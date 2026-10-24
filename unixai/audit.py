"""İşlem kayıtları (audit log): arayüzden, API'den ve MCP'den yapılan her değişiklik

- Durumu değiştiren her API isteği (POST/DELETE), giriş denemeleri, başka ajanlardan gelen MCP araç çağrıları
  ve sunucu hatası dönen istekler kaydedilir: zaman, kullanıcı, IP, işlem, hedef, sonuç, süre
- Parola / token / gövde içeriği yazılmaz; yalnızca işlemi tanımlayan güvenli alanlar (sunucu adı, adres, mod...)
- data/audit/YYYY-MM-DD.jsonl  +  journalctl'e tek satır  +  arayüz: İşlem Kayıtları
"""
from __future__ import annotations

import json
import threading
from collections import deque
from datetime import date, timedelta

from .store import append_line, day_of, file, iso_now

_recent: deque = deque(maxlen=1000)
_lock = threading.Lock()

# route → (işlem adı, gövdeden alınacak güvenli alanlar)
ACTIONS = {
    "POST /api/auth/login": ("Giriş", ["username"]),
    "POST /api/auth/logout": ("Çıkış", []),
    "POST /api/servers": ("Sunucu kaydedildi", ["server.name", "server.host", "server.user", "server.port", "server.enabled", "original"]),
    "POST /api/servers/import": ("Sunucular toplu eklendi", []),
    "POST /api/runs": ("Çalıştırma başlatıldı", ["mode", "servers", "ai"]),
    "POST /api/connection": ("GenAI bağlantısı kaydedildi", ["tms.url", "tms.tokenSource", "chat.url", "chat.model"]),
    "POST /api/connection/test": ("GenAI bağlantısı test edildi", ["step"]),
    "POST /api/settings": ("Ayarlar kaydedildi", ["ssh.user", "ssh.keyFile", "ssh.hostKeyPolicy", "schedule.enabled", "schedule.time", "analysis.ai"]),
    "POST /api/prompts": ("Sistem mesajı kaydedildi", []),
    "POST /api/limits": ("Token limitleri kaydedildi", ["dailyLimit", "perUserDailyLimit"]),
    "POST /api/chat": ("Ajana soru soruldu", ["question", "context.server"]),
    "POST /api/integrations/morpheus": ("MorpheusAI entegrasyonu kaydedildi", ["url", "targetField", "autoSend", "autoSendPriority"]),
    "POST /api/integrations/morpheus/test": ("MorpheusAI bağlantısı test edildi", []),
    "POST /api/remediations": ("Bulgu MorpheusAI'ye gönderildi", ["server", "actionIndex"]),
    "POST /api/agent/settings": ("Ajan ayarları kaydedildi", ["mode", "maxSteps", "maxHop", "allowWrite"]),
    "POST /api/mcp/tokens": ("MCP erişim token'ı üretildi", ["name", "readonly"]),
    "POST /api/mcp/origins": ("MCP izinli alanlar kaydedildi", ["allowedOrigins"]),
    "POST /api/mcp/connections": ("MCP bağlantısı kaydedildi", ["name", "url", "enabled"]),
}
PATTERNS = [  # (method, path öneki/soneki, işlem)
    ("DELETE", "/api/servers/", "Sunucu silindi"),
    ("POST", "/test", "SSH bağlantı testi"),
    ("POST", "/diagnostic", "Canlı tanı çalıştırıldı"),
    ("DELETE", "/api/mcp/tokens/", "MCP token'ı iptal edildi"),
    ("DELETE", "/api/mcp/connections/", "MCP bağlantısı silindi"),
    ("POST", "/approve", "Playbook onaylandı (MorpheusAI'ye aktarıldı)"),
    ("POST", "/reject", "Playbook reddedildi (MorpheusAI'ye aktarıldı)"),
    ("POST", "/launch", "Playbook AAP'de başlatıldı (MorpheusAI)"),
]


def _pick(body: dict, path: str):
    cur = body
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def describe(method: str, path: str, body) -> tuple:
    """(işlem adı, ayrıntı) — ayrıntıda yalnızca güvenli alanlar"""
    body = body if isinstance(body, dict) else {}
    act = ACTIONS.get(f"{method} {path}")
    if act:
        label, fields = act
        detail = {}
        for f in fields:
            v = _pick(body, f) if f != "question" else (next((m.get("content") for m in reversed(body.get("messages") or []) if isinstance(m, dict) and m.get("role") == "user"), None))
            if v not in (None, "", []):
                detail[f.split(".")[-1] if f != "context.server" else "server"] = (v[:200] + "…") if isinstance(v, str) and len(v) > 200 else v
        if path == "/api/servers/import":
            detail["lines"] = len([l for l in str(body.get("text") or "").splitlines() if l.strip() and not l.strip().startswith("#")])
        return label, detail
    for m, frag, label in PATTERNS:
        if method == m and (path.startswith(frag) or path.endswith(frag)):
            target = path.rstrip("/").split("/")
            name = target[3] if len(target) > 3 and path.startswith(("/api/servers/", "/api/remediations/")) else target[-1]
            detail = {"target": name}
            if frag == "/launch":
                detail["mode"] = "run" if body.get("mode") == "run" else "check"
            if frag == "/diagnostic":
                detail.update(check=body.get("check"), params=body.get("params"))
            return label, detail
    return f"{method} {path}", {}


def record(user, ip, method, path, status, duration_ms, label=None, detail=None, error=None, source="arayüz") -> dict:
    row = {
        "time": iso_now(), "date": day_of(), "user": user or "bilinmiyor", "ip": ip, "source": source,
        "method": method, "path": path, "action": label or f"{method} {path}", "detail": detail or {},
        "status": status, "ok": status is not None and status < 400, "durationMs": duration_ms, "error": (error or None) and str(error)[:300],
    }
    with _lock:
        _recent.append(row)
    try:
        append_line(file("audit", f"{row['date']}.jsonl"), row)
    except OSError as e:
        print(f"[işlem] yazılamadı: {e}", flush=True)
    det = " ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in row["detail"].items())
    print(f"[işlem] {row['user']} ({ip}) {row['action']}{' ' + det if det else ''} → {status} {duration_ms}ms"
          + (f" HATA: {row['error']}" if row["error"] else ""), flush=True)
    return row


def _read_day(d: str) -> list:
    rows = []
    try:
        for line in file("audit", f"{d}.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return rows


def query(frm=None, to=None, user=None, q=None, failed=False, limit: int = 1000) -> dict:
    def valid(x):
        try:
            date.fromisoformat(x or "")
            return True
        except ValueError:
            return False
    to = to if valid(to) else day_of()
    frm = frm if valid(frm) else (date.fromisoformat(to) - timedelta(days=6)).isoformat()
    if frm > to:
        frm, to = to, frm
    frm = max(frm, (date.fromisoformat(to) - timedelta(days=399)).isoformat())  # en fazla 400 gün (bitişten geriye)
    days, d = [], date.fromisoformat(frm)
    while d <= date.fromisoformat(to) and len(days) < 400:
        days.append(d.isoformat())
        d += timedelta(days=1)
    all_rows = [r for day in days for r in _read_day(day)]
    rows = all_rows
    if user:
        rows = [r for r in rows if r["user"] == user]
    if failed:
        rows = [r for r in rows if not r["ok"]]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in json.dumps(r, ensure_ascii=False).lower()]
    rows.sort(key=lambda r: r["time"], reverse=True)
    return {"from": frm, "to": to, "total": len(rows), "failed": sum(1 for r in rows if not r["ok"]),
            "records": rows[:limit], "users": sorted({r["user"] for r in all_rows})}
