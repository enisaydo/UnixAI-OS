"""Sunucu envanteri (Sunucular ekranından yönetilir, data/servers.json)

  { name, host, port?, user?, keyFile?, tags[], enabled, notes }
  port / user / keyFile boşsa Ayarlar → SSH'taki varsayılanlar kullanılır.

Değerler ssh komutuna argüman olarak gider (kabuk kullanılmaz); yine de '-' ile başlayan
(ssh seçeneği gibi yorumlanabilecek) adlar ve kontrol karakterleri reddedilir.
"""
from __future__ import annotations

import re
import threading

from .errors import ApiError
from .store import iso_now, read_json, write_json

FILE = "servers.json"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:%-]{0,252}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")
KEY_RE = re.compile(r"^/[^\0\n\r]+$")

_cache = None
_lock = threading.RLock()


def list_servers() -> list:
    global _cache
    if _cache is None:
        _cache = read_json(FILE, []) or []
    return _cache


def get(name: str):
    return next((s for s in list_servers() if s["name"] == name), None)


def validate(s: dict) -> dict:
    tags = s.get("tags")
    tags = tags if isinstance(tags, list) else str(tags or "").split(",")
    port = s.get("port")
    out = {
        "name": str(s.get("name") or "").strip(),
        "host": str(s.get("host") or "").strip(),
        "port": None if port in ("", None) else port,
        "user": str(s.get("user") or "").strip() or None,
        "keyFile": str(s.get("keyFile") or "").strip() or None,
        "tags": [t for t in (str(x).strip() for x in tags) if t][:20],
        "enabled": s.get("enabled") is not False,
        "notes": str(s.get("notes") or "")[:500],
    }
    if not NAME_RE.match(out["name"]):
        raise ApiError(f"Geçersiz sunucu adı: '{out['name']}' (harf, rakam, . _ - ; en fazla 64 karakter)")
    if not HOST_RE.match(out["host"]):
        raise ApiError(f"Geçersiz adres: '{out['host']}' (ad veya IP)")
    if out["port"] is not None:
        try:
            out["port"] = int(out["port"])
        except (TypeError, ValueError):
            out["port"] = 0
        if not 0 < out["port"] < 65536:
            raise ApiError("Port 1-65535 arasında olmalı")
    if out["user"] and not USER_RE.match(out["user"]):
        raise ApiError(f"Geçersiz kullanıcı adı: '{out['user']}'")
    if out["keyFile"] and not KEY_RE.match(out["keyFile"]):
        raise ApiError("Anahtar dosyası mutlak yol olmalı")
    return out


def _persist(nxt: list) -> None:
    global _cache
    write_json(FILE, nxt)
    _cache = nxt


def upsert(data: dict, original=None) -> dict:
    """Ekle / güncelle. original: düzenlenen kaydın eski adı (yeniden adlandırma için)"""
    s = validate(data)
    with _lock:
        cur = list_servers()
        key = original or s["name"]
        if s["name"] != key and any(x["name"] == s["name"] for x in cur):
            raise ApiError(f"'{s['name']}' zaten var", 409)
        idx = next((i for i, x in enumerate(cur) if x["name"] == key), -1)
        row = {**(cur[idx] if idx >= 0 else {"createdAt": iso_now()}), **s, "updatedAt": iso_now()}
        _persist([row if i == idx else x for i, x in enumerate(cur)] if idx >= 0 else cur + [row])
        return row


def remove(name: str) -> None:
    with _lock:
        cur = list_servers()
        if not any(x["name"] == name for x in cur):
            raise ApiError("Sunucu bulunamadı", 404)
        _persist([x for x in cur if x["name"] != name])


def import_text(text: str) -> dict:
    """Her satır "ad adres [kullanıcı] [port]" veya yalnızca "adres"; '#' ile başlayanlar yok sayılır."""
    added, errors = [], []
    for i, raw in enumerate(str(text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        p = re.split(r"[\s,;]+", line)
        name, host, user, port = ([p[0].split(".")[0], p[0]] if len(p) == 1 else p + [None, None])[:4]
        try:
            added.append(upsert({"name": name, "host": host, "user": user, "port": port, "enabled": True}, name if get(name) else None)["name"])
        except ApiError as e:
            errors.append(f"Satır {i}: {e}")
    return {"added": added, "errors": errors}
