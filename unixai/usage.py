"""Token kullanımı ve günlük limitler

- Her AI çağrısı data/usage/YYYY-MM-DD.jsonl dosyasına bir satır olarak yazılır (kim, ne zaman, tür, soru, kaç token)
- Limitler data/limits.json'da saklanır, Ayarlar ekranından değiştirilir
    dailyLimit         : tüm kullanıcılar için günlük toplam token (0 = sınırsız)
    perUserDailyLimit  : kullanıcı başına günlük token (0 = sınırsız)
"""
from __future__ import annotations

import json
import threading
from datetime import date, timedelta

from .errors import ApiError
from .store import TZ_NAME, append_line, day_of, file, iso_now, read_json, write_json

LIMIT_DEFAULTS = {"dailyLimit": 0, "perUserDailyLimit": 0}
_lock = threading.Lock()
_day_cache: dict = {}
_limits = None


class LimitError(ApiError):
    def __init__(self, message: str):
        super().__init__(message, 429)


def get_limits() -> dict:
    global _limits
    if _limits is None:
        _limits = {**LIMIT_DEFAULTS, **(read_json("limits.json", {}) or {})}
    return _limits


def save_limits(data: dict, user=None) -> dict:
    global _limits

    def num(v, name):
        try:
            f = float(0 if v in (None, "") else v)
        except (TypeError, ValueError):
            f = -1.0
        if isinstance(v, bool) or f < 0 or not f.is_integer():
            raise ValueError(f"{name} 0 veya pozitif bir tam sayı olmalı")
        return int(f)

    nxt = {"dailyLimit": num(data.get("dailyLimit"), "Günlük limit"),
           "perUserDailyLimit": num(data.get("perUserDailyLimit"), "Kullanıcı başına limit"),
           "updatedAt": iso_now(), "updatedBy": user}
    write_json("limits.json", nxt)
    _limits = nxt
    return nxt


def _read_day(d: str) -> list:
    with _lock:
        if d in _day_cache:
            return _day_cache[d]
        rows = []
        try:
            for line in file("usage", f"{d}.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        except OSError:
            pass
        _day_cache[d] = rows
        if len(_day_cache) > 120:
            _day_cache.pop(next(iter(_day_cache)))
        return rows


def record(user=None, ip=None, kind=None, question="", model=None, usage=None, estimated=False, ok=True, error=None, logId=None) -> dict:
    u = usage or {}
    pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    row = {
        "time": iso_now(), "date": day_of(), "user": user or "bilinmiyor", "ip": ip, "kind": kind,
        "question": str(question or "")[:2000], "model": model,
        "promptTokens": pt, "completionTokens": ct, "totalTokens": int(u.get("total_tokens") or 0) or pt + ct,
        "estimated": bool(estimated), "ok": ok is not False, "error": error, "logId": logId,
    }
    _read_day(row["date"]).append(row)
    try:
        append_line(file("usage", f"{row['date']}.jsonl"), row)
    except OSError as e:
        print("[usage] yazılamadı:", e, flush=True)
    return row


def _sum(rows):
    return sum(r["totalTokens"] for r in rows)


def today(user=None) -> dict:
    d = day_of()
    rows = _read_day(d)
    lim = get_limits()
    total = _sum(rows)
    user_total = _sum([r for r in rows if r["user"] == user]) if user else None
    return {
        "date": d, "total": total, "requests": len(rows), "dailyLimit": lim["dailyLimit"],
        "remaining": max(0, lim["dailyLimit"] - total) if lim["dailyLimit"] else None,
        "user": user, "userTotal": user_total, "perUserDailyLimit": lim["perUserDailyLimit"],
        "userRemaining": max(0, lim["perUserDailyLimit"] - user_total) if lim["perUserDailyLimit"] and user else None,
    }


def assert_allowed(user=None) -> None:
    """Limit dolmuşsa LimitError (429) fırlatır (çağrıdan ÖNCE kontrol edilir)."""
    t = today(user)
    if t["dailyLimit"] and t["total"] >= t["dailyLimit"]:
        raise LimitError(f"Günlük token limiti doldu ({t['total']:,} / {t['dailyLimit']:,}). Limit yarın sıfırlanır veya Ayarlar'dan artırılabilir.".replace(",", "."))
    if t["perUserDailyLimit"] and user and t["userTotal"] >= t["perUserDailyLimit"]:
        raise LimitError(f"{user} için günlük token limiti doldu ({t['userTotal']:,} / {t['perUserDailyLimit']:,}).".replace(",", "."))


def _range(frm: str, to: str) -> list:
    a, b = date.fromisoformat(frm), date.fromisoformat(to)
    out = []
    while a <= b and len(out) < 400:
        out.append(a.isoformat())
        a += timedelta(days=1)
    return out


def _group(rows, key):
    m: dict = {}
    for r in rows:
        k = r.get(key) or "—"
        g = m.setdefault(k, {"key": k, "totalTokens": 0, "promptTokens": 0, "completionTokens": 0, "requests": 0, "errors": 0})
        g["totalTokens"] += r["totalTokens"]
        g["promptTokens"] += r["promptTokens"]
        g["completionTokens"] += r["completionTokens"]
        g["requests"] += 1
        g["errors"] += 0 if r["ok"] else 1
    return sorted(m.values(), key=lambda g: -g["totalTokens"])


def _valid(d):
    try:
        date.fromisoformat(d or "")
        return True
    except ValueError:
        return False


def report(frm=None, to=None, user=None, kind=None, limit: int = 500) -> dict:
    t = day_of()
    to = to if _valid(to) else t
    frm = frm if _valid(frm) else to[:8] + "01"
    if frm > to:
        frm, to = to, frm
    days = _range(frm, to)
    all_rows = [r for d in days for r in _read_day(d)]
    rows = [r for r in all_rows if (not user or r["user"] == user) and (not kind or r["kind"] == kind)]
    return {
        "from": frm, "to": to, "timezone": TZ_NAME,
        "total": {"totalTokens": _sum(rows), "promptTokens": sum(r["promptTokens"] for r in rows),
                  "completionTokens": sum(r["completionTokens"] for r in rows), "requests": len(rows),
                  "errors": sum(1 for r in rows if not r["ok"])},
        "days": [{"date": d, "totalTokens": _sum([r for r in rows if r["date"] == d]), "requests": sum(1 for r in rows if r["date"] == d)} for d in days],
        "byUser": _group(rows, "user"),
        "byKind": _group(rows, "kind"),
        "records": sorted(rows, key=lambda r: r["time"], reverse=True)[:limit],
        "users": sorted({r["user"] for r in all_rows}),
        "limits": get_limits(),
        "today": today(user),
    }
