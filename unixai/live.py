"""Canlı akış: ajanlar arası trafik anlık olarak arayüze iletilir (Server-Sent Events, GET /api/live)

Olay türleri (her olayda id, type, time):
  mcp    MCP çağrısı           dir=gelen|giden, peer, method, tool, args (kısa), phase=start|end, ok, durationMs, error, rid
  http   uygulamalar arası REST dir=gelen|giden, peer, method, path, status, ok, durationMs
  a2a    ajana soru            dir=gelen|giden, peer, question, server, phase=start|end, answer (kısa), remediations, ok, rid
  step   ajanın araç adımı     conversation, tool, where, phase=start|end, ok, durationMs, hop, origin, rid

Bellekte son 300 olay tutulur; yeni bağlanan istemci son 40 olayı (veya Last-Event-ID'den sonrasını) alır.
Olaylar logs/canli/ dosyasına da yazılır (GET durum sorguları hariç); servis yeniden başlayınca son olaylar oradan yüklenir.
Parola/token gönderilmez; argümanlar logger maskesiyle kısaltılır.
"""
from __future__ import annotations

import json
import threading
import uuid
from collections import deque

from .store import iso_now

MAX_SUBSCRIBERS = 50
_cond = threading.Condition()
_buf: deque = deque(maxlen=300)
_seq = 0
_subs = 0


def rid() -> str:
    return uuid.uuid4().hex[:12]


def short(v, n=220) -> str | None:
    if v is None:
        return None
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def args_preview(args) -> str | None:
    if not isinstance(args, dict) or not args:
        return None
    from . import logger  # maskeleme (token/parola alanları)

    clean = {k: v for k, v in args.items() if k != "context"}
    return short(logger.mask_body(json.dumps(clean, ensure_ascii=False, default=str)), 180)


def publish(type_: str, **data) -> dict:
    global _seq
    with _cond:
        _seq += 1
        ev = {"id": _seq, "type": type_, "time": iso_now(), **{k: v for k, v in data.items() if v is not None and k not in ("id", "type")}}
        _buf.append(ev)
        _cond.notify_all()
    if not (type_ == "http" and data.get("method") == "GET") and not data.get("replay"):
        from . import oplog

        oplog.write_entry("canli", {**ev, "category": "canli"})
    try:
        from . import pixel  # yerel Pixel Agents ofisi (PIXEL_OFFICE=true ise)

        pixel.on_event(ev)
    except Exception as e:  # ofis hatası canlı akışı bozmasın
        print(f"[pixel] {e}", flush=True)
    return ev


def _preload() -> None:
    """Servis yeniden başladığında son canlı olayları (bugün + dün) belleğe al"""
    global _seq
    try:
        from datetime import date, timedelta

        from . import oplog

        rows = []
        for d in ((date.today() - timedelta(days=1)).isoformat(), date.today().isoformat()):
            try:
                rows += [json.loads(l) for l in oplog.file_of("canli", d).read_text(encoding="utf-8").splitlines()[-300:] if l.strip()]
            except (OSError, ValueError):
                pass
        for r in rows[-300:]:
            _seq += 1
            r.pop("category", None)
            _buf.append({**r, "id": _seq})
    except Exception as e:  # geçmiş yüklenemezse canlı akış yine çalışır
        print(f"[canlı] geçmiş yüklenemedi: {e}", flush=True)


_preload()


def stream(handler, last_id: int | None) -> None:
    """SSE bağlantısını açık tutar; bağlantı kapanınca döner (her bağlantı bir iş parçacığı)"""
    global _subs
    with _cond:
        if _subs >= MAX_SUBSCRIBERS:
            handler.send_json(503, {"error": "Çok fazla canlı izleyici"})
            return
        _subs += 1
    try:
        handler.send_response(200)
        for k, v in {"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-transform",
                     "Connection": "keep-alive", "X-Accel-Buffering": "no"}.items():
            handler.send_header(k, v)
        handler.end_headers()
        handler.close_connection = True
        w = handler.wfile
        w.write(b"retry: 3000\n\n")
        with _cond:
            backlog = [e for e in _buf if e["id"] > last_id] if last_id else list(_buf)[-40:]
            sent = _seq
        for ev in backlog:  # geçmiş olaylar: arayüz yalnızca listeler, karakterler canlandırmaz
            w.write(_frame({**ev, "replay": True}))
        w.flush()
        while True:
            with _cond:
                _cond.wait_for(lambda: _seq > sent, timeout=15)
                new = [e for e in _buf if e["id"] > sent]
                sent = _seq
            if new:
                for ev in new:
                    w.write(_frame(ev))
            else:
                w.write(b": ka\n\n")  # bağlantıyı canlı tut (proxy zaman aşımı)
            w.flush()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        pass
    finally:
        with _cond:
            _subs -= 1


def _frame(ev: dict) -> bytes:
    return f"id: {ev['id']}\nevent: live\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


def recent(limit: int = 100) -> list:
    with _cond:
        return list(_buf)[-limit:]
