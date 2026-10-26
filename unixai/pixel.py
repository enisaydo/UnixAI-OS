"""Pixel Office (yerleşik) — bu makinedeki açık kaynak Pixel Agents ofisine doğrudan olay gönderir (ayrı köprü gerekmez)

  .env:
    PIXEL_OFFICE=true            açar (varsayılan kapalı)
    PIXEL_URL=, PIXEL_TOKEN=     boşsa aynı makinedeki ~/.pixel-agents/servers/*.json'dan bulunur (Pixel Agents aynı kullanıcıyla, ör. root, çalışmalı)
    PIXEL_CWD=/pixel-office      karakterlerin proje klasörü öneki

  UnixAI'nin canlı akışındaki her olay (ajan adımı, MorpheusAI'ye soru, toplama, MorpheusAI'den aktarılan düzeltme akışı)
  Pixel Agents'ın hook biçimine çevrilip POST /api/hooks/claude ile gönderilir. Ofiste iki karakter görünür:
  UnixAI ve MorpheusAI. Gönderim arka planda sıralı yapılır; Pixel Agents kapalıysa uygulama etkilenmez.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import urllib.request
from pathlib import Path

from .env import env

SELF = "unixai"
_q: queue.Queue = queue.Queue(maxsize=2000)
_started: set = set()
_busy = {"unixai": 0, "morpheus": 0}
_idle: dict = {}
_stats = {"sent": 0, "failed": 0, "lastError": None, "target": None}
_worker = None
_lock = threading.Lock()
READ_RE = re.compile(r"get_|list_|status|findings|diagnostic|search|query|standards|records|tasks")


def enabled() -> bool:
    return (env("PIXEL_OFFICE") or "").lower() in ("1", "true", "yes", "on")


def _agents():
    cwd = (env("PIXEL_CWD") or "/pixel-office").rstrip("/")
    return {"unixai": {"session": "unixai-os-ajani", "cwd": f"{cwd}/UnixAI"}, "morpheus": {"session": "morpheusai-ajani", "cwd": f"{cwd}/MorpheusAI"}}


def _targets() -> list:
    if env("PIXEL_URL"):
        return [(env("PIXEL_URL").rstrip("/"), env("PIXEL_TOKEN"))]
    base = Path.home() / ".pixel-agents"
    files = sorted((base / "servers").glob("*.json")) if (base / "servers").is_dir() else []
    out = []
    for f in files or [base / "server.json"]:
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
            if c.get("port") and c.get("token"):
                out.append((f"http://127.0.0.1:{c['port']}", c["token"]))
        except (OSError, ValueError):
            pass
    return out


def _post(url, token, body) -> int:
    req = urllib.request.Request(url + "/api/hooks/claude", data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
                                 headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def _loop():
    while True:
        kind, body = _q.get()
        a = _agents()[kind]
        for url, token in _targets():
            key = f"{url}|{a['session']}"
            _stats["target"] = url
            try:
                if key not in _started:
                    _post(url, token, {"session_id": a["session"], "hook_event_name": "SessionStart", "source": "startup", "cwd": a["cwd"]})
                    _started.add(key)
                _post(url, token, {"session_id": a["session"], **body})
                _stats["sent"] += 1
            except Exception as e:  # Pixel Agents kapalı / token değişti: bir dahaki olayda yeniden dene
                _started.discard(key)
                _stats.update(failed=_stats["failed"] + 1, lastError=str(e)[:200])


def _hook(kind, body):
    global _worker
    with _lock:
        if not (_worker and _worker.is_alive()):
            _worker = threading.Thread(target=_loop, name="pixel-office", daemon=True)
            _worker.start()
    try:
        _q.put_nowait((kind, body))
    except queue.Full:
        pass


def _done(kind, sec=4.0):
    t = _idle.get(kind)
    if t:
        t.cancel()
    t = threading.Timer(sec, lambda: _busy[kind] or _hook(kind, {"hook_event_name": "Stop"}))
    t.daemon = True
    _idle[kind] = t
    t.start()


def _start(kind, tool, inp):
    t = _idle.get(kind)
    if t:
        t.cancel()
    _busy[kind] += 1
    _hook(kind, {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp})


def _end(kind, failed=False):
    _busy[kind] = max(0, _busy[kind] - 1)
    _hook(kind, {"hook_event_name": "PostToolUseFailure" if failed else "PostToolUse"})
    _done(kind)


def _sub(kind, desc):
    _start(kind, "Agent", {"description": str(desc)[:120], "run_in_background": True})


def _blip(kind, tool, inp, failed=False):
    _start(kind, tool, inp)
    _end(kind, failed)


def on_event(ev: dict) -> None:
    """live.publish'ten çağrılır: UnixAI olayını ofis hareketine çevirir"""
    if not enabled() or ev.get("replay"):
        return
    peer = "morpheus"
    t = ev.get("type")
    if t == "step":
        tool = re.sub(r"^.*__", "", str(ev.get("tool") or ""))
        if ev.get("phase") == "start":
            if tool.endswith("agent_ask"):
                return _sub(SELF, "MorpheusAI'ye soruyor")
            if READ_RE.search(tool):
                return _start(SELF, "Read", {"file_path": f"/{tool}"})
            return _start(SELF, "Bash", {"command": tool})
        return _end(SELF, ev.get("ok") is False)
    if t == "a2a":
        callee = SELF if ev.get("dir") == "gelen" else peer
        asker = peer if callee == SELF else SELF
        if ev.get("phase") == "start":
            if callee == SELF:  # MorpheusAI bize soruyor: onun karakteri de soruyor görünsün
                _sub(peer, "UnixAI'ye soruyor")
            return _sub(callee, f"{'UnixAI' if asker == SELF else 'MorpheusAI'}'den soru: {ev.get('question') or ''}")
        _busy[callee] = max(0, _busy[callee] - 1)
        _hook(callee, {"hook_event_name": "PostToolUseFailure" if ev.get("ok") is False else "PostToolUse"})
        if callee == SELF:
            _end(peer)
        if ev.get("remediations"):
            return _hook("morpheus", {"hook_event_name": "PermissionRequest"})
        return _done(callee, 1.5)
    if t == "rem":
        s, st = ev.get("server") or "sunucu", ev.get("stage")
        if st == "received":
            return _blip(peer, "Read", {"file_path": f"/bulgu-{s}"})
        if st == "generated":
            _blip(peer, "Write", {"file_path": f"/playbook-{s}.yml"})
            return _hook(peer, {"hook_event_name": "PermissionRequest"})
        cmd = {"approved": f"onaylandı · {ev.get('by') or ''}", "commit": f"git commit · Bitbucket · {s}", "sync": "AAP proje sync",
               "template": f"AAP job template · {s}", "launch": f"AAP {'uygula' if ev.get('mode') == 'run' else 'check'} · {s}"}.get(st)
        if cmd:
            return _blip(peer, "Bash", {"command": cmd})
        if st in ("job", "rejected", "failed"):
            return _blip(peer, "Bash", {"command": str(ev.get("text") or st)[:80]}, failed=st != "job" or "successful" not in str(ev.get("text")))
        return None
    if t == "run":
        if ev.get("phase") == "start":
            return _sub(SELF, f"Toplama · {len(ev.get('servers') or [])} sunucu")
        if ev.get("phase") == "server":
            return _blip(SELF, "Read", {"file_path": f"/{ev.get('server')}"}, failed=ev.get("ok") is False)
        _busy[SELF] = max(0, _busy[SELF] - 1)
        _hook(SELF, {"hook_event_name": "PostToolUse"})
        return _done(SELF, 1.5)
    return None


def hello() -> None:
    """Servis açılınca iki karakteri ofise al"""
    if enabled():
        for k in ("unixai", "morpheus"):
            _hook(k, {"hook_event_name": "Stop"})


def status() -> dict:
    return {"enabled": enabled(), "targets": [u for u, _ in _targets()] if enabled() else [], "queue": _q.qsize(), **_stats}
