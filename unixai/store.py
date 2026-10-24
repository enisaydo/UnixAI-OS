"""data/ dizinindeki JSON dosyaları için küçük yardımcılar.

- Yazma atomiktir (geçici dosya + rename), dosyalar 0600 izniyle oluşturulur
- Gün ve saat USAGE_TIMEZONE'a göre hesaplanır (varsayılan Europe/Istanbul)
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from .env import ROOT

DATA_DIR = Path(os.environ.get("UNIXAI_DATA_DIR") or ROOT / "data")
TZ_NAME = os.environ.get("USAGE_TIMEZONE", "Europe/Istanbul")
_lock = threading.RLock()

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo(TZ_NAME)
except Exception:  # tzdata yoksa (ör. Windows) yerel saat dilimi
    TZ = None


def file(*parts) -> Path:
    return DATA_DIR.joinpath(*parts)


def read_json(rel, fallback=None):
    try:
        return json.loads(file(rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def write_json(rel, value):
    target = file(rel)
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    return value


def append_line(path: Path, obj) -> None:
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def now_local(d: datetime | None = None) -> datetime:
    d = d or datetime.now(timezone.utc)
    return d.astimezone(TZ) if TZ else d.astimezone()


def day_of(d: datetime | None = None) -> str:
    """YYYY-MM-DD (USAGE_TIMEZONE'da)"""
    return now_local(d).strftime("%Y-%m-%d")


def time_of(d: datetime | None = None) -> str:
    """HH:MM (USAGE_TIMEZONE'da)"""
    return now_local(d).strftime("%H:%M")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
