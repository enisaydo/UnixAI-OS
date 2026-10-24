"""GenAI bağlantı ayarları (arayüzden yönetilir)

Ayarlar → GenAI bağlantısı ekranında girilen TMS token servisi ve chat/completions (GPT) adresleri,
header'lar ve gövdeler data/connection.json dosyasında (0600) saklanır.
Dosya yoksa ilk değerler .env'deki TMS_* / CHAT_* değişkenlerinden alınır (MorpheusAI ile aynı biçim).

Gizli değerler (password, client_secret, authorization, client-id...) tarayıcıya hiçbir zaman açık
gönderilmez; MASK ile değiştirilir. Kaydederken MASK olarak kalan alanlar eski değeri korur.
Değerlerde {{env:İSİM}} kullanılırsa sır .env'de kalır, arayüzde yalnızca yer tutucu görünür.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .env import bool_env, env, headers_from_env
from .errors import ApiError
from .store import iso_now, read_json, write_json

FILE = "connection.json"
MASK = "••••••••"
_SECRET = re.compile(r"^(authorization|proxy-authorization|cookie|password|passwd|pass|pwd|token|access_token|refresh_token|id_token|"
                     r"x-api-key|api[-_]?key|client[-_]?id|basicpass)$|secret", re.I)
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_DEFAULT_HEADERS = [{"name": "accept", "value": "application/json"}, {"name": "content-type", "value": "application/json"}]


def _header_list(d: dict) -> list:
    return [{"name": k, "value": v} for k, v in d.items()]


def from_env() -> dict:
    """.env'deki MorpheusAI biçimli ayarlardan ilk yapılandırma"""
    return {
        "tms": {
            "url": env("TMS_TOKEN_URL"),
            "method": env("TMS_METHOD", "POST"),
            "body": env("TMS_BODY", '{"client_id":"","client_secret":"","grant_type":"password","username":"","password":""}'),
            "headers": _header_list(headers_from_env("TMS_H_")) or list(_DEFAULT_HEADERS),
            "basicUser": env("TMS_BASIC_USER"),
            "basicPass": env("TMS_BASIC_PASS"),
            "tokenPath": env("TMS_TOKEN_PATH"),
            "tokenHeader": env("TMS_TOKEN_HEADER", "authorization"),
            "tokenSource": env("TMS_TOKEN_SOURCE", "auto").lower() if env("TMS_TOKEN_SOURCE", "auto").lower() in ("auto", "header", "body") else "auto",
            "expiresPath": env("TMS_EXPIRES_PATH"),
            "ttl": int(env("TMS_TOKEN_TTL_SECONDS", "300") or 300),
        },
        "chat": {
            "url": env("CHAT_URL"),
            "headers": _header_list(headers_from_env("CHAT_H_")) or list(_DEFAULT_HEADERS),
            "authHeader": env("CHAT_AUTH_HEADER", "Authorization"),
            "authPrefix": env("CHAT_AUTH_PREFIX", "Bearer"),
            "modelField": env("CHAT_MODEL_FIELD", "model"),
            "model": env("CHAT_MODEL", "gpt-5.5"),
            "bodyExtra": env("CHAT_BODY_EXTRA", '{"max_completion_tokens":16000}'),
            "replyPath": env("CHAT_REPLY_PATH", "choices.0.message.content"),
        },
        "insecureTls": bool_env("GENAI_TLS_INSECURE"),
        "timeoutMs": int(env("GENAI_TIMEOUT_MS", "180000") or 180000),
        "source": ".env",
    }


_cache = None


def get() -> dict:
    global _cache
    if _cache is None:
        saved = read_json(FILE)
        base = from_env()
        _cache = ({**base, **saved, "tms": {**base["tms"], **saved.get("tms", {})}, "chat": {**base["chat"], **saved.get("chat", {})},
                   "source": "arayüz"} if saved else base)
    return _cache


# ------------------------------------------------------------------
#  Maskeleme
# ------------------------------------------------------------------
def _is_template(v) -> bool:  # {{env:X}} gibi yer tutucular gösterilebilir
    return bool(re.match(r"^\s*\{\{[^}]+\}\}\s*$", str(v)))


def _mask_val(k, v):
    return MASK if _SECRET.search(str(k)) and v not in ("", None) and not isinstance(v, (dict, list)) and not _is_template(v) else v


def _walk(o, fn, path=()):
    if isinstance(o, list):
        return [_walk(v, fn, path + (i,)) for i, v in enumerate(o)]
    if isinstance(o, dict):
        return {k: (_walk(v, fn, path + (k,)) if isinstance(v, (dict, list)) else fn(k, v, path + (k,))) for k, v in o.items()}
    return o


def _at(o, path):
    for k in path:
        try:
            o = o[k]
        except (KeyError, IndexError, TypeError):
            return None
    return o


_FORM_RE = re.compile(r"^[^=&\s]+=[^&]*(&[^=&\s]+=[^&]*)*$")


def _form_pairs(s: str) -> list:
    return [(kv.split("=", 1) + [""])[:2] for kv in s.split("&")]


