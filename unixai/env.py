"""Bağımlılıksız .env yükleyici.

Zaten tanımlı ortam değişkenlerini ezmez (systemd / Podman değerleri önceliklidir).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _unquote(v: str) -> str:
    return v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'" else v


def load_env(file: Path | None = None) -> None:
    # Podman --env-file tırnakları değerin parçası sayar: X="a b" → a b
    for k, v in list(os.environ.items()):
        os.environ[k] = _unquote(v)
    file = file or ROOT / ".env"
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = re.sub(r"^export\s+", "", key.strip())
        os.environ.setdefault(key, _unquote(val.strip()))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def headers_from_env(prefix: str) -> dict[str, str]:
    """CHAT_H_CLIENT_ID=abc → client-id: abc"""
    return {k[len(prefix):].lower().replace("_", "-"): v for k, v in os.environ.items() if k.startswith(prefix) and v != ""}


def json_env(name: str, fallback):
    v = os.environ.get(name)
    if not v:
        return fallback
    try:
        return json.loads(v)
    except ValueError as e:
        raise ValueError(f"{name} geçerli JSON değil: {e}") from e


def bool_env(name: str) -> bool:
    return bool(re.match(r"^(1|true|yes|on)$", os.environ.get(name, ""), re.I))
