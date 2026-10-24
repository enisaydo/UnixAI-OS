"""MCP merkezi: barındırılan MCP sunucusu (/mcp, Streamable HTTP) + uzak MCP sunucularına istemci bağlantıları

Barındırma (başka alanlardan gelen ajanlar / MCP istemcileri bağlanır):
  POST /mcp  JSON-RPC 2.0 (initialize, tools/list, tools/call, ping), yanıt application/json
  Kimlik: Authorization: Bearer <token>  — arayüzden üretilen erişim token'ları (salt-okunur seçeneği) veya .env UNIXAI_API_TOKEN
  Origin: tarayıcıdan gelen isteklerde Origin izin listesinde olmalı (DNS rebinding koruması); CORS başlıkları buna göre verilir

İstemci (UnixAI ajanı başka MCP sunucularını kullanır; ör. MorpheusAI, başka ekiplerin ajanları):
  Bağlantılar arayüzden eklenir (ad, URL, token); araçları ajana "<bağlantı>__<araç>" adıyla açılır.

  data/mcp.json  (0600): { allowedOrigins[], tokens[{id,name,hash,prefix,readonly,...}], connections[{id,name,url,token,enabled,...}] }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
from urllib.parse import urlsplit

from . import tools
from .env import env
from .errors import ApiError
from .http_client import request
from .store import iso_now, read_json, write_json

FILE = "mcp.json"
MASK = "••••••••"
VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "unixai-os", "title": "UnixAI OS (RHEL agentless gözlem ajanı)", "version": "2.0.0"}
INSTRUCTIONS = ("UnixAI OS, RHEL sunuculardan agentless log/metrik toplayan ve anomalileri aksiyon maddelerine çeviren bir ajandır. "
                "Sunucu bulguları, ham loglar ve salt-okunur canlı tanı araçları sunar; doğal dille konuşmak için unixai_agent_ask kullanın.")
_lock = threading.RLock()


def _load() -> dict:
    d = read_json(FILE, {}) or {}
    return {"allowedOrigins": d.get("allowedOrigins", []), "tokens": d.get("tokens", []), "connections": d.get("connections", [])}


def _save(d: dict) -> None:
    write_json(FILE, d)


def _hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


# ------------------------------------------------------------------
#  Barındırılan sunucu: erişim token'ları ve Origin
# ------------------------------------------------------------------
def env_tokens() -> list:
    return [t.strip() for t in env("UNIXAI_API_TOKEN").split(",") if len(t.strip()) >= 16]


def authenticate(headers) -> dict | None:
    m = re.match(r"^Bearer\s+(.+)$", headers.get("authorization") or "", re.I)
    if not m:
        return None
    tok = m.group(1).strip()
    h = _hash(tok)
    for t in env_tokens():
        if hmac.compare_digest(h, _hash(t)):
            return {"client": headers.get("x-unixai-client") or "api", "readonly": False, "tokenId": "env"}
    with _lock:
        d = _load()
        for t in d["tokens"]:
            if hmac.compare_digest(h, t["hash"]):
                if time.time() - t.get("_lastSaved", 0) > 60:
                    t["lastUsedAt"] = iso_now()
                    t["_lastSaved"] = time.time()
                    _save(d)
                return {"client": t["name"], "readonly": bool(t.get("readonly")), "tokenId": t["id"]}
    return None


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True  # sunucudan sunucuya istek (tarayıcı değil)
    allowed = _load()["allowedOrigins"]
    return "*" in allowed or origin.rstrip("/") in [a.rstrip("/") for a in allowed]


def server_view(base_url: str) -> dict:
    d = _load()
    return {
        "endpoint": base_url.rstrip("/") + "/mcp", "transport": "Streamable HTTP (JSON yanıt)", "protocolVersions": VERSIONS,
        "allowedOrigins": d["allowedOrigins"], "envToken": bool(env_tokens()),
        "tokens": [{k: t.get(k) for k in ("id", "name", "prefix", "readonly", "createdAt", "createdBy", "lastUsedAt")} for t in d["tokens"]],
        "tools": [{"name": t["name"], "readonly": t["readonly"], "description": t["description"]} for t in tools.TOOLS],
    }


def save_origins(origins, user=None) -> list:
    clean = []
    for o in origins or []:
        o = str(o).strip().rstrip("/")
        if not o:
            continue
        if o != "*" and not re.match(r"^https?://[A-Za-z0-9.-]+(:\d+)?$", o):
            raise ApiError(f"Geçersiz origin: {o} (ör. https://portal.kurum.local)")
        clean.append(o)
    with _lock:
        d = _load()
        d["allowedOrigins"] = clean
        _save(d)
    return clean


def create_token(name: str, readonly: bool, user=None) -> dict:
    name = str(name or "").strip()[:60]
    if not re.match(r"^[\w .@-]{2,60}$", name):
        raise ApiError("Token adı 2-60 karakter olmalı (ör. morpheusai, ekip-x-ajan)")
    raw = "uxa_" + secrets.token_urlsafe(32)
    rec = {"id": uuid.uuid4().hex[:12], "name": name, "hash": _hash(raw), "prefix": raw[:8], "readonly": bool(readonly),
           "createdAt": iso_now(), "createdBy": user, "lastUsedAt": None}
    with _lock:
        d = _load()
        d["tokens"].append(rec)
        _save(d)
    return {**{k: rec[k] for k in ("id", "name", "prefix", "readonly", "createdAt")}, "token": raw}


def delete_token(tid: str) -> None:
    with _lock:
        d = _load()
        if not any(t["id"] == tid for t in d["tokens"]):
            raise ApiError("Token bulunamadı", 404)
        d["tokens"] = [t for t in d["tokens"] if t["id"] != tid]
        _save(d)


# ------------------------------------------------------------------
#  JSON-RPC işleyici (barındırılan /mcp)
# ------------------------------------------------------------------
def _result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle_rpc(msg: dict, principal: dict) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None  # bildirim
    if method == "initialize":
        pv = params.get("protocolVersion")
        return _result(mid, {"protocolVersion": pv if pv in VERSIONS else VERSIONS[0], "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "ping":
        return _result(mid, {})
    if method == "tools/list":
        return _result(mid, {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"],
                                        "annotations": {"readOnlyHint": t["readonly"]}} for t in tools.visible(principal["readonly"])]})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in tools.BY_NAME:
            return _error(mid, -32602, f"Bilinmeyen araç: {name}")
        ctx = {"user": f"mcp:{principal['client']}", "client": principal["client"], "readonly": principal["readonly"], "source": f"mcp:{principal['client']}"}
        try:
            res = tools.call(name, args, ctx)
            text = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, indent=1, default=str)
            out = {"content": [{"type": "text", "text": text}]}
            if isinstance(res, dict):
                out["structuredContent"] = json.loads(json.dumps(res, default=str))
            return _result(mid, out)
        except Exception as e:
            return _result(mid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
    return _error(mid, -32601, f"Desteklenmeyen metot: {method}")


# ------------------------------------------------------------------
#  İstemci: uzak MCP sunucuları
# ------------------------------------------------------------------
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")


def connections_view() -> list:
    return [{**{k: v for k, v in c.items() if k != "token"}, "token": MASK if c.get("token") else ""} for c in _load()["connections"]]


def save_connection(data: dict, user=None) -> dict:
    name = str(data.get("name") or "").strip().lower()
    url = str(data.get("url") or "").strip()
    if not NAME_RE.match(name):
        raise ApiError("Bağlantı adı küçük harf/rakam/_/- olmalı (2-31 karakter, ör. morpheusai)")
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise ApiError("MCP adresi http(s):// ile başlamalı (ör. http://morpheus.kurum.local:3000/mcp)")
    with _lock:
        d = _load()
        cur = next((c for c in d["connections"] if c["id"] == data.get("id")), None)
        if any(c["name"] == name and c is not cur for c in d["connections"]):
            raise ApiError(f"'{name}' adlı bağlantı zaten var", 409)
        token = (cur or {}).get("token", "") if data.get("token") == MASK else str(data.get("token") or "").strip()
        rec = {**(cur or {"id": uuid.uuid4().hex[:12], "createdAt": iso_now()}), "name": name, "url": url, "token": token,
               "enabled": data.get("enabled") is not False, "insecureTls": bool(data.get("insecureTls")),
               "description": str(data.get("description") or "")[:200], "updatedAt": iso_now(), "updatedBy": user}
        d["connections"] = [rec if c is cur else c for c in d["connections"]] if cur else d["connections"] + [rec]
        _save(d)
    _clients.pop(rec["id"], None)
    return {**rec, "token": MASK if rec["token"] else ""}


def delete_connection(cid: str) -> None:
    with _lock:
        d = _load()
        if not any(c["id"] == cid for c in d["connections"]):
            raise ApiError("Bağlantı bulunamadı", 404)
        d["connections"] = [c for c in d["connections"] if c["id"] != cid]
        _save(d)
    _clients.pop(cid, None)


class RemoteMCP:
    """Streamable HTTP MCP istemcisi (JSON veya SSE yanıtı)"""

    def __init__(self, conn: dict):
        self.conn = conn
        self.session = None
        self.protocol = None
        self.info = None
        self.tools = None
        self.tools_at = 0
        self.lock = threading.Lock()
        self._id = 0

    def _post(self, msg: dict, timeout_ms=600000):
        h = {"content-type": "application/json", "accept": "application/json, text/event-stream", "x-unixai-client": "unixai-os"}
        if self.conn.get("token"):
            h["authorization"] = f"Bearer {self.conn['token']}"
        if self.session:
            h["mcp-session-id"] = self.session
        if self.protocol:
            h["mcp-protocol-version"] = self.protocol
        res = request("mcp", f"mcp:{self.conn['name']}:{msg.get('method')}", self.conn["url"], method="POST", headers=h,
                      body=json.dumps(msg, ensure_ascii=False), insecure_tls=self.conn.get("insecureTls", False), timeout_ms=timeout_ms)
        if res["status"] == 404 and self.session:
            raise _SessionExpired()
        if res["status"] >= 400:
            raise ApiError(f"MCP '{self.conn['name']}' HTTP {res['status']}: {res['text'][:300]}", 502)
        if res["headers"].get("mcp-session-id"):
            self.session = res["headers"]["mcp-session-id"]
        if "id" not in msg:
            return None
        ctype = res["headers"].get("content-type", "")
        if "text/event-stream" in ctype:
            for block in res["text"].split("\n\n"):
                data = "\n".join(l[5:].lstrip() for l in block.splitlines() if l.startswith("data:"))
                if not data:
                    continue
                try:
                    j = json.loads(data)
                except ValueError:
                    continue
                if isinstance(j, dict) and j.get("id") == msg["id"]:
                    return j
            raise ApiError(f"MCP '{self.conn['name']}': SSE yanıtında cevap bulunamadı", 502)
        if not isinstance(res["json"], dict):
            raise ApiError(f"MCP '{self.conn['name']}': geçersiz yanıt: {res['text'][:200]}", 502)
        return res["json"]

    def _rpc(self, method, params=None, timeout_ms=600000):
        self._id += 1
        r = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}, timeout_ms)
        if r.get("error"):
            raise ApiError(f"MCP '{self.conn['name']}' {method}: {r['error'].get('message')}", 502)
        return r.get("result") or {}

    def ensure(self):
        if self.info:
            return
        r = self._rpc("initialize", {"protocolVersion": VERSIONS[0], "capabilities": {},
                                      "clientInfo": {"name": "unixai-os", "title": "UnixAI OS ajanı", "version": SERVER_INFO["version"]}}, 30000)
        self.protocol = r.get("protocolVersion") if r.get("protocolVersion") in VERSIONS else VERSIONS[0]
        self.info = {"serverInfo": r.get("serverInfo"), "instructions": r.get("instructions"), "protocolVersion": self.protocol}
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, 30000)

    def _with_retry(self, fn):
        with self.lock:
            try:
                self.ensure()
                return fn()
            except _SessionExpired:
                self.session, self.info = None, None
                self.ensure()
                return fn()

    def list_tools(self, max_age=300):
        if self.tools is not None and time.time() - self.tools_at < max_age:
            return self.tools

        def go():
            out, cursor = [], None
            for _ in range(10):
                r = self._rpc("tools/list", {"cursor": cursor} if cursor else {}, 30000)
                out += r.get("tools", [])
                cursor = r.get("nextCursor")
                if not cursor:
                    break
            return out
        self.tools = self._with_retry(go)
        self.tools_at = time.time()
        return self.tools

    def call(self, name, args):
        return self._with_retry(lambda: self._rpc("tools/call", {"name": name, "arguments": args or {}}))


class _SessionExpired(Exception):
    pass


_clients: dict = {}


def client(conn: dict) -> RemoteMCP:
    c = _clients.get(conn["id"])
    if not c or c.conn.get("url") != conn["url"] or c.conn.get("token") != conn.get("token"):
        c = _clients[conn["id"]] = RemoteMCP(conn)
    return c


def test_connection(cid: str) -> dict:
    conn = next((c for c in _load()["connections"] if c["id"] == cid), None)
    if not conn:
        raise ApiError("Bağlantı bulunamadı", 404)
    _clients.pop(cid, None)
    cl = client(conn)
    try:
        t = cl.list_tools(max_age=0)
        res = {"ok": True, "server": cl.info, "tools": [{"name": x["name"], "description": (x.get("description") or "")[:300]} for x in t]}
    except Exception as e:
        res = {"ok": False, "error": str(e)}
    with _lock:
        d = _load()
        for c in d["connections"]:
            if c["id"] == cid:
                c["lastTest"] = {"at": iso_now(), "ok": res["ok"], "tools": len(res.get("tools", [])), "error": res.get("error"),
                                 "server": (res.get("server") or {}).get("serverInfo")}
        _save(d)
    return res


def _exposed(conn_name: str, tool: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"{conn_name}__{tool}")[:64]


def remote_tools(include_agent: bool = True) -> list:
    """Ajan için etkin bağlantıların araçları: [{exposed, conn, tool, description, inputSchema, agent}]"""
    out = []
    for conn in _load()["connections"]:
        if not conn.get("enabled", True):
            continue
        try:
            for t in client(conn).list_tools():
                is_agent = t["name"].endswith("agent_ask")
                if is_agent and not include_agent:
                    continue
                out.append({"exposed": _exposed(conn["name"], t["name"]), "conn": conn, "tool": t["name"], "agent": is_agent,
                            "description": f"[{conn['name']}] {t.get('description') or ''}"[:1000],
                            "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}}})
        except Exception as e:
            print(f"[mcp] '{conn['name']}' araçları alınamadı: {e}", flush=True)
    return out


def call_remote(conn: dict, tool: str, args: dict):
    r = client(conn).call(tool, args)
    if r.get("structuredContent") is not None:
        data = r["structuredContent"]
    else:
        data = "\n".join(c.get("text", "") for c in r.get("content", []) if c.get("type") == "text")
    if r.get("isError"):
        raise ApiError(f"[{conn['name']}] {data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)}", 502)
    return data