def mask_body(body: str) -> str:
    """Gövde JSON veya form-urlencoded olabilir; ikisinde de gizli alanları maskeler"""
    try:
        return json.dumps(_walk(json.loads(body), lambda k, v, p: _mask_val(k, v)), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        pass
    if _FORM_RE.match(body or ""):
        return "&".join(f"{k}={_mask_val(k, v)}" for k, v in _form_pairs(body))
    return body


def unmask_body(nxt: str, prev: str) -> str:
    if MASK not in str(nxt):
        return nxt
    try:
        n = json.loads(nxt)
    except ValueError:
        n = None
    if n is not None:
        try:
            o = json.loads(prev)
        except (ValueError, TypeError):
            o = {}
        return json.dumps(_walk(n, lambda k, v, p: (_at(o, p) if _at(o, p) is not None else "") if v == MASK else v), ensure_ascii=False)
    old = dict(_form_pairs(str(prev or "")))
    return "&".join(f"{k}={old.get(k, '') if v == MASK else v}" for k, v in _form_pairs(str(nxt)))


def _mask_headers(lst):
    return [{"name": h["name"], "value": _mask_val(h["name"], h["value"])} for h in (lst or [])]


def _unmask_headers(nxt, prev):
    out = []
    for h in nxt or []:
        name, value = str(h.get("name") or "").strip(), str(h.get("value") if h.get("value") is not None else "")
        if not name:
            continue
        if value == MASK:
            value = next((o["value"] for o in prev or [] if o["name"].lower() == name.lower()), "")
        out.append({"name": name, "value": value})
    return out


def view() -> dict:
    """Tarayıcıya gönderilecek (maskeli) görünüm"""
    c = get()
    return {**c,
            "tms": {**c["tms"], "body": mask_body(c["tms"]["body"]), "headers": _mask_headers(c["tms"]["headers"]),
                    "basicPass": MASK if c["tms"].get("basicPass") else ""},
            "chat": {**c["chat"], "headers": _mask_headers(c["chat"]["headers"])},
            "mask": MASK}


# ------------------------------------------------------------------
#  Kaydetme
# ------------------------------------------------------------------
def _valid_url(v: str) -> bool:
    return not v or urlsplit(v).scheme in ("http", "https") and bool(urlsplit(v).netloc)


def _int(v, default):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def save(data: dict, user=None) -> dict:
    global _cache
    prev = get()
    t, ch = data.get("tms") or {}, data.get("chat") or {}
    nxt = {
        "tms": {
            "url": str(t.get("url") or "").strip(),
            "method": "GET" if str(t.get("method") or "POST").upper() == "GET" else "POST",
            "body": unmask_body(str(t.get("body") if t.get("body") is not None else ""), prev["tms"]["body"]),
            "headers": _unmask_headers(t.get("headers"), prev["tms"]["headers"]),
            "basicUser": str(t.get("basicUser") or ""),
            "basicPass": prev["tms"].get("basicPass", "") if t.get("basicPass") == MASK else str(t.get("basicPass") or ""),
            "tokenPath": str(t.get("tokenPath") or "").strip(),
            "tokenHeader": str(t.get("tokenHeader") or "authorization").strip().lower(),
            "tokenSource": t.get("tokenSource") if t.get("tokenSource") in ("auto", "header", "body") else "auto",
            "expiresPath": str(t.get("expiresPath") or "").strip(),
            "ttl": _int(t.get("ttl"), 300) or 300,
        },
        "chat": {
            "url": str(ch.get("url") or "").strip(),
            "headers": _unmask_headers(ch.get("headers"), prev["chat"]["headers"]),
            "authHeader": str(ch.get("authHeader") or "Authorization").strip(),
            "authPrefix": str(ch.get("authPrefix") if ch.get("authPrefix") is not None else "Bearer").strip(),
            "modelField": str(ch.get("modelField") or "model").strip(),
            "model": str(ch.get("model") or "").strip(),
            "bodyExtra": str(ch.get("bodyExtra") or "").strip(),
            "replyPath": str(ch.get("replyPath") or "choices.0.message.content").strip(),
        },
        "insecureTls": bool(data.get("insecureTls")),
        "timeoutMs": _int(data.get("timeoutMs"), 180000) or 180000,
        "updatedAt": iso_now(),
        "updatedBy": user,
    }
    errors = []
    if not _valid_url(nxt["tms"]["url"]):
        errors.append("Token servisi adresi http(s):// ile başlamalı")
    if not _valid_url(nxt["chat"]["url"]):
        errors.append("Chat/completions adresi http(s):// ile başlamalı")
    if nxt["tms"]["body"].strip().startswith("{"):
        try:
            json.loads(nxt["tms"]["body"])
        except ValueError as e:
            errors.append(f"Token gövdesi geçerli JSON değil: {e}")
    if nxt["chat"]["bodyExtra"]:
        try:
            if not isinstance(json.loads(nxt["chat"]["bodyExtra"]), dict):
                raise ValueError("bir JSON nesnesi olmalı")
        except ValueError as e:
            errors.append(f"Ek chat gövdesi geçerli JSON değil: {e}")
    for h in nxt["tms"]["headers"] + nxt["chat"]["headers"]:
        if not _HEADER_NAME.match(h["name"]):
            errors.append(f"Geçersiz header adı: {h['name']}")
    if not _HEADER_NAME.match(nxt["chat"]["authHeader"]):
        errors.append(f"Geçersiz yetki header adı: {nxt['chat']['authHeader']}")
    if not 5000 <= nxt["timeoutMs"] <= 600000:
        errors.append("Zaman aşımı 5.000 - 600.000 ms arasında olmalı")
    if errors:
        raise ApiError(" · ".join(errors), 400)
    write_json(FILE, nxt)
    _cache = None
    return view()
