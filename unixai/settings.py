"""Uygulama ayarları (Ayarlar ekranından yönetilir, data/settings.json)

  ssh        : varsayılan SSH kullanıcısı (root), anahtar dosyası, host key politikası, zaman aşımları
  schedule   : günlük otomatik toplama saati
  analysis   : AI analizi, filo özeti, IP maskeleme, saklama süresi
  thresholds : kural motoru eşikleri
"""
from __future__ import annotations

import copy
import os
import re

from . import rules
from .errors import ApiError
from .store import iso_now, read_json, write_json

FILE = "settings.json"

DEFAULTS = {
    "ssh": {
        "user": os.environ.get("SSH_USER", "root"),
        "keyFile": os.environ.get("SSH_KEY_FILE", "/root/.ssh/id_rsa"),
        "port": 22,
        "hostKeyPolicy": "yes",  # yes: known_hosts'ta olmalı · accept-new: ilk bağlantıda kaydet
        "connectTimeout": 15,
        "commandTimeout": 180,
        "concurrency": 10,
    },
    "schedule": {"enabled": True, "time": "06:30", "lastRunDate": None},
    "analysis": {"ai": True, "fleetSummary": True, "redactIps": False, "retentionDays": 90},
    "thresholds": dict(rules.DEFAULTS),
}

_cache = None


def get() -> dict:
    global _cache
    if _cache is None:
        s = read_json(FILE, {}) or {}
        _cache = {k: {**DEFAULTS[k], **(s.get(k) or {})} for k in ("ssh", "schedule", "analysis", "thresholds")}
        _cache["updatedAt"], _cache["updatedBy"] = s.get("updatedAt"), s.get("updatedBy")
    return _cache


def _num(v, name, lo, hi):
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = None
    if n is None or not lo <= n <= hi:
        raise ApiError(f"{name} {lo} - {hi} arasında olmalı")
    return int(n) if n.is_integer() else n


def save(data: dict, user=None) -> dict:
    global _cache
    cur = copy.deepcopy(get())
    ssh = {**cur["ssh"], **(data.get("ssh") or {})}
    schedule = {**cur["schedule"], **(data.get("schedule") or {}), "lastRunDate": cur["schedule"]["lastRunDate"]}
    analysis = {**cur["analysis"], **(data.get("analysis") or {})}
    thresholds = {**cur["thresholds"], **(data.get("thresholds") or {})}

    if not re.match(r"^[a-z_][a-z0-9_.-]{0,31}$", str(ssh["user"])):
        raise ApiError("Geçersiz SSH kullanıcı adı")
    if not re.match(r"^/[^\0\n\r]+$", str(ssh["keyFile"])):
        raise ApiError("SSH anahtar dosyası mutlak yol olmalı (ör. /root/.ssh/id_rsa)")
    if ssh["hostKeyPolicy"] not in ("yes", "accept-new"):
        raise ApiError("Geçersiz host key politikası")
    ssh["port"] = int(_num(ssh["port"], "SSH portu", 1, 65535))
    ssh["connectTimeout"] = int(_num(ssh["connectTimeout"], "Bağlantı zaman aşımı", 3, 120))
    ssh["commandTimeout"] = int(_num(ssh["commandTimeout"], "Komut zaman aşımı", 30, 1800))
    ssh["concurrency"] = int(_num(ssh["concurrency"], "Eş zamanlı bağlantı", 1, 100))
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(schedule["time"])):
        raise ApiError("Saat SS:DD biçiminde olmalı (ör. 06:30)")
    schedule["enabled"] = bool(schedule["enabled"])
    for k in ("ai", "fleetSummary", "redactIps"):
        analysis[k] = bool(analysis[k])
    analysis["retentionDays"] = int(_num(analysis["retentionDays"], "Saklama süresi (gün)", 7, 3650))
    for k in DEFAULTS["thresholds"]:
        thresholds[k] = _num(thresholds[k], f"Eşik {k}", 0, 100000)

    write_json(FILE, {"ssh": ssh, "schedule": schedule, "analysis": analysis, "thresholds": thresholds,
                      "updatedAt": iso_now(), "updatedBy": user})
    _cache = None
    return get()


def mark_scheduled(day: str) -> None:
    """Zamanlayıcının son çalıştığı günü kaydeder (aynı gün iki kez çalışmasın)"""
    global _cache
    s = copy.deepcopy(get())
    s["schedule"]["lastRunDate"] = day
    write_json(FILE, s)
    _cache = None
