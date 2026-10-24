"""Dış servis çağrı logları (GenAI)

- Son AI_LOG_MEMORY (varsayılan 200) kayıt bellekte tutulur → /api/logs ile arayüzde görünür
- Her kayıt logs/ai-YYYY-MM-DD.jsonl dosyasına bir satır olarak yazılır
- Konsola (journalctl) tek satırlık özet basılır
- Gizli alanlar (authorization, cookie, client_secret, password, token...) maskelenir
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from .env import ROOT
from .store import append_line, iso_now

LOG_DIR = Path(os.environ.get("AI_LOG_DIR") or ROOT / "logs")
MAX_MEMORY = int(os.environ.get("AI_LOG_MEMORY", "200"))
MAX_BODY = int(os.environ.get("AI_LOG_MAX_BODY", "200000"))
TO_FILE = not re.match(r"^(0|false|no|off)$", os.environ.get("AI_LOG_FILE", "true"), re.I)

# Not: "max_completion_tokens", "total_tokens" gibi sayaçlar maskelenmez
_SECRET = re.compile(r"^(authorization|proxy-authorization|cookie|set-cookie|password|passwd|token|access_token|refresh_token|id_token|"
                     r"x-api-key|api[-_]?key|client[-_]?id)$|secret", re.I)

_entries: deque = deque(maxlen=MAX_MEMORY)
_lock = threading.Lock()


def mask_value(v, key: str = "") -> str:
    """Parola/secret hiç gösterilmez; token ve id'lerin yalnızca ilk 4 karakteri (eşleştirme için) gösterilir."""
    s = "" if v is None else str(v)
    if not s:
        return s
    if re.search(r"password|passwd|secret", key, re.I):
        return f"****({len(s)})"
    m = re.match(r"^(Bearer|Basic)\s+(.*)$", s, re.I)
    if m:
        return f"{m.group(1)} {m.group(2)[:4]}…({len(m.group(2))})"
    return "****" if len(s) <= 8 else f"{s[:4]}…({len(s)})"


def mask_headers(h) -> dict:
    return {k: (mask_value(v, k) if _SECRET.search(k) else v) for k, v in (h or {}).items()}


def _walk(o):
    if isinstance(o, list):
        return [_walk(x) for x in o]
    if isinstance(o, dict):
        return {k: (mask_value(v, k) if _SECRET.search(k) and not isinstance(v, (dict, list)) else _walk(v)) for k, v in o.items()}
    return o


def _truncate(s):
    return s[:MAX_BODY] + f"…[{len(s) - MAX_BODY} karakter kesildi]" if isinstance(s, str) and len(s) > MAX_BODY else s


def mask_body(body):
    if body is None or body == "":
        return body
    obj = body
    if isinstance(body, str):
        try:
            obj = json.loads(body)
        except ValueError:
            return _truncate(body)
    return _walk(obj)


def record(service, kind, url, method="GET", request_headers=None, request_body=None, status=None, response_headers=None,
           response_body=None, duration_ms=0, error=None, meta=None) -> dict:
    u = urlsplit(url)
    entry = {
        "id": str(uuid.uuid4()),
        "time": iso_now(),
        "service": service,
        "kind": kind,
        "method": method,
        "host": u.netloc or None,
        "path": (u.path + ("?" + u.query if u.query else "")) if u.netloc else url,
        "status": status,
        "ok": not error and status is not None and status < 400,
        "durationMs": duration_ms,
        "request": {"headers": mask_headers(request_headers), "body": mask_body(request_body)},
        "response": {"headers": mask_headers(response_headers), "body": mask_body(response_body)},
        "error": error,
        "meta": meta,
    }
    with _lock:
        _entries.append(entry)
    if TO_FILE:
        try:
            append_line(LOG_DIR / f"ai-{entry['time'][:10]}.jsonl", entry)
        except OSError:
            pass
    usage = (meta or {}).get("usage") or {}
    print(f"[{service}] {kind} {method} {entry['host']}{entry['path']} → {status if status is not None else 'ERR'} {duration_ms}ms"
          + (f" tokens={usage.get('total_tokens', '?')}" if usage else "") + (f" HATA: {error}" if error else ""), flush=True)
    return entry


def list_entries(limit: int = 50, service=None) -> list:
    with _lock:
        rows = [e for e in _entries if not service or e["service"] == service]
    return [{k: v for k, v in e.items() if k not in ("request", "response")} for e in reversed(rows[-limit:])]


def get(entry_id: str):
    with _lock:
        return next((e for e in _entries if e["id"] == entry_id), None)
