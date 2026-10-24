"""Ajanlar arası konuşma kaydı (UnixAI ajanı ⇄ MorpheusAI ajanı ve diğer bağlı ajanlar)

Kullanıcı sohbetlerinden ayrı tutulur. Her kayıt bir soru-cevap alışverişidir:
  direction : "giden" (UnixAI ajanı karşı ajana sordu) | "gelen" (karşı ajan UnixAI ajanına sordu)
  peer      : karşı ajan (bağlantı adı / istemci adı, ör. morpheusai)
  server    : ilgili sunucu (açıkça verilmişse, sohbet bağlamından ya da sorudaki sunucu adından)
  remediations : karşı ajanın ürettiği playbook'lar (YAML, uyum skoru, durum)
data/a2a/YYYY-MM-DD.jsonl
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date, timedelta

from .store import append_line, day_of, file, iso_now

_lock = threading.Lock()
_cache: dict = {}


def known_servers() -> list:
    from . import inventory, runs
    return sorted({s["name"] for s in inventory.list_servers()} | set(runs.latest_by_server()), key=len, reverse=True)


def detect_server(*texts, explicit=None) -> str | None:
    if explicit:
        return str(explicit)
    blob = " ".join(str(t or "") for t in texts)
    for name in known_servers():
        if re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", blob):
            return name
    return None


def record(direction, peer, question, answer=None, server=None, conversation_id=None, remote_conversation_id=None,
           remediations=None, tools=None, ok=True, error=None, duration_ms=None, user=None) -> dict:
    row = {"id": uuid.uuid4().hex[:16], "time": iso_now(), "date": day_of(), "direction": direction, "peer": peer, "user": user,
           "server": server, "question": str(question or "")[:8000], "answer": str(answer or "")[:20000],
           "conversationId": conversation_id, "remoteConversationId": remote_conversation_id,
           "remediations": remediations or [], "tools": tools or [], "ok": ok, "error": (error or None) and str(error)[:500], "durationMs": duration_ms}
    append_line(file("a2a", f"{row['date']}.jsonl"), row)
    with _lock:
        _cache.pop(row["date"], None)
    print(f"[ajanlar] {'UnixAI → ' + peer if direction == 'giden' else peer + ' → UnixAI'}{f' ({server})' if server else ''}: "
          f"{row['question'][:120]!r} → {'✓' if ok else '✕ ' + str(error)[:120]}"
          + (f" · {len(row['remediations'])} playbook" if row["remediations"] else ""), flush=True)
    return row


def _day(d: str) -> list:
    with _lock:
        if d in _cache:
            return _cache[d]
    rows = []
    try:
        for line in file("a2a", f"{d}.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    with _lock:
        if d != day_of():
            _cache[d] = rows
    return rows


def _days(n=90):
    t = date.fromisoformat(day_of())
    return [(t - timedelta(days=i)).isoformat() for i in range(n)]


def summary(r: dict) -> dict:
    return {**{k: r.get(k) for k in ("id", "time", "direction", "peer", "user", "server", "ok", "error", "durationMs", "conversationId")},
            "question": r["question"][:300], "answer": r["answer"][:300],
            "remediations": [{k: x.get(k) for k in ("id", "title", "status", "score")} for x in r.get("remediations") or []]}


def query(server=None, peer=None, limit=200) -> dict:
    rows = []
    for d in _days():
        for r in reversed(_day(d)):
            if (not server or r.get("server") == server) and (not peer or r.get("peer") == peer):
                rows.append(r)
                if len(rows) >= limit:
                    break
        if len(rows) >= limit:
            break
    peers = sorted({r["peer"] for d in _days(30) for r in _day(d)})
    return {"items": [summary(r) for r in rows], "peers": peers}


def get(rid: str):
    if not re.match(r"^[0-9a-f]{16}$", rid or ""):
        return None
    for d in _days():
        for r in _day(d):
            if r["id"] == rid:
                return r
    return None
