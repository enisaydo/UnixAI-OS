#!/usr/bin/env python3
"""UnixAI OS - sunucu (bağımlılıksız, yalnızca Python 3.9+ standart kütüphanesi)

- public/ klasörünü statik olarak sunar
- RHEL sunuculardan agentless (SSH) günlük log ve metrik toplar, kural motoru + GenAI (GPT) ile analiz eder
- GenAI bağlantısı arayüzden girilir: token servisi (TMS) → chat/completions (Ayarlar → GenAI bağlantısı)
  Bağlantı yoksa DEMO modunda kural motoru çalışır.

Çalıştırma:   python3 server.py     (ayarlar .env + data/*.json)
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import signal
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from unixai.env import ROOT, env, load_env

load_env()

from unixai import (agent, analyzer, auth, collector, connection, demo, diagnostics, genai, inventory,  # noqa: E402
                    logger, mcp_hub, morpheus, prompts, runs, settings, usage)
from unixai.errors import ApiError  # noqa: E402

PORT = int(env("PORT", "3000") or 3000)
HOST = env("HOST", "0.0.0.0")
PUBLIC_DIR = (ROOT / "public").resolve()
USER_HEADER = env("USER_HEADER").lower()
PUBLIC_ROUTES = {"POST /api/auth/login", "POST /api/auth/logout", "GET /api/auth/me", "GET /api/health"}
ADMIN_RE = re.compile(r"^/api/(limits|prompts|connection|settings|servers|runs|integrations|mcp|agent/settings)(/|$)")
# Servisler arası erişim (MCP sunucusu, otomasyon): Authorization: Bearer <UNIXAI_API_TOKEN> (virgülle birden fazla, ≥16 karakter)
API_TOKENS = [t.strip() for t in env("UNIXAI_API_TOKEN").split(",") if len(t.strip()) >= 16]


def api_session(headers):
    import hashlib
    import hmac
    m = re.match(r"^Bearer\s+(.+)$", headers.get("authorization") or "", re.I)
    if not m or not API_TOKENS:
        return None
    given = hashlib.sha256(m.group(1).strip().encode()).digest()
    if any(hmac.compare_digest(given, hashlib.sha256(t.encode()).digest()) for t in API_TOKENS):
        return {"username": (headers.get("x-unixai-client") or "api")[:40], "displayName": "API", "isAdmin": True, "isApi": True}
    return None

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                               "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
}
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


def mode() -> str:
    return "genai" if genai.is_configured() else "demo"


def is_admin_route(method: str, path: str) -> bool:
    return method != "GET" and bool(ADMIN_RE.match(path)) and path != "/api/connection/test"


def ssh_key_info() -> dict:
    s = settings.get()["ssh"]
    try:
        pub = Path(s["keyFile"] + ".pub").read_text(encoding="utf-8").strip()
    except OSError:
        pub = None
    return {
        "keyFile": s["keyFile"], "user": s["user"], "exists": Path(s["keyFile"]).exists(), "publicKey": pub,
        "generate": f'ssh-keygen -t ed25519 -N "" -f {s["keyFile"]} -C "unixai@$(hostname -s)"',
        "targetSetup": f'bash target-setup.sh "{pub}" <merkez_sunucu_ip>' if pub else None,
    }


def overview() -> dict:
    """Genel Bakış: envanter + son toplama sonuçları"""
    latest = runs.latest_by_server()
    inv = inventory.list_servers()
    names = list(dict.fromkeys([s["name"] for s in inv] + list(latest)))
    servers = []
    for name in names:
        i = next((s for s in inv if s["name"] == name), None)
        l = latest.get(name)
        servers.append({
            "name": name, "host": (i or {}).get("host") or (l or {}).get("host"), "tags": (i or {}).get("tags", []),
            "enabled": i["enabled"] if i else False, "inInventory": bool(i), "demo": not i and (l or {}).get("trigger") == "demo",
            "latest": {k: l.get(k) for k in ("runId", "collectedAt", "collect", "error", "score", "status", "engine", "findings", "actions",
                                              "p1", "headline", "aiError")} | {"summary": l.get("summary") or ""} if l else None,
        })
    last = runs.list_runs(1)
    return {"servers": servers, "fleet": runs.latest_fleet(), "lastRun": last[0] if last else None, "scheduler": runs.scheduler_status(), "mode": mode()}


def chat_context(ctx):
    """Sohbet bağlamı: seçilen sunucunun son analizi veya filo özeti"""
    if not ctx:
        return None
    if ctx.get("server"):
        s = runs.snapshot(ctx["server"])
        if not s:
            return None
        m = s["metrics"]
        return {"name": ctx["server"], "data": {
            "collectedAt": s["collectedAt"], "metrics": {k: v for k, v in m.items() if k not in ("journal_errors", "kernel_messages")},
            "journal_errors": m["journal_errors"], "kernel_messages": m["kernel_messages"], "rule_findings": s["findings"],
            "analysis": s["analysis"], "previous": s.get("previous")}}
    if ctx.get("fleet"):
        o = overview()
        return {"name": "filo", "data": {"fleet": o["fleet"], "servers": [{"name": s["name"], **s["latest"]} for s in o["servers"] if s["latest"]]}}
    return None


def health(deep: bool) -> dict:
    out = {"status": "ok", "mode": mode(), "auth": auth.status(), "genai": genai.status(), "scheduler": runs.scheduler_status(),
           "servers": len(inventory.list_servers()), "morpheus": morpheus.status(),
           "agent": {"mode": agent.settings()["mode"], "detected": agent.settings().get("detected"), "connections": len(mcp_hub.connections_view())}}
    if deep and mode() == "genai":
        try:
            genai.get_token(force=True)
            out["genai"] = {**genai.status(), "tokenCheck": "ok"}
        except Exception as e:
            out["status"] = "degraded"
            out["genai"] = {**genai.status(), "tokenCheck": "failed", "error": str(e)}
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "UnixAI-OS"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # erişim logu yerine yalnızca hatalar
        pass

    # ---------------- yardımcılar ----------------
    def client_ip(self) -> str:
        return (self.headers.get("x-forwarded-for") or self.client_address[0]).split(",")[0].strip()

    def send_json(self, code: int, data, extra_headers=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", MIME[".json"])
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for k, v in {**SECURITY_HEADERS, **(extra_headers or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def who(self, session) -> dict:
        ip = self.client_ip()
        if session:
            return {"user": session["username"], "ip": ip, "isAdmin": session["isAdmin"]}
        raw = (USER_HEADER and self.headers.get(USER_HEADER)) or self.headers.get("x-unixai-user") or ""
        user = unquote(raw).strip()[:80]
        return {"user": user or f"anonim ({ip})", "ip": ip, "isAdmin": True}

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def do_OPTIONS(self):
        if urlsplit(self.path).path != "/mcp":
            return self.send_json(404, {"error": "Not found"})
        origin = self.headers.get("origin")
        if not mcp_hub.origin_allowed(origin):
            return self.send_json(403, {"error": "Origin izinli değil"})
        self.send_response(204)
        for k, v in self.cors(origin).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def cors(self, origin):
        if not origin:
            return {}
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin", "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "authorization, content-type, accept, mcp-session-id, mcp-protocol-version",
                "Access-Control-Expose-Headers": "mcp-session-id", "Access-Control-Max-Age": "600"}

    def handle_mcp(self):
        """Barındırılan MCP sunucusu (Streamable HTTP, JSON yanıt, durumsuz)"""
        origin = self.headers.get("origin")
        if not mcp_hub.origin_allowed(origin):
            return self.send_json(403, {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "Origin izinli değil (Ajan & MCP → İzinli alanlar)"}})
        cors = self.cors(origin)
        if self.command == "GET":  # sunucu başlatmalı SSE akışı sunulmuyor
            return self.send_json(405, {"error": "Bu sunucu yalnızca POST (JSON yanıt) destekler"}, {**cors, "Allow": "POST"})
        if self.command != "POST":
            return self.send_json(405, {"error": "Method not allowed"}, cors)
        principal = mcp_hub.authenticate(self.headers)
        if not principal:
            return self.send_json(401, {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Geçersiz veya eksik token (Authorization: Bearer)"}},
                                  {**cors, "WWW-Authenticate": 'Bearer realm="unixai-os"'})
        length = int(self.headers.get("content-length") or 0)
        try:
            msg = json.loads(self.rfile.read(length)) if 0 < length <= 5_000_000 else None
        except ValueError:
            msg = None
        if msg is None:
            return self.send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, cors)
        if isinstance(msg, list):
            out = [r for r in (mcp_hub.handle_rpc(m, principal) for m in msg) if r]
        else:
            out = mcp_hub.handle_rpc(msg, principal)
        if not out:
            self.send_response(202)
            for k, v in cors.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return self.send_json(200, out, cors)

    def dispatch(self):
        url = urlsplit(self.path)
        try:
            if url.path == "/mcp":
                return self.handle_mcp()
            if url.path.startswith("/api/"):
                self.handle_api(url)
            else:
                self.serve_static(url.path)
        except ApiError as e:
            self.send_json(e.status, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            self.send_json(502, {"error": str(e)})

    # ---------------- API ----------------
    def handle_api(self, url):
        length = int(self.headers.get("content-length") or 0)
        if length > 5_000_000:
            raise ApiError("Body too large", 413)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            return self.send_json(400, {"error": "Geçersiz JSON gövdesi"})
        if not isinstance(body, dict):
            body = {}
        path, method = url.path, self.command
        route = f"{method} {path}"
        qs = {k: v[0] for k, v in parse_qs(url.query).items()}

        # ---- Kimlik doğrulama ----
        api = api_session(self.headers)
        if not api and API_TOKENS and re.match(r"^Bearer\s", self.headers.get("authorization") or "", re.I):
            return self.send_json(401, {"error": "Geçersiz API token"})
        session = api or (auth.read_session(self.headers.get("cookie", "")) if auth.is_enabled() else None)
        if route == "POST /api/auth/login":
            if not auth.is_enabled():
                return self.send_json(400, {"error": "Giriş etkin değil"})
            user = auth.authenticate(body.get("username"), body.get("password"), self.client_ip())
            value, max_age = auth.create_session(user)
            return self.send_json(200, {"enabled": True, "user": user}, {"Set-Cookie": auth.cookie_header(value, max_age)})
        if route == "POST /api/auth/logout":
            return self.send_json(200, {"ok": True}, {"Set-Cookie": auth.cookie_header("", 0)})
        if route == "GET /api/auth/me":
            if not auth.is_enabled():
                return self.send_json(200, {"enabled": False})
            t = auth.status()["type"]
            return (self.send_json(200, {"enabled": True, "type": t, "user": session}) if session
                    else self.send_json(401, {"enabled": True, "type": t, "authRequired": True, "error": "Oturum açmanız gerekiyor"}))
        if auth.is_enabled() and not session and (route not in PUBLIC_ROUTES or qs.get("deep")):
            return self.send_json(401, {"authRequired": True, "error": "Oturum açmanız gerekiyor"})
        if is_admin_route(method, path) and session and not session["isAdmin"]:
            return self.send_json(403, {"error": "Bu işlem için yönetici yetkisi gerekiyor (LDAP yönetici grubu)"})

        who = self.who(session)
        j = self.send_json

        if route == "GET /api/health":
            return j(200, health(bool(qs.get("deep"))))
        if route == "GET /api/overview":
            return j(200, overview())

        # ---- Sunucu envanteri ----
        if route == "GET /api/servers":
            return j(200, {"servers": inventory.list_servers(), "defaults": settings.get()["ssh"], "sshKey": ssh_key_info()})
        if route == "POST /api/servers":
            return j(200, inventory.upsert(body.get("server") or {}, body.get("original")))
        if route == "POST /api/servers/import":
            return j(200, inventory.import_text(body.get("text")))
        if route == "GET /api/ssh-key":
            return j(200, ssh_key_info())

        # ---- Çalıştırmalar ----
        if route == "GET /api/runs":
            return j(200, runs.list_runs(int(qs.get("limit") or 100)))
        if route == "POST /api/runs":
            opts = {"servers": body.get("servers") if isinstance(body.get("servers"), list) else None, "ai": body.get("ai") is not False,
                    "user": who["user"], "ip": who["ip"]}
            m = body.get("mode")
            r = (runs.start_demo(ai=opts["ai"], user=opts["user"], ip=opts["ip"]) if m == "demo"
                 else runs.start_reanalyze(**opts) if m == "reanalyze" else runs.start_collect(**opts))
            return j(202, r)
        if route == "GET /api/collected":
            latest = runs.latest_by_server()
            return j(200, {"sections": collector.SECTIONS, "servers": [
                {"name": e["name"], "host": e.get("host"), "runId": e["runId"], "collectedAt": e.get("collectedAt"), "collect": e["collect"],
                 "error": e.get("error"), "sectionLines": e.get("sectionLines") or {}} for e in latest.values()]})

        # ---- AI ----
        if route == "POST /api/chat":
            context = chat_context(body.get("context"))
            msgs = body.get("messages") if isinstance(body.get("messages"), list) else []
            if mode() != "genai":
                last = next((m.get("content") for m in reversed(msgs) if isinstance(m, dict) and m.get("role") == "user"), "")
                return j(200, {"reply": demo.chat_reply(last, context), "engine": "demo"})
            if body.get("agent") is False:
                return j(200, analyzer.chat(msgs, context, who, model=body.get("model")))
            ctxr = body.get("context") or {}
            r = agent.run(msgs, context={"server": ctxr.get("server")} if ctxr.get("server") else None,
                          ctx={"user": who["user"], "ip": who["ip"], "source": "arayüz"}, origin=who["user"])
            return j(200, {**r, "engine": "agent"})

        # ---- GenAI bağlantısı (arayüzden) ----
        if route == "GET /api/connection":
            return j(200, connection.view())
        if route == "POST /api/connection":
            v = connection.save(body, who["user"])
            genai.invalidate()
            return j(200, {**v, "status": genai.status()})
        if route == "POST /api/connection/test":
            # step: token → yalnızca token servisi; chat → token + kısa bir chat isteği
            out = {"status": genai.status()}
            try:
                genai.get_token(force=True)
                out["token"] = {"ok": True, "expiresIn": genai.status()["tokenExpiresIn"]}
            except Exception as e:
                return j(200, {**out, "token": {"ok": False, "error": str(e)}})
            if body.get("step") == "chat":
                try:
                    out["chat"] = {"ok": True, **analyzer.ping(who)}
                except Exception as e:
                    out["chat"] = {"ok": False, "error": str(e), "logId": getattr(e, "log_id", None)}
            return j(200, out)

        # ---- Ajan & MCP ----
        if route == "GET /api/agent/settings":
            return j(200, {**agent.settings(), "defaults": agent.DEFAULTS})
        if route == "POST /api/agent/settings":
            return j(200, agent.save_settings(body, who["user"]))
        if route == "GET /api/agent/conversations":
            return j(200, agent.conversations(int(qs.get("limit") or 100)))
        if route == "GET /api/mcp/server":
            host = self.headers.get("x-forwarded-host") or self.headers.get("host") or f"localhost:{PORT}"
            proto = self.headers.get("x-forwarded-proto") or "http"
            return j(200, mcp_hub.server_view(f"{proto}://{host}"))
        if route == "POST /api/mcp/origins":
            return j(200, {"allowedOrigins": mcp_hub.save_origins(body.get("allowedOrigins"), who["user"])})
        if route == "POST /api/mcp/tokens":
            return j(201, mcp_hub.create_token(body.get("name"), body.get("readonly"), who["user"]))
        if route == "GET /api/mcp/connections":
            return j(200, mcp_hub.connections_view())
        if route == "POST /api/mcp/connections":
            return j(200, mcp_hub.save_connection(body, who["user"]))
        if route == "GET /api/diagnostics":
            return j(200, diagnostics.catalog())

        # ---- MorpheusAI köprüsü ----
        if route == "GET /api/integrations/morpheus":
            return j(200, morpheus.view())
        if route == "POST /api/integrations/morpheus":
            return j(200, morpheus.save(body, who["user"]))
        if route == "POST /api/integrations/morpheus/test":
            try:
                return j(200, morpheus.test())
            except ApiError as e:
                return j(200, {"ok": False, "error": str(e)})
        if route == "GET /api/remediations":
            return j(200, {"items": morpheus.list_records(qs.get("server"), refresh_pending=qs.get("refresh") != "0"), "integration": morpheus.status()})
        if route == "POST /api/remediations":
            name = str(body.get("server") or "")
            snap = runs.snapshot(name, body.get("run"))
            if not snap:
                return j(404, {"error": "Bu sunucu için analiz yok"})
            snap["inventory"] = inventory.get(name)
            idx = body.get("actionIndex")
            if not isinstance(idx, int):
                return j(400, {"error": "actionIndex (tam sayı) zorunlu"})
            return j(201, morpheus.send(snap, idx, who["user"]))

        # ---- Ayarlar ----
        if route == "GET /api/settings":
            return j(200, {**settings.get(), "defaults": settings.DEFAULTS})
        if route == "POST /api/settings":
            return j(200, settings.save(body, who["user"]))
        if route == "GET /api/prompts":
            return j(200, {**prompts.get(), "defaults": prompts.DEFAULTS})
        if route == "POST /api/prompts":
            return j(200, prompts.save(body, who["user"]))

        # ---- Token kullanımı / loglar ----
        if route == "GET /api/usage":
            return j(200, usage.report(qs.get("from"), qs.get("to"), qs.get("user"), qs.get("kind")))
        if route == "GET /api/usage/today":
            return j(200, {**usage.today(who["user"]), "mode": mode()})
        if route == "GET /api/limits":
            return j(200, usage.get_limits())
        if route == "POST /api/limits":
            try:
                return j(200, usage.save_limits(body, who["user"]))
            except ValueError as e:
                return j(400, {"error": str(e)})
        if route == "GET /api/logs":
            return j(200, logger.list_entries(int(qs.get("limit") or 100), qs.get("service")))

        m = re.match(r"^/api/servers/([^/]+)(?:/(test|snapshot|raw|history))?$", path)
        if m:
            name, sub = unquote(m.group(1)), m.group(2)
            if method == "DELETE" and not sub:
                inventory.remove(name)
                return j(200, {"ok": True})
            if method == "POST" and sub == "test":
                s = inventory.get(name) or (inventory.validate(body["server"]) if body.get("server") else None)
                if not s:
                    return j(404, {"error": "Sunucu bulunamadı"})
                return j(200, collector.test(s, settings.get()["ssh"]))
            if method == "GET" and sub == "history":
                return j(200, runs.history(name))
            if method == "GET" and sub in ("snapshot", "raw"):
                s = runs.snapshot(name, qs.get("run"))
                if not s:
                    return j(404, {"error": "Bu sunucu için toplanmış veri yok"})
                if sub == "raw":
                    return j(200, {"server": name, "run": s["run"], "collectedAt": s["collectedAt"], "sections": s["sections"],
                                   "stderr": s.get("stderr"), "missing": s.get("missing")})
                return j(200, {**{k: v for k, v in s.items() if k != "sections"}, "inventory": inventory.get(name)})
        m = re.match(r"^/api/agent/conversations/([0-9a-f]{16})$", path)
        if method == "GET" and m:
            c = agent.conversation(m.group(1))
            return j(200, c) if c else j(404, {"error": "Konuşma bulunamadı"})
        m = re.match(r"^/api/mcp/tokens/([0-9a-f]{12})$", path)
        if method == "DELETE" and m:
            mcp_hub.delete_token(m.group(1))
            return j(200, {"ok": True})
        m = re.match(r"^/api/mcp/connections/([0-9a-f]{12})(/test)?$", path)
        if m:
            if method == "DELETE" and not m.group(2):
                mcp_hub.delete_connection(m.group(1))
                return j(200, {"ok": True})
            if method == "POST" and m.group(2):
                return j(200, mcp_hub.test_connection(m.group(1)))
        m = re.match(r"^/api/servers/([^/]+)/diagnostic$", path)
        if method == "POST" and m:
            return j(200, diagnostics.run(unquote(m.group(1)), body.get("check"), body.get("params") or {}))
        m = re.match(r"^/api/remediations/([\w-]+)$", path)
        if method == "GET" and m:
            return j(200, morpheus.refresh(m.group(1)))
        m = re.match(r"^/api/runs/([^/]+)$", path)
        if method == "GET" and m:
            r = runs.get(unquote(m.group(1)))
            return j(200, r) if r else j(404, {"error": "Çalıştırma bulunamadı"})
        m = re.match(r"^/api/logs/(.+)$", path)
        if method == "GET" and m:
            e = logger.get(unquote(m.group(1)))
            return j(200, e) if e else j(404, {"error": "Log kaydı bulunamadı (servis yeniden başlatıldıysa yalnızca logs/ai-*.jsonl dosyasında)"})
        return j(404, {"error": "Bilinmeyen uç nokta", "route": route})

    # ---------------- statik dosyalar ----------------
    def serve_static(self, path: str):
        rel = unquote(path).lstrip("/\\") or "index.html"
        target = (PUBLIC_DIR / rel).resolve()
        if PUBLIC_DIR not in target.parents and target != PUBLIC_DIR:
            return self.send_json(403, {"error": "Forbidden"})
        if not target.is_file():
            target = PUBLIC_DIR / "index.html"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix, mimetypes.guess_type(target.name)[0] or "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    s = genai.status()
    print(f"\n  UnixAI OS   →  http://localhost:{PORT}")
    print("  Mod         →  " + (f"GENAI → token: {s['tokenHost']} · chat: {s['chatHost']} ({s['source']})" if mode() == "genai"
                                 else "DEMO (kural motoru) — GenAI adreslerini Ayarlar ekranından girin"))
    ssh = settings.get()["ssh"]
    print(f"  Sunucular   →  {len(inventory.list_servers())} kayıtlı · SSH: {ssh['user']}@ · anahtar {ssh['keyFile']}")
    sc = runs.scheduler_status()
    print(f"  Zamanlayıcı →  {'her gün ' + sc['time'] if sc['enabled'] else 'kapalı'}")
    au = auth.status()
    print("  Giriş       →  " + ((("yerel yönetici (ADMIN_USER / ADMIN_PASSWORD)" if au["type"] == "local" else f"LDAP ({au['url']}, {au['mode'] or 'YAPILANDIRMA EKSİK'})"))
                                if au["enabled"] else "KAPALI — .env'de ADMIN_PASSWORD veya LDAP_SERVER tanımlayın!") + "\n", flush=True)
    runs.start_scheduler()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
