"""İşlem logları — sistemin yaptığı her iş, kategorisine göre AYRI dosyaya yazılır

  logs/<kategori>/<kategori>-YYYY-MM-DD.jsonl     (bir satır = bir olay, JSON)
  + journalctl için tek satırlık özet

  islem          arayüz/API'den yapılan her değişiklik (kim, ne, sonuç)
  giris          giriş denemeleri (LDAP / yerel yönetici)
  ssh            hedef sunuculara her SSH bağlantısı (sunucu, kullanıcı, amaç, çıkış kodu, süre)
  toplama        toplama/analiz çalıştırmaları (başladı, sunucu sonucu, bitti)
  tani           canlı salt-okunur tanı komutları
  genai          TMS token + GPT çağrıları
  morpheus       MorpheusAI köprüsü (bulgu gönderimi, onay aktarımı, durum)
  mcp-istemci    bağlı MCP sunucularına giden çağrılar
  mcp-sunucu     /mcp'ye gelen çağrılar
  ajan           ajanın her araç adımı
  ajanlar-arasi  UnixAI ⇄ MorpheusAI ajan konuşmaları

.env: LOG_DIR (varsayılan ./logs; AI_LOG_DIR da kabul edilir), LOG_RETENTION_DAYS (90), LOG_FILE=false (dosyaya yazma)
Parola/token değerleri yazılmaz (HTTP çağrılarında logger maskeler).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from .env import ROOT
from .store import append_line, iso_now

LOG_DIR = Path(os.environ.get("LOG_DIR") or os.environ.get("AI_LOG_DIR") or ROOT / "logs")
TO_FILE = not re.match(r"^(0|false|no|off)$", os.environ.get("LOG_FILE") or os.environ.get("AI_LOG_FILE") or "true", re.I)
RETENTION = int(os.environ.get("LOG_RETENTION_DAYS", "90") or 0)

CATEGORIES = {
    "islem": "Kullanıcı / API işlemleri",
    "giris": "Giriş denemeleri",
    "ssh": "SSH bağlantıları",
    "toplama": "Toplama ve analiz çalıştırmaları",
    "tani": "Canlı tanı komutları",
    "genai": "GenAI (TMS + GPT) çağrıları",
    "morpheus": "MorpheusAI köprüsü",
    "mcp-istemci": "Giden MCP çağrıları",
    "mcp-sunucu": "Gelen MCP çağrıları (/mcp)",
    "ajan": "Ajan adımları",
    "ajanlar-arasi": "Ajanlar arası konuşmalar",
}
SERVICE_CATEGORY = {"mcp": "mcp-istemci"}
_lock = threading.Lock()
_last_cleanup = 0.0


def _safe(cat: str) -> str:
    return cat if cat in CATEGORIES or re.match(r"^[a-z][a-z0-9-]{1,30}$", cat or "") else "diger"


def file_of(cat: str, d: str) -> Path:
    return LOG_DIR / cat / f"{cat}-{d}.jsonl"


def _append(cat: str, row: dict) -> None:
    if not TO_FILE:
        return
    try:
        with _lock:
            append_line(file_of(cat, row["time"][:10]), row)
    except OSError as e:
        print(f"[log] {cat} yazılamadı: {e}", flush=True)
    _maybe_cleanup()


def write_entry(service: str, entry: dict) -> None:
    """Hazır bir kaydı (ör. HTTP çağrısı) kategorisinin dosyasına yazar"""
    _append(_safe(SERVICE_CATEGORY.get(service, service)), entry)


def log(cat: str, action: str, user=None, ok=None, error=None, quiet=False, **detail) -> dict:
    cat = _safe(cat)
    row = {"time": iso_now(), "category": cat, "action": str(action)[:300], "user": user, "ok": (not error) if ok is None else bool(ok),
           "error": (str(error)[:1000] if error else None), **{k: v for k, v in detail.items() if v is not None}}
    _append(cat, row)
    if not quiet:
        brief = " ".join(f"{k}={str(v)[:80]}" for k, v in list(detail.items())[:5] if v is not None and not isinstance(v, (dict, list)))
        print(f"[{cat}] {row['action']}{' · ' + str(user) if user else ''}{' · ' + brief if brief else ''}{' · HATA: ' + row['error'] if row['error'] else ''}", flush=True)
    return row


def files() -> list:
    try:
        dirs = [p.name for p in LOG_DIR.iterdir() if p.is_dir()]
    except OSError:
        dirs = []
    out = []
    for c in list(CATEGORIES) + [d for d in sorted(dirs) if d not in CATEGORIES]:
        fl = []
        try:
            for p in (LOG_DIR / c).glob(f"{c}-*.jsonl"):
                fl.append({"date": p.name[len(c) + 1:-6], "size": p.stat().st_size})
        except OSError:
            pass
        out.append({"category": c, "label": CATEGORIES.get(c, c), "dir": str(LOG_DIR / c), "files": sorted(fl, key=lambda x: x["date"], reverse=True)})
    return out


def read(cat: str, d: str | None = None, q: str | None = None, failed: bool = False, limit: int = 500) -> dict:
    from .errors import ApiError

    cat = _safe(cat)
    d = d or iso_now()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        raise ApiError("Geçersiz tarih", 400)
    try:
        lines = [l for l in file_of(cat, d).read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        lines = []
    needle, out = (q or "").lower(), []
    for line in reversed(lines):
        if len(out) >= min(int(limit or 500), 2000):
            break
        if needle and needle not in line.lower():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if failed and row.get("ok") is not False:
            continue
        out.append(row)
    return {"category": cat, "date": d, "total": len(lines), "file": str(file_of(cat, d)), "items": out}


def _maybe_cleanup() -> None:
    global _last_cleanup
    if RETENTION <= 0 or time.time() - _last_cleanup < 12 * 3600:
        return
    _last_cleanup = time.time()
    limit = (date.today() - timedelta(days=RETENTION)).isoformat()
    for c in files():
        for f in c["files"]:
            if f["date"] < limit:
                try:
                    file_of(c["category"], f["date"]).unlink()
                except OSError:
                    pass
