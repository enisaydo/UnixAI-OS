"""Giriş ve oturum yönetimi (LDAP veya yerel yönetici)

.env — LDAP için üç değer yeterli (LDAP_SERVER doluysa LDAP ile giriş yapılır):
  LDAP_SERVER=dc01.kurum.local            ad, ad:port veya ldap(s)://ad:port  (636 → otomatik ldaps)
  LDAP_BASE_DN=DC=kurum,DC=local          kullanıcıların aranacağı kök
  LDAP_BIND_DN=...
      {{username}} İÇERİYORSA → kullanıcı kendi parolasıyla doğrudan bağlanır (servis hesabı gerekmez)
          ör. {{username}}@kurum.local  |  KURUM\\{{username}}  |  uid={{username}},ou=people,dc=kurum,dc=local
      bir OU İSE → kullanıcılar orada aranır, kullanici@alanadi ile kendi parolasıyla bağlanır
      sabit bir DN İSE → kullanıcı LDAP_BASE_DN altında aranır (LDAP_BIND_PASSWORD varsa bu hesapla, yoksa anonim),
          sonra kullanıcının kendi parolasıyla doğrulanır
Opsiyonel: LDAP_USER_FILTER, LDAP_REQUIRED_GROUP, LDAP_ADMIN_GROUP, LDAP_STARTTLS, LDAP_TLS_INSECURE, LDAP_DISPLAY_NAME_ATTR

LDAP yoksa yerel yönetici: ADMIN_USER (varsayılan admin) + ADMIN_PASSWORD. İkisi de yoksa giriş kapalıdır
(yalnızca deneme için; UnixAI root SSH anahtarı kullandığı için sunucuda mutlaka birini açın).

Oturum: HMAC imzalı, HttpOnly çerez (SESSION_TTL_HOURS, varsayılan 8 saat).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

from .env import bool_env, env
from .errors import ApiError
from .ldap import LdapClient, LdapError, escape_filter
from .store import file

COOKIE = "unixai_session"
_PLACEHOLDER = re.compile(r"\{\{\s*username\s*\}\}|\{username\}|%s|%u")
DEFAULT_FILTER = "(|(sAMAccountName={{username}})(userPrincipalName={{username}})(uid={{username}}))"


def _normalize_server(v: str) -> str:
    v = (v or "").strip()
    if not v or re.match(r"^ldaps?://", v, re.I):
        return v
    m = re.search(r":(\d+)$", v)
    return f"{'ldaps' if m and m.group(1) in ('636', '3269') else 'ldap'}://{v}"


def _raw_server() -> str:
    return env("LDAP_SERVER") or env("LDAP_URL")


def ldap_enabled() -> bool:
    return bool(_raw_server()) and not re.search(r"[<>]", _raw_server())


def local_enabled() -> bool:
    return not ldap_enabled() and bool(env("ADMIN_PASSWORD"))


def is_enabled() -> bool:
    return ldap_enabled() or local_enabled()


def _first_rdn_type(dn: str) -> str:
    m = re.match(r"^\s*([A-Za-z]+)\s*=", dn or "")
    return m.group(1).lower() if m else ""


def _domain_of(dn: str) -> str:
    return ".".join(m.strip() for m in re.findall(r"(?:^|,)\s*DC\s*=\s*([^,]+)", dn or "", re.I))


def _config() -> dict:
    bind_raw = env("LDAP_BIND_DN")
    is_tpl = bool(_PLACEHOLDER.search(bind_raw))
    is_container = not is_tpl and _first_rdn_type(bind_raw) in ("ou", "dc", "o", "c")
    base_dn = env("LDAP_BASE_DN") or env("LDAP_SEARCH_BASE")
    upn = env("LDAP_UPN_SUFFIX") or _domain_of(bind_raw) or _domain_of(base_dn)
    return {
        "url": _normalize_server(_raw_server()),
        "startTls": bool_env("LDAP_STARTTLS"),
        "insecureTls": bool_env("LDAP_TLS_INSECURE"),
        "timeoutMs": int(env("LDAP_TIMEOUT_MS", "10000") or 10000),
        "bindDn": "" if is_tpl or is_container else bind_raw,
        "bindPassword": env("LDAP_BIND_PASSWORD"),
        "searchBase": bind_raw if is_container else base_dn,
        "userFilter": env("LDAP_USER_FILTER") or DEFAULT_FILTER,
        "userDnTemplate": bind_raw if is_tpl else f"{{{{username}}}}@{upn}" if is_container else env("LDAP_USER_DN_TEMPLATE"),
        "upnBind": is_container,
        "displayAttr": env("LDAP_DISPLAY_NAME_ATTR", "displayName"),
        "requiredGroup": env("LDAP_REQUIRED_GROUP"),
        "adminGroup": env("LDAP_ADMIN_GROUP"),
        "ttlHours": float(env("SESSION_TTL_HOURS", "8") or 8),
    }


def _with_user(template: str, value: str) -> str:
    return _PLACEHOLDER.sub(lambda m: value, template)


# ------------------------------------------------------------------
#  Kaba kuvvet koruması: 15 dk'da 5 hatalı deneme → 15 dk kilit
# ------------------------------------------------------------------
_failures: dict = {}
_flock = threading.Lock()
WINDOW, MAX_FAIL = 15 * 60, 5


def _check_lock(key):
    f = _failures.get(key)
    if f and f.get("locked_until", 0) > time.time():
        raise ApiError(f"Çok fazla hatalı deneme. {int((f['locked_until'] - time.time()) // 60) + 1} dakika sonra tekrar deneyin.", 429)


def _note_failure(key):
    with _flock:
        now = time.time()
        f = _failures.get(key)
        cur = f if f and now - f["first"] < WINDOW else {"count": 0, "first": now}
        cur["count"] += 1
        if cur["count"] >= MAX_FAIL:
            cur["locked_until"] = now + WINDOW
        _failures[key] = cur


def _same_dn(a: str, b: str) -> bool:
    n = lambda x: re.sub(r"\s*,\s*", ",", x).lower()  # noqa: E731
    return n(a) == n(b)


def authenticate(username: str, password: str, ip: str) -> dict:
    username = str(username or "").strip()
    # AD boş parolayla "anonim bind"e izin verip başarı dönebilir → kesinlikle reddet
    if not username or not password:
        raise ApiError("Kullanıcı adı ve parola zorunlu", 400)
    if len(username) > 128 or re.search(r"[\0\r\n]", username):
        raise ApiError("Geçersiz kullanıcı adı", 400)
    keys = [f"u:{username.lower()}", f"ip:{ip}"]
    for k in keys:
        _check_lock(k)

    def fail():
        for k in keys:
            _note_failure(k)
        print(f"[auth] hatalı giriş: {username} ip={ip}", flush=True)
        return ApiError("Kullanıcı adı veya parola hatalı", 401)

    if local_enabled():
        h = lambda v: hashlib.sha256(str(v).encode()).digest()  # noqa: E731
        ok = hmac.compare_digest(h(username), h(env("ADMIN_USER", "admin"))) & hmac.compare_digest(h(password), h(env("ADMIN_PASSWORD")))
        if not ok:
            raise fail()
        _failures.pop(keys[0], None)
        print(f"[auth] giriş başarılı: {username} (yerel yönetici) ip={ip}", flush=True)
        return {"username": username, "displayName": username, "isAdmin": True}

    c = _config()
    client = LdapClient(c["url"], timeout_ms=c["timeoutMs"], insecure_tls=c["insecureTls"], start_tls=c["startTls"])
    try:
        client.connect()
        attrs = ["distinguishedName", "sAMAccountName", "uid", "cn", "mail", "memberOf", c["displayAttr"]]
        plain = username.split("\\")[-1] if "\\" in username else username.split("@")[0]
        flt = _with_user(c["userFilter"], escape_filter(plain))
        if c["bindDn"]:
            if not c["searchBase"]:
                raise ApiError("LDAP yapılandırması eksik: kullanıcıyı aramak için LDAP_BASE_DN gerekli.", 500)
            if c["bindPassword"]:
                try:
                    client.bind(c["bindDn"], c["bindPassword"])
                except LdapError as e:
                    raise ApiError(f"LDAP servis hesabı bağlanamadı: {e}", 502)
            try:
                found = client.search(c["searchBase"], flt, attrs)
            except LdapError as e:
                if not c["bindPassword"] and e.code in (1, 48, 50, 53):
                    raise ApiError("LDAP sunucusu parolasız (anonim) aramaya izin vermiyor. LDAP_BIND_PASSWORD girin veya LDAP_BIND_DN'e "
                                   "{{username}} içeren şablon yazın (ör. {{username}}@kurum.local).", 502)
                raise
            if len(found) != 1:
                print(f"[auth] '{username}' için {len(found)} kayıt bulundu (filtre: {flt})", flush=True)
                raise fail()
            entry = found[0]
            try:
                client.bind(entry["dn"], password)
            except LdapError as e:
                if e.code == 49:
                    raise fail()
                raise
        elif c["userDnTemplate"]:
            tpl = c["userDnTemplate"]
            is_dn = "=" in tpl
            value = re.sub(r'([,+"\\<>;=#])', r"\\\1", username) if is_dn else plain
            dn = username if not is_dn and re.search(r"[@\\]", username) else _with_user(tpl, value)
            try:
                client.bind(dn, password)
            except LdapError as e:
                if e.code == 49:
                    raise fail()
                raise
            try:
                found = client.search(c["searchBase"], flt, attrs) if c["searchBase"] else []
            except (LdapError, RuntimeError, ValueError):
                found = []
            entry = found[0] if found else {"dn": dn, "attrs": {}}
        else:
            raise ApiError("LDAP yapılandırması eksik: LDAP_BIND_DN tanımlayın (ör. {{username}}@kurum.local).", 500)

        a = entry.get("attrs") or {}
        groups = a.get("memberof") or []
        if c["requiredGroup"] and not any(_same_dn(g, c["requiredGroup"]) for g in groups):
            raise ApiError("Bu uygulamaya erişim yetkiniz yok (gerekli LDAP grubunda değilsiniz)", 403)
        _failures.pop(keys[0], None)
        user = {
            "username": (a.get("samaccountname") or a.get("uid") or [username])[0],
            "displayName": (a.get(c["displayAttr"].lower()) or a.get("cn") or [username])[0],
            "isAdmin": any(_same_dn(g, c["adminGroup"]) for g in groups) if c["adminGroup"] else True,
        }
        print(f"[auth] giriş başarılı: {user['username']}{' (yönetici)' if user['isAdmin'] else ''} ip={ip}", flush=True)
        return user
    except ApiError:
        raise
    except Exception as e:
        print(f"[auth] LDAP hatası ({username}): {e}", flush=True)
        raise ApiError(f"LDAP sunucusuna ulaşılamadı veya hata döndü: {e}", 502)
    finally:
        client.close()


# ------------------------------------------------------------------
#  Oturum çerezi (HMAC imzalı)
# ------------------------------------------------------------------
_secret = None


def _get_secret() -> bytes:
    global _secret
    if _secret:
        return _secret
    if env("SESSION_SECRET"):
        _secret = env("SESSION_SECRET").encode()
        return _secret
    f = file("session-secret")
    try:
        _secret = f.read_bytes()
    except OSError:
        _secret = secrets.token_bytes(32)
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(_secret)
        except OSError:
            pass
    return _secret


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign(payload: str) -> str:
    return _b64(hmac.new(_get_secret(), payload.encode(), hashlib.sha256).digest())


def create_session(user: dict):
    exp = time.time() + _config()["ttlHours"] * 3600
    payload = _b64(json.dumps({"u": user["username"], "n": user["displayName"], "a": user["isAdmin"], "exp": exp * 1000}).encode())
    return f"{payload}.{_sign(payload)}", int(exp - time.time())


def read_session(cookie_header: str):
    m = re.search(rf"(?:^|;\s*){COOKIE}=([^;]+)", cookie_header or "")
    if not m or "." not in m.group(1):
        return None
    payload, mac = m.group(1).split(".", 1)
    if not hmac.compare_digest(mac, _sign(payload)):
        return None
    try:
        s = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    if not s.get("exp") or s["exp"] < time.time() * 1000:
        return None
    return {"username": s["u"], "displayName": s["n"], "isAdmin": bool(s["a"])}


def cookie_header(value: str, max_age: int) -> str:
    return f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}" + ("; Secure" if bool_env("SESSION_COOKIE_SECURE") else "")


def status() -> dict:
    if local_enabled():
        return {"enabled": True, "type": "local", "url": None, "mode": "yerel yönetici (ADMIN_USER/ADMIN_PASSWORD)"}
    c = _config()
    en = is_enabled()
    return {
        "enabled": en, "type": "ldap" if en else None,
        "url": re.sub(r"//([^/]*@)?", "//", c["url"]) if en else None,
        "mode": ("service-account" if c["bindPassword"] else "anonymous-search") if c["bindDn"]
        else f"upn-bind ({c['userDnTemplate'].replace('{{username}}', 'kullanici')}, arama: {c['searchBase']})" if c["upnBind"]
        else "direct-bind" if c["userDnTemplate"] else None,
        "requiredGroup": bool(c["requiredGroup"]), "adminGroup": bool(c["adminGroup"]),
    }
