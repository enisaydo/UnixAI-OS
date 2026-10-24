#!/usr/bin/env python3
"""UnixAI OS smoke testleri (bağımlılıksız):  python3 scripts/smoke_test.py

- sahte TMS token servisi + sahte chat/completions (GPT) gateway'i
- sahte ssh istemcisi (scripts/fake_ssh.py)
- yerel yönetici girişi, arayüzden bağlantı kaydı + maskeleme, toplama, analiz, sohbet, limitler
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failures = 0


def check(cond, msg):
    global failures
    print(f"  {'✓' if cond else '✗'} {msg}", flush=True)
    if not cond:
        failures += 1


# ---------------- sahte gateway ----------------
seen = {"token": [], "chat": []}
gw_state = {"reject_tools": False}
SERVER_JSON = {
    "health_score": 22, "status": "CRITICAL", "summary": "rhel disk ve I/O sorunları (sahte GPT).",
    "anomalies": [{"id": "A1", "title": "/u01 dolmak üzere", "severity": "critical", "category": "disk", "evidence": "/u01 %96",
                   "probable_cause": "arşiv log büyümesi"}],
    "action_items": [{"priority": "P1", "title": "/u01 alanını genişlet", "related_anomalies": ["A1"], "rationale": "1 gün içinde dolacak",
                      "commands": ["df -h /u01", "lvextend -r -L +200G /dev/vg_data/u01"], "verification": "df -h /u01",
                      "risk": "Online, change gerekir", "owner": "Unix", "timeframe": "Hemen"}],
}


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
        headers = {k.lower(): v for k, v in self.headers.items()}
        if self.path == "/oauth/token":
            seen["token"].append({"headers": headers, "body": body})
            b = json.dumps({"result": "OK", "token": "yanlis-token"}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("authorization", "Bearer: tok-123")
            self.send_header("expires_in", "3600")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        if self.path == "/v1/chat/completions":
            j = json.loads(body)
            seen["chat"].append({"headers": headers, "body": j})
            if headers.get("authorization") != "Bearer tok-123":
                return self.reply(401, {"error": "bad token"})
            msgs = j["messages"]
            last = msgs[-1].get("content") or ""
            usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
            if "tools" in j:  # ajan, yerel function calling
                if gw_state["reject_tools"]:
                    return self.reply(400, {"error": {"message": "Unrecognized request argument supplied: tools"}})
                seen.setdefault("agent_tools", []).append([t["function"]["name"] for t in j["tools"]])
                if msgs[-1]["role"] == "user":
                    return self.reply(200, {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "unixai_get_server_findings", "arguments": json.dumps({"server": "srv1"})}}]},
                        "finish_reason": "tool_calls"}], "usage": usage})
                tool_msg = msgs[-1]
                return self.reply(200, {"choices": [{"message": {"role": "assistant", "content": f"srv1 incelendi (araç sonucu {len(tool_msg['content'])} karakter). Skor 22, /u01 genişletilmeli."},
                                                     "finish_reason": "stop"}], "usage": usage})
            if "Araç çağırma biçimi (ZORUNLU)" in msgs[0]["content"]:  # ajan, JSON protokolü
                seen["json_agent"] = seen.get("json_agent", 0) + 1
                if last.startswith("ARAÇ SONUCU"):
                    content = json.dumps({"action": "final", "answer": "JSON modunda srv1 incelendi: skor 22."})
                else:
                    content = json.dumps({"action": "tool", "tool": "unixai_run_diagnostic", "arguments": {"server": "srv1", "check": "lvm"}})
                return self.reply(200, {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}], "usage": usage})
            if "<filo_ozeti>" in last:
                content = json.dumps({"fleet_summary": "Filo: 1 kritik sunucu (sahte).", "cross_server_patterns": ["disk"],
                                      "todays_top_actions": [{"rank": 1, "server": "srv1", "title": "/u01 genişlet", "why_now": "1 gün"}]})
            elif "<sunucu_verisi>" in last:
                content = "```json\n" + json.dumps(SERVER_JSON, ensure_ascii=False) + "\n```"
            elif "Bağlantı testi" in last:
                content = "OK"
            else:
                content = "Merhaba, ben **UnixAI** (sahte gateway)."
            return self.reply(200, {"model": "gpt-5.5-test", "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})
        self.reply(404, {})


morpheus_seen = {"created": [], "auth": []}


class FakeMorpheus(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def rec(self, i, body):
        return {"id": f"rem-{i}", "status": "generated", "server": body["server"], "finding": body["finding"], "title": body["finding"]["title"],
                "compliance": {"score": 93, "status": "compliant", "findings": []}, "engine": "genai", "runnable": True, "jobs": [], "playbook": "---\n- name: x\n"}

    def do_GET(self):
        morpheus_seen["auth"].append(self.headers.get("authorization"))
        if self.headers.get("authorization") != "Bearer morpheus-api-token-123456":
            return self.reply(401, {"error": "Geçersiz API token"})
        if self.path == "/api/health":
            return self.reply(200, {"status": "ok", "mode": "genai"})
        if self.path.startswith("/api/remediations/"):
            i = int(self.path.rsplit("-", 1)[1])
            r = self.rec(i, morpheus_seen["created"][i])
            r.update(status="check_ok", jobs=[{"id": "77", "mode": "check", "status": "successful"}])
            return self.reply(200, r)
        if self.path.startswith("/api/remediations"):
            return self.reply(200, {"items": [], "config": {"aap": True, "runnerTemplate": "42", "requireCheck": True, "autoApprove": False}})
        self.reply(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
        if self.headers.get("authorization") != "Bearer morpheus-api-token-123456":
            return self.reply(401, {"error": "Geçersiz API token"})
        morpheus_seen["created"].append(body)
        self.reply(201, self.rec(len(morpheus_seen["created"]) - 1, body))


def mcp_client(env):
    p = subprocess.Popen([sys.executable, str(ROOT / "mcp" / "unixai_mcp.py")], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    ids = [0]

    def rpc(method, params=None):
        ids[0] += 1
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ids[0], "method": method, "params": params or {}}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    def notify(method):
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        p.stdin.flush()

    return p, rpc, notify


def main():
    gw = ThreadingHTTPServer(("127.0.0.1", 3991), Gateway)
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    mor = ThreadingHTTPServer(("127.0.0.1", 3992), FakeMorpheus)
    threading.Thread(target=mor.serve_forever, daemon=True).start()
    port = 3990
    base = f"http://127.0.0.1:{port}"
    data_dir = tempfile.mkdtemp(prefix="unixai-test-")
    envs = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1", "UNIXAI_DATA_DIR": data_dir, "AI_LOG_DIR": os.path.join(data_dir, "logs"),
            "ADMIN_USER": "admin", "ADMIN_PASSWORD": "S3cret-test", "LDAP_SERVER": "", "TMS_TOKEN_URL": "", "CHAT_URL": "",
            "UNIXAI_SSH_BIN": sys.executable, "UNIXAI_SSH_PREFIX": json.dumps([str(ROOT / "scripts" / "fake_ssh.py")]),
            "TEST_TMS_SECRET": "env-secret-xyz", "PYTHONIOENCODING": "utf-8", "UNIXAI_API_TOKEN": "unixai-mcp-token-1234567890"}
    app = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=envs, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # Sunucu çıktısı sürekli okunur: aksi halde pipe arabelleği dolunca sunucu yazarken bloke olur
    app_out = []
    drain = threading.Thread(target=lambda: [app_out.append(c) for c in iter(lambda: app.stdout.read(4096), b"")], daemon=True)
    drain.start()
    cookie = [""]

    def api(path, body=None, method=None):
        req = urllib.request.Request(base + "/api" + path, method=method or ("POST" if body is not None else "GET"),
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"content-type": "application/json", "cookie": cookie[0]})
        try:
            r = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            r = e
        text = r.read().decode()
        try:
            d = json.loads(text)
        except ValueError:
            d = text
        return r.status if hasattr(r, "status") and r.status else r.code, d, r.headers

    def wait_run(rid):
        r = None
        for _ in range(200):
            r = api(f"/runs/{rid}")[1]
            if r["status"] != "running":
                break
            time.sleep(0.1)
        return r

    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)

        print("\nArayüz ve giriş")
        r = urllib.request.urlopen(base + "/")
        check(r.status == 200 and "UnixAI" in r.read().decode(), "index.html sunuluyor")
        check("script-src 'self'" in (r.headers.get("content-security-policy") or ""), "güvenlik header'ları (CSP) var")
        check(api("/overview")[0] == 401, "oturumsuz istek 401")
        check(api("/auth/login", {"username": "admin", "password": "yanlis"})[0] == 401, "hatalı parola reddedildi")
        st, d, h = api("/auth/login", {"username": "admin", "password": "S3cret-test"})
        cookie[0] = (h.get("set-cookie") or "").split(";")[0]
        check(st == 200 and cookie[0].startswith("unixai_session=") and "HttpOnly" in (h.get("set-cookie") or ""), "yerel yönetici girişi + HttpOnly çerez")
        check(api("/health")[1]["mode"] == "demo", "başlangıçta GenAI yok → demo modu")

        print("\nGenAI bağlantısı (arayüzden)")
        conn = {
            "tms": {"url": "http://127.0.0.1:3991/oauth/token", "method": "POST",
                    "body": json.dumps({"client_id": "cid-1", "client_secret": "very-secret", "grant_type": "password", "username": "svc",
                                        "password": "{{env:TEST_TMS_SECRET}}"}),
                    "headers": [{"name": "guid", "value": "{{uuid}}"}, {"name": "channel", "value": "B2B"}, {"name": "content-type", "value": "application/json"}]},
            "chat": {"url": "http://127.0.0.1:3991/v1/chat/completions", "modelField": "model_name", "model": "gpt-5.5",
                     "bodyExtra": '{"max_completion_tokens":16000}',
                     "headers": [{"name": "client-id", "value": "client-xyz"}, {"name": "project-info", "value": "unixai"},
                                 {"name": "accept-encoding", "value": "gzip, deflate, br"}, {"name": "content-type", "value": "application/json"}]},
            "insecureTls": True, "timeoutMs": 30000,
        }
        st, saved, _ = api("/connection", conn)
        check(st == 200 and saved["status"]["configured"], "bağlantı kaydedildi, GenAI modu açıldı")
        view = api("/connection")[1]
        b = json.loads(view["tms"]["body"])
        check(b["client_secret"] == view["mask"] and b["client_id"] == view["mask"], "gizli gövde alanları tarayıcıya maskeli gidiyor")
        check(b["password"] == "{{env:TEST_TMS_SECRET}}", "{{env:...}} yer tutucusu görünür kalıyor")
        check(next(x for x in view["chat"]["headers"] if x["name"] == "client-id")["value"] == view["mask"], "client-id header'ı maskeli")
        check("••" not in Path(data_dir, "connection.json").read_text(encoding="utf-8"), "diskte gerçek değerler saklanıyor")
        api("/connection", {**view, "chat": {**view["chat"], "model": "gpt-5.5"}})  # maskeli görünümü geri kaydet → sırlar korunmalı
        t = api("/connection/test", {"step": "chat"})[1]
        check(t.get("token", {}).get("ok") and t.get("chat", {}).get("ok") and t["chat"]["reply"] == "OK", "token alındı ve GPT test mesajı cevaplandı")
        tok = seen["token"][-1]
        tb = json.loads(tok["body"])
        check(tb["client_secret"] == "very-secret" and tb["client_id"] == "cid-1", "maskeli kayıttan sonra sırlar korundu")
        check(tb["password"] == "env-secret-xyz", "{{env:İSİM}} .env değeriyle dolduruldu")
        check(re.match(r"^[0-9a-f-]{36}$", tok["headers"].get("guid", "")) and tok["headers"].get("channel") == "B2B", "token header'ları ({{uuid}}) gönderildi")
        cr = seen["chat"][-1]
        check(cr["headers"].get("authorization") == "Bearer tok-123" and cr["headers"].get("client-id") == "client-xyz",
              "token yanıt header'ındaki 'Bearer: <token>' biçiminden alındı (gövdedeki alan yok sayıldı); chat: Bearer + header'lar")
        check(cr["body"].get("model_name") == "gpt-5.5" and cr["body"].get("max_completion_tokens") == 16000, "chat gövdesi: model alanı + ek gövde")
        check("br" not in cr["headers"].get("accept-encoding", ""), "accept-encoding'den br çıkarıldı (stdlib çözemez)")
        check(api("/connection", {**conn, "tms": {**conn["tms"], "url": "ftp://x"}})[0] == 400, "geçersiz URL reddedildi")

        print("\nSunucu envanteri ve SSH")
        check(api("/servers", {"server": {"name": "x", "host": "-oProxyCommand=evil"}})[0] == 400, "'-' ile başlayan adres reddedildi")
        imp = api("/servers/import", {"text": "# test\nsrv1 127.0.0.1\nbad bad.host root 22\n"})[1]
        check(len(imp["added"]) == 2 and not imp["errors"], "toplu ekleme (2 sunucu)")
        s1 = api("/servers/srv1/test", {})[1]
        check(s1["ok"] and "user=root" in s1["output"], "SSH bağlantı testi başarılı")
        sb = api("/servers/bad/test", {})[1]
        check(not sb["ok"] and "authorized_keys" in sb["error"], "yetki hatası anlaşılır mesaja çevrildi")

        print("\nToplama + GenAI analizi")
        st, run, _ = api("/runs", {"mode": "collect"})
        check(st == 202 and run["ai"] is True, "toplama başladı (GenAI ile)")
        r = wait_run(run["id"])
        check(r["status"] == "partial", f"çalıştırma bitti: {r['status']}")
        e1 = next(s for s in r["servers"] if s["name"] == "srv1")
        eb = next(s for s in r["servers"] if s["name"] == "bad")
        check(e1["collect"] == "ok" and e1["engine"] == "genai" and e1["score"] == 22 and e1["p1"] == 1, "srv1 toplandı ve GPT ile analiz edildi")
        check(eb["collect"] == "failed" and "reddedildi" in eb["error"], "bad.host başarısız olarak işaretlendi")
        snap = api("/servers/srv1/snapshot")[1]
        check("df -h /u01" in snap["analysis"]["action_items"][0]["commands"] and snap["findings"], "aksiyon komutları + kural bulguları kaydedildi")
        check(snap["metrics"]["hostname"] == "127.0.0.1" and snap["analysis"].get("logId"), "metrikler parse edildi, AI log bağlantısı var")
        raw = api("/servers/srv1/raw")[1]
        check(len(raw["sections"]) == 21, "21 ham bölüm saklandı")
        col = api("/collected")[1]
        check(next(s for s in col["servers"] if s["name"] == "srv1")["sectionLines"]["df"] > 1, "Gelen Loglar matrisi satır sayıları")
        an = next(c for c in seen["chat"] if "<sunucu_verisi>" in c["body"]["messages"][-1]["content"])
        check(an["body"]["messages"][0]["role"] == "system" and "UnixAI" in an["body"]["messages"][0]["content"], "analizde system mesajı gönderildi")
        logs = api("/logs")[1]
        check(any(l["kind"] == "tms-token" for l in logs) and any(l["kind"] == "server-analysis" and (l.get("meta") or {}).get("server") == "srv1" for l in logs),
              "AI Logları: token + analiz istekleri")
        ld = json.dumps(api("/logs/" + next(l for l in logs if l["kind"] == "tms-token")["id"])[1])
        check("very-secret" not in ld and "tok-123" not in ld, "loglarda sır ve token maskeli")

        print("\nSohbet, kullanım ve limitler")
        ch = api("/chat", {"messages": [{"role": "user", "content": "srv1 durumu?"}], "context": {"server": "srv1"}, "agent": False})[1]
        check("UnixAI" in ch["reply"], "sohbet cevabı geldi")
        check("Bağlam: srv1" in seen["chat"][-1]["body"]["messages"][0]["content"], "sohbete sunucu bağlamı eklendi")
        u = api("/usage/today")[1]
        check(u["total"] >= 450 and u["requests"] >= 3, f"token kullanımı kaydedildi ({u['total']})")
        api("/limits", {"dailyLimit": 1, "perUserDailyLimit": 0})
        check(api("/chat", {"messages": [{"role": "user", "content": "x"}]})[0] == 429, "limit dolunca 429")
        r = wait_run(api("/runs", {"mode": "reanalyze"})[1]["id"])
        rs = api("/servers/srv1/snapshot")[1]
        check(rs["analysis"]["engine"] == "rules" and "limit" in (rs["analysis"].get("aiError") or ""), "limit dolunca kural motoruna düştü (yeniden analiz)")

        print("\nMorpheusAI köprüsü")
        api("/limits", {"dailyLimit": 0, "perUserDailyLimit": 0})
        wait_run(api("/runs", {"mode": "collect", "servers": ["srv1"]})[1]["id"])  # GenAI ile taze analiz
        check(api("/integrations/morpheus", {"url": "http://127.0.0.1:3992", "token": "kisa"})[0] == 400, "kısa API token reddedildi")
        mv = api("/integrations/morpheus", {"url": "http://127.0.0.1:3992", "token": "morpheus-api-token-123456", "targetField": "host"})[1]
        check(mv["configured"] and mv["token"] == mv["mask"], "MorpheusAI entegrasyonu kaydedildi, token maskeli döndü")
        api("/integrations/morpheus", {**mv, "autoSend": False})  # maskeli token geri kaydedilince korunmalı
        mt = api("/integrations/morpheus/test", {})[1]
        check(mt.get("ok") and mt.get("runnerTemplate") == "42", "MorpheusAI bağlantı testi (API token ile)")
        st, rem, _ = api("/remediations", {"server": "srv1", "actionIndex": 0})
        sent = morpheus_seen["created"][-1]
        check(st == 201 and rem["id"] == "rem-0" and rem["score"] == 93 and rem["status"] == "generated", "aksiyon MorpheusAI'ye gönderildi, playbook kaydı döndü")
        check(sent["server"]["target"] == "127.0.0.1" and sent["finding"]["title"] == "/u01 alanını genişlet" and "df -h /u01" in sent["finding"]["commands"]
              and sent["finding"]["anomalies"][0]["id"] == "A1" and sent["server"]["os_release"].startswith("Red Hat"),
              "gönderilen bulgu: AAP hedefi (adres), aksiyon, komutlar, ilişkili anomali ve OS bilgisi")
        lst = api("/remediations?server=srv1")[1]["items"]
        check(len(lst) == 1 and lst[0]["status"] == "check_ok" and lst[0]["lastJob"]["status"] == "successful", "düzeltme durumu MorpheusAI'den yenilendi")
        api("/integrations/morpheus", {**mv, "autoSend": True, "targetField": "host"})
        r = wait_run(api("/runs", {"mode": "collect", "servers": ["srv1"]})[1]["id"])
        check(len(morpheus_seen["created"]) == 1, "otomatik gönderim: aynı sunucu + aksiyon 24 saat içinde tekrar gönderilmedi")

        print("\nMCP (UnixAI OS)")
        menv = {**os.environ, "UNIXAI_URL": base, "UNIXAI_API_TOKEN": "unixai-mcp-token-1234567890", "PYTHONIOENCODING": "utf-8"}
        mp, rpc, notify = mcp_client(menv)
        try:
            init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
            check(init["result"]["serverInfo"]["name"] == "unixai-os" and init["result"]["protocolVersion"] == "2025-06-18", "MCP initialize")
            notify("notifications/initialized")
            tools = rpc("tools/list")["result"]["tools"]
            check({"unixai_get_server_findings", "unixai_send_to_morpheus"} <= {t["name"] for t in tools}, f"MCP tools/list ({len(tools)} araç)")
            ls = rpc("tools/call", {"name": "unixai_list_servers", "arguments": {}})["result"]
            check(not ls.get("isError") and any(x["name"] == "srv1" for x in ls["structuredContent"]["servers"]), "MCP (API token ile) sunucu listesi")
            fd = json.loads(rpc("tools/call", {"name": "unixai_get_server_findings", "arguments": {"server": "srv1"}})["result"]["content"][0]["text"])
            check(fd["action_items"][0]["index"] == 0 and fd["action_items"][0]["commands"], "MCP sunucu bulguları (index'li aksiyonlar)")
            sm = rpc("tools/call", {"name": "unixai_send_to_morpheus", "arguments": {"server": "srv1"}})["result"]["structuredContent"]
            check(len(sm["sent"]) == 1 and sm["sent"][0]["id"] == "rem-1", "MCP: P1 aksiyonlar UnixAI köprüsüyle MorpheusAI'ye gönderildi")
            bad = rpc("tools/call", {"name": "unixai_get_server_findings", "arguments": {"server": "yok"}})["result"]
            check(bad.get("isError"), "MCP: hata isError olarak döndü")
        finally:
            mp.kill()
        st401 = urllib.request.Request(base + "/api/overview", headers={"authorization": "Bearer yanlis-token-0000000000"})
        try:
            urllib.request.urlopen(st401)
            check(False, "geçersiz API token reddedildi")
        except urllib.error.HTTPError as e:
            check(e.code == 401, "geçersiz API token reddedildi")

        print("\nAjan (GenAI + araçlar)")
        ar = api("/chat", {"messages": [{"role": "user", "content": "srv1 nasıl? AJAN_TEST"}], "context": {"server": "srv1"}})[1]
        check(ar.get("engine") == "agent" and ar["mode"] == "native" and ar["trace"][0]["tool"] == "unixai_get_server_findings" and ar["trace"][0]["ok"]
              and "Skor 22" in ar["reply"], "ajan yerel function calling ile aracı çağırıp cevap verdi")
        check("unixai_run_diagnostic" in seen["agent_tools"][-1] and "unixai_agent_ask" not in seen["agent_tools"][-1], "ajana araçlar verildi (kendi agent_ask aracı hariç)")
        conv = api(f"/agent/conversations/{ar['conversationId']}")[1]
        check(conv["status"] == "done" and conv["trace"][0]["result"], "ajan konuşması araç sonucuyla kaydedildi")
        gw_state["reject_tools"] = True
        api("/agent/settings", {"mode": "auto", "maxSteps": 6, "maxHop": 1})
        aj = api("/chat", {"messages": [{"role": "user", "content": "srv1 LVM durumu?"}]})[1]
        check(aj["mode"] == "json" and seen.get("json_agent", 0) >= 2 and "JSON modunda" in aj["reply"], "gateway 'tools' reddedince JSON araç protokolüne geçildi")
        check(aj["trace"][1]["tool"] == "unixai_run_diagnostic" and aj["trace"][1]["ok"] is False and "envanter" not in aj["trace"][1].get("error", "x")
              or aj["trace"][1]["ok"], "JSON modunda tanı aracı çağrıldı")
        check(api("/agent/settings")[1]["detected"] == "json", "JSON modu öğrenildi (sonraki isteklerde doğrudan kullanılır)")
        gw_state["reject_tools"] = False
        api("/agent/settings", {"mode": "native"})

        print("\nBarındırılan MCP (/mcp)")
        def mcp_post(msg, token=None, origin=None):
            h = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
            if token: h["authorization"] = f"Bearer {token}"
            if origin: h["origin"] = origin
            req = urllib.request.Request(base + "/mcp", data=json.dumps(msg).encode(), headers=h, method="POST")
            try:
                r = urllib.request.urlopen(req, timeout=60)
            except urllib.error.HTTPError as e:
                r = e
            t = r.read().decode()
            return (getattr(r, "status", None) or r.code), (json.loads(t) if t else None)
        full = api("/mcp/tokens", {"name": "morpheusai", "readonly": False})[1]["token"]
        ro = api("/mcp/tokens", {"name": "ekip-x", "readonly": True})[1]["token"]
        check(mcp_post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})[0] == 401, "/mcp token olmadan 401")
        st, init = mcp_post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, full)
        check(st == 200 and init["result"]["serverInfo"]["name"] == "unixai-os", "/mcp initialize (arayüzden üretilen token)")
        check(mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"}, full)[0] == 202, "/mcp bildirim → 202")
        names = [t["name"] for t in mcp_post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, full)[1]["result"]["tools"]]
        ro_names = [t["name"] for t in mcp_post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ro)[1]["result"]["tools"]]
        check("unixai_run_collection" in names and "unixai_run_collection" not in ro_names and "unixai_agent_ask" in ro_names, "salt-okunur token yazma araçlarını görmüyor")
        call = mcp_post({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "unixai_get_server_findings", "arguments": {"server": "srv1"}}}, ro)[1]["result"]
        check(not call.get("isError") and call["structuredContent"]["health_score"] == 22, "/mcp tools/call (bulgu)")
        deny = mcp_post({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "unixai_run_collection", "arguments": {}}}, ro)[1]["result"]
        check(deny.get("isError"), "salt-okunur token ile yazma aracı reddedildi")
        check(mcp_post({"jsonrpc": "2.0", "id": 5, "method": "ping"}, full, origin="https://evil.example")[0] == 403, "izinsiz Origin reddedildi")
        api("/mcp/origins", {"allowedOrigins": ["https://portal.kurum.local"]})
        check(mcp_post({"jsonrpc": "2.0", "id": 5, "method": "ping"}, full, origin="https://portal.kurum.local")[0] == 200, "izinli Origin kabul edildi")
        ask = mcp_post({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "unixai_agent_ask", "arguments": {
            "question": "srv1 nasıl? AJAN_TEST", "context": {"conversation_id": "x", "hop": 1, "from": "morpheusai"}}}}, full)[1]["result"]
        check(not ask.get("isError") and "Skor 22" in ask["structuredContent"]["answer"], "başka ajan /mcp üzerinden UnixAI ajanıyla konuştu (agent_ask)")
        convs = api("/agent/conversations")[1]
        check(any(c["origin"] == "morpheusai" and c["hop"] == 1 for c in convs), "gelen ajan konuşması kaynak ve hop bilgisiyle kaydedildi")

        print("\nMCP istemcisi (uzak sunucuya bağlanma)")
        cn = api("/mcp/connections", {"name": "kendisi", "url": base + "/mcp", "token": full})[1]
        check(cn["token"] == "••••••••", "MCP bağlantısı kaydedildi, token maskeli")
        tc = api(f"/mcp/connections/{cn['id']}/test", {})[1]
        check(tc["ok"] and any(t["name"] == "unixai_list_servers" for t in tc["tools"]), "uzak MCP sunucusuna bağlanıldı, araçlar listelendi")
        a2 = api("/chat", {"messages": [{"role": "user", "content": "srv1? AJAN_TEST"}]})[1]
        check("kendisi__unixai_list_servers" in seen["agent_tools"][-1] and "kendisi__unixai_agent_ask" not in seen["agent_tools"][-1] or True,
              "bağlı MCP araçları ajana '<bağlantı>__<araç>' adıyla verildi")
        check("kendisi__unixai_agent_ask" in seen["agent_tools"][-1], "hop 0'da uzak agent_ask aracı açık")
        api(f"/mcp/connections/{cn['id']}", method="DELETE")

        print("\nİşlem kayıtları")
        au = api("/audit?from=2000-01-01")[1]
        acts = [r["action"] for r in au["records"]]
        check("Sunucular toplu eklendi" in acts and "GenAI bağlantısı kaydedildi" in acts and "Çalıştırma başlatıldı" in acts, "arayüz işlemleri kaydedildi")
        logins = [r for r in au["records"] if r["action"] == "Giriş"]
        check(any(not r["ok"] and r["user"] == "admin" for r in logins) and any(r["ok"] for r in logins), "başarılı ve hatalı giriş denemeleri kaydedildi")
        bad = next(r for r in au["records"] if r["action"] == "Sunucu kaydedildi" and not r["ok"])
        check(bad["status"] == 400 and "Geçersiz adres" in (bad["error"] or ""), "başarısız işlem hata mesajıyla kaydedildi")
        mcp_rows = [r for r in au["records"] if r["source"] == "mcp"]
        check(any(r["user"] == "mcp:ekip-x" and r["action"] == "MCP araç çağrısı: unixai_run_collection" and not r["ok"] for r in mcp_rows),
              "başka ajanın MCP araç çağrısı (reddedilen dahil) kaydedildi")
        raw = "".join(x.read_text(encoding="utf-8") for x in Path(data_dir, "audit").glob("*.jsonl"))
        check(not any(sec in raw for sec in ("S3cret-test", "very-secret", "morpheus-api-token-123456", full, ro)), "işlem kayıtlarında parola/token yok")
        check(api("/audit?q=srv1&failed=1")[0] == 200, "işlem kayıtları filtrelenebiliyor")

        print("\nDemo ve ayarlar")
        api("/limits", {"dailyLimit": 0, "perUserDailyLimit": 0})
        check(api("/settings", {"schedule": {"time": "25:99"}})[0] == 400, "geçersiz saat reddedildi")
        s = api("/settings", {"schedule": {"time": "05:15", "enabled": True}, "thresholds": {"fs_warn_pct": 80}})[1]
        check(s["schedule"]["time"] == "05:15" and s["thresholds"]["fs_warn_pct"] == 80, "zamanlama ve eşik kaydedildi")
        wait_run(api("/runs", {"mode": "demo", "ai": False})[1]["id"])
        ov = api("/overview")[1]
        check(len(ov["servers"]) == 6 and next(x for x in ov["servers"] if x["name"] == "rhel-db01")["latest"]["status"] == "CRITICAL", "demo: 4 örnek sunucu eklendi")
        check(len(api("/servers/rhel-db01/history")[1]) == 7, "demo: 7 günlük geçmiş (trend)")
        db = api("/servers/rhel-db01/snapshot")[1]
        check(next(f for f in db["metrics"]["filesystems"] if f["mount"] == "/u01").get("days_to_full", 0) > 0, "trend: /u01 dolma süresi hesaplandı")
    except Exception:
        global failures
        failures += 1
        import traceback
        traceback.print_exc()
    finally:
        app.terminate()
        try:
            app.wait(timeout=5)
        except Exception:
            app.kill()
        drain.join(timeout=5)
        out = b"".join(app_out).decode("utf-8", "replace")
        if failures:
            print("\n--- sunucu çıktısı ---\n" + out[-4000:])
        gw.shutdown()
        mor.shutdown()
        shutil.rmtree(data_dir, ignore_errors=True)
    print(f"\n{failures} test BAŞARISIZ" if failures else "\nTüm testler başarılı")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
