"""Kurumsal GenAI gateway istemcisi (MorpheusAI ile aynı akış)

  1) Token servisine (TMS) istek atılır → token alınır ve süresi dolana kadar cache'lenir
  2) chat/completions (GPT) adresine header'lar + "Authorization: Bearer <token>" ile istek atılır

Adresler, header'lar ve gövdeler arayüzden (Ayarlar → GenAI bağlantısı) gelir; bkz. connection.py.
Değerlerde şu yer tutucular kullanılabilir:
  {{uuid}}       → her istekte yeni UUID
  {{timestamp}}  → ISO zaman damgası
  {{token}}      → TMS'ten alınan token
  {{env:İSİM}}   → .env'deki bir değişkenin değeri (gövdede JSON-güvenli kaçışlanır)
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from . import connection
from .http_client import request
from .store import iso_now

TLS_HINT = ("Ayarlar → GenAI bağlantısı'nda 'Sertifika doğrulamasını kapat' seçeneğini işaretleyin "
            "veya kurum CA'sını sisteme ekleyin (update-ca-trust).")


class GenAIError(Exception):
    def __init__(self, message, usage=None, log_id=None):
        super().__init__(message)
        self.usage = usage
        self.log_id = log_id


def _config() -> dict:
    c = connection.get()

    def to_obj(lst):
        return {h["name"].lower(): h["value"] for h in lst or [] if h.get("name") and h.get("value") != ""}

    try:
        extra = json.loads(c["chat"]["bodyExtra"]) if c["chat"].get("bodyExtra") else {}
    except ValueError:
        extra = {}
    prefix = str(c["chat"].get("authPrefix") if c["chat"].get("authPrefix") is not None else "Bearer").strip()
    return {
        "tms": {**c["tms"], "headers": to_obj(c["tms"]["headers"]), "tokenHeader": (c["tms"].get("tokenHeader") or "authorization").lower()},
        "chat": {**c["chat"], "headers": to_obj(c["chat"]["headers"]), "authPrefix": prefix + " " if prefix else "", "bodyExtra": extra},
        "insecureTls": c["insecureTls"],
        "timeoutMs": c["timeoutMs"],
    }


def valid_url(v) -> bool:
    if not v or re.search(r"[<>]", v):
        return False
    u = urlsplit(v)
    return u.scheme in ("http", "https") and bool(u.netloc)


def _host(v):
    return urlsplit(v).netloc if valid_url(v) else None


def is_configured() -> bool:
    c = connection.get()
    return valid_url(c["tms"]["url"]) and valid_url(c["chat"]["url"])


_PLACEHOLDER = re.compile(r"\{\{\s*(uuid|token|timestamp|env:([A-Za-z0-9_]+))\s*\}\}")


def fill(s: str, token: str = "", as_json: bool = False) -> str:
    """Yer tutucuları doldurur. as_json=True ise değerler JSON string içine güvenle gömülür."""
    def rep(m):
        k = m.group(1)
        v = str(uuid.uuid4()) if k == "uuid" else token if k == "token" else iso_now() if k == "timestamp" else os.environ.get(m.group(2), "")
        return json.dumps(v)[1:-1] if as_json else v
    return _PLACEHOLDER.sub(rep, str(s))


def clean_token(v) -> str:
    """'Bearer xxx', 'Bearer: xxx', 'bearer:xxx' → 'xxx'"""
    return re.sub(r"^Bearer\s*:?\s*", "", str(v or "").strip(), flags=re.I).strip()


def get_path(obj, path: str):
    for k in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, list):
            try:
                obj = obj[int(k)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(k)
        else:
            return None
    return obj


# ------------------------------------------------------------------
#  Token
# ------------------------------------------------------------------
_cached = None  # {"token", "expires_at"}
_token_lock = threading.Lock()
TOKEN_CANDIDATES = ["access_token", "accessToken", "token", "data.access_token", "data.accessToken", "data.token", "result.token"]
EXPIRES_CANDIDATES = ["expires_in", "expiresIn", "data.expires_in", "data.expiresIn"]


def _fetch_token() -> str:
    global _cached
    c = _config()
    t = c["tms"]
    if not valid_url(t["url"]):
        raise GenAIError("Token servisi adresi tanımlı değil (Ayarlar → GenAI bağlantısı)")
    headers = {k: fill(v) for k, v in t["headers"].items()}
    if t.get("basicUser"):
        headers["authorization"] = "Basic " + base64.b64encode(f"{t['basicUser']}:{t.get('basicPass', '')}".encode()).decode()
    is_json = str(t.get("body") or "").strip().startswith("{")
    body = None if t["method"] == "GET" else (fill(t["body"], as_json=is_json) if t.get("body") else "{}")
    res = request("genai", "tms-token", t["url"], method=t["method"], headers=headers, body=body,
                  insecure_tls=c["insecureTls"], timeout_ms=c["timeoutMs"], tls_hint=TLS_HINT)
    if res["status"] >= 400:
        raise GenAIError(f"Token isteği başarısız (HTTP {res['status']}): {res['text'][:300]}")
    j = res["json"]
    # Token kaynağı: auto (JSON yolu > yanıt header'ı > gövdedeki bilinen alanlar) | header | body
    src = t.get("tokenSource") or "auto"
    from_header = None if src == "body" else res["headers"].get(t["tokenHeader"])
    from_path = get_path(j, t["tokenPath"]) if src != "header" and t.get("tokenPath") else None
    from_body = None if src == "header" else next((v for v in (get_path(j, p) for p in TOKEN_CANDIDATES) if isinstance(v, str)), None)
    token = clean_token(from_path or (from_body if src == "body" else from_header or from_body))
    if not token:
        raise GenAIError(f"Token yanıtında token bulunamadı (kaynak: {src}; gövde veya '{t['tokenHeader']}' header'ı). Ayarlar'da 'Token kaynağı', "
                         f"'Token alanı (JSON yolu)' veya 'Token header'ı' alanlarını kontrol edin. Yanıt: {res['text'][:300] or '(boş gövde)'}")
    exp_raw = get_path(j, t["expiresPath"]) if t.get("expiresPath") else next(
        (v for v in (get_path(j, p) for p in EXPIRES_CANDIDATES) if v is not None), None)
    try:
        exp = float(exp_raw if exp_raw is not None else res["headers"].get("expires_in"))
    except (TypeError, ValueError):
        exp = 0
    exp = exp or t.get("ttl") or 300
    _cached = {"token": token, "expires_at": time.time() + max(exp - 30, 10)}  # süre dolmadan 30 sn önce yenile
    return token


def get_token(force: bool = False) -> str:
    with _token_lock:
        if not force and _cached and _cached["expires_at"] > time.time():
            return _cached["token"]
        return _fetch_token()


def invalidate() -> None:
    """Ayarlar değişince eski token kullanılmasın"""
    global _cached
    _cached = None


# ------------------------------------------------------------------
#  Chat completions
# ------------------------------------------------------------------
def chat(messages: list, model=None, kind: str = "chat", server=None) -> dict:
    """Dönen: {content, usage, model, logId}. Hata durumunda GenAIError (usage / log_id dolu olabilir)."""
    c = _config()
    ch = c["chat"]
    if not valid_url(ch["url"]):
        raise GenAIError("Chat/completions adresi tanımlı değil (Ayarlar → GenAI bağlantısı)")
    model = model or ch.get("model")
    body = json.dumps({**({ch.get("modelField") or "model": model} if model else {}), "messages": messages, **ch["bodyExtra"]}, ensure_ascii=False)

    def send(token):
        headers = {k: fill(v, token=token) for k, v in ch["headers"].items()}
        headers[ch["authHeader"].lower()] = ch["authPrefix"] + token
        return request("genai", kind, ch["url"], method="POST", headers=headers, body=body, insecure_tls=c["insecureTls"],
                       timeout_ms=c["timeoutMs"], tls_hint=TLS_HINT,
                       meta_fn=lambda r: {"usage": (r["json"] or {}).get("usage"), "finishReason": get_path(r["json"], "choices.0.finish_reason"),
                                          "model": (r["json"] or {}).get("model"), "server": server})

    res = send(get_token())
    if res["status"] in (401, 403):  # token yenile, bir kez tekrar dene
        res = send(get_token(force=True))
    j = res["json"] if isinstance(res["json"], dict) else {}

    def fail(msg):
        return GenAIError(msg, usage=j.get("usage"), log_id=res["logId"])

    if res["status"] >= 400:
        raise fail(f"Chat isteği başarısız (HTTP {res['status']}): {res['text'][:400]}")
    reply_path = ch.get("replyPath") or "choices.0.message.content"
    reply = get_path(j, reply_path)
    if not isinstance(reply, str):
        raise fail(f"Yanıtta '{reply_path}' bulunamadı. Ayarlar'da 'Cevap alanı (JSON yolu)' değerini düzeltin. Yanıt: {res['text'][:300]}")
    if not reply.strip():
        # Akıl yürüten modeller (gpt-5.x) token bütçesini düşünmeye harcarsa içerik boş döner
        fr = get_path(j, "choices.0.finish_reason")
        raise fail(f"Model boş cevap döndü (finish_reason={fr or '?'})."
                   + (" Ek chat gövdesindeki max_completion_tokens değerini artırın (ör. 16000)." if fr == "length" else ""))
    return {"content": reply, "usage": j.get("usage"), "model": j.get("model"), "logId": res["logId"]}


def status() -> dict:
    c = connection.get()
    return {
        "configured": is_configured(),
        "source": c.get("source"),
        "tokenHost": _host(c["tms"]["url"]),
        "chatHost": _host(c["chat"]["url"]),
        "model": c["chat"].get("model") or None,
        "tokenCached": bool(_cached and _cached["expires_at"] > time.time()),
        "tokenExpiresIn": max(0, round(_cached["expires_at"] - time.time())) if _cached else 0,
    }
