#!/usr/bin/env python3
"""İki ajanın MCP üzerinden konuşması — uçtan uca test (gerçek UnixAI OS + gerçek MorpheusAI, sahte GPT ve sahte AAP)

  MORPHEUS_DIR=../MorpheusAI python3 scripts/agents_e2e.py

Senaryo:
  kullanıcı → UnixAI ajanı: "rhel-db01'in P1 bulgusunu doğrula, MorpheusAI'den düzeltme iste"
    UnixAI ajanı  → unixai_get_server_findings (yerel)
    UnixAI ajanı  → morpheusai__morpheus_agent_ask  (MCP: UnixAI → MorpheusAI /mcp)
        MorpheusAI ajanı → unixai__unixai_get_server_findings   (MCP: MorpheusAI → UnixAI /mcp, veri aracı)
        MorpheusAI ajanı → morpheus_create_remediation          (GenAI playbook + standart analizi)
        MorpheusAI ajanı → morpheus_launch_remediation (check)  → REDDEDİLİR: playbook henüz onaylanmadı
        MorpheusAI ajanı → "Bu playbook'u yazdım, onayınızı bekliyor"
    UnixAI ajanı  → kullanıcıya özet + playbook (YAML) + Onayla/Reddet kartı
  kullanıcı (UnixAI ekranı) → Onayla  → MorpheusAI (X-Approved-By)
        → Bitbucket'a commit (ilk deneme yetki hatası → UnixAI'den "Tekrar dene")
        → AAP proje sync → kayda özel job template → AAP check (dry-run)
  check başarılı → kullanıcı → Sunucuya uygula → AAP run
  hop sınırı: MorpheusAI ajanı (hop 1) UnixAI ajanına geri soru soramaz (yalnızca veri araçları açık)
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
MDIR = Path(os.environ.get("MORPHEUS_DIR", ROOT.parent / "MorpheusAI")).resolve()
GW, AAP, UX, MP, BB, LF = 3951, 3953, 3954, 3955, 3956, 3957
fails = 0
seen = {"langfuse": [], "unixai_tools": [], "morpheus_tools": [], "aap": [], "rid": None, "jobs": {}, "bb": {}, "bb_deny": True, "synced": [], "templates": []}


def check(c, msg):
    global fails
    print(f"  {'✓' if c else '✗'} {msg}", flush=True)
    fails += 0 if c else 1


def tool_call(name, args):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{int(time.time() * 1000) % 100000}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]},
        "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230}}


def final(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260}}


def last_tool(msgs):
    """Son araç çağrısının adı ve sonucu"""
    if msgs[-1]["role"] != "tool":
        return None, None
    cid = msgs[-1]["tool_call_id"]
    for m in reversed(msgs):
        for tc in m.get("tool_calls") or []:
            if tc["id"] == cid:
                return tc["function"]["name"], msgs[-1]["content"]
    return None, msgs[-1]["content"]


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def j(self, code, o, headers=None):
        b = json.dumps(o, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])) or b"{}")
        if self.path == "/token":
            return self.j(200, {"status": "OK"}, {"authorization": "Bearer: gw-token", "expires_in": "3600"})
        msgs = body.get("messages", [])
        sysm, last = msgs[0]["content"], msgs[-1].get("content") or ""
        if "tools" in body and "UnixAI OS AJANISIN" in sysm:
            seen["unixai_tools"].append([t["function"]["name"] for t in body["tools"]])
            name, res = last_tool(msgs)
            if name is None:
                return self.j(200, tool_call("unixai_get_server_findings", {"server": "rhel-db01"}))
            if name == "unixai_get_server_findings":
                return self.j(200, tool_call("morpheusai__morpheus_agent_ask", {
                    "question": "rhel-db01 sunucusunda /u01 %96 dolu (P1). Standartlara uygun düzeltme playbook'u üret ve check modda çalıştır."}))
            ans = json.loads(res).get("answer", res) if res.startswith("{") else res
            return self.j(200, final(f"UnixAI doğruladı: /u01 %96. MorpheusAI ajanının cevabı: {ans}"))
        if "tools" in body and "MorpheusAI AJANISIN" in sysm:
            seen["morpheus_tools"].append([t["function"]["name"] for t in body["tools"]])
            name, res = last_tool(msgs)
            if name is None:
                return self.j(200, tool_call("unixai__unixai_get_server_findings", {"server": "rhel-db01"}))
            if name == "unixai__unixai_get_server_findings":
                f = json.loads(res)
                act = f["action_items"][0]
                return self.j(200, tool_call("morpheus_create_remediation", {"server": {"name": f["server"], "os_release": f["os_release"]},
                                                                             "finding": {"title": act["title"], "priority": act["priority"], "commands": ["df -h /u01", "vgs"]}}))
            if name == "morpheus_create_remediation":
                seen["rid"] = json.loads(res)["id"]
                # kurallara uymayan bir ajan gibi onaysız check dener — MorpheusAI reddetmeli
                return self.j(200, tool_call("morpheus_launch_remediation", {"id": seen["rid"], "mode": "check"}))
            seen["launch_result"] = res
            return self.j(200, final(f"Bu playbook'u yazdım: {seen['rid']} (uyum 95). AAP'de çalıştırmadan önce onayınızı bekliyorum."))
        if "Bu bulguyu gidermek için" in last:  # MorpheusAI playbook üretimi
            pb = ('---\n- name: "UnixAI | /u01 alanını genişlet"\n  hosts: "{{ target_host }}"\n  become: true\n  gather_facts: true\n  tasks:\n'
                  '    - name: "VG boş alanını kontrol et"\n      ansible.builtin.command: vgs --noheadings -o vg_free vg_data\n      register: unixai_vg\n      changed_when: false\n      check_mode: false\n      tags: [unixai_remediation]\n')
            return self.j(200, final(json.dumps({"title": "/u01 genişletme", "summary": "VG kontrolü", "risk": "medium", "disruptive": False, "playbook": pb})))
        if "şemaya uyan geçerli JSON" in last:  # MorpheusAI standart analizi / UnixAI sunucu analizi
            return self.j(200, final(json.dumps({"score": 95, "status": "compliant", "summary": "uyumlu", "findings": [], "missing": []})))
        return self.j(200, final("tamam"))


class FakeAAP(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def j(self, code, o):
        out = json.dumps(o).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        b = json.loads(self.rfile.read(n) or b"{}")
        path = self.path.split("?")[0]
        if path == "/api/v2/projects/7/update/":
            seen["synced"] = list(seen["bb"])
            return self.j(202, {"project_update": 77, "status": "pending"})
        if path == "/api/v2/job_templates/":
            if b.get("playbook") not in seen["synced"]:
                return self.j(400, {"playbook": ["Playbook not found for project."]})
            t = {"id": 500 + len(seen["templates"]), **b}
            seen["templates"].append(t)
            return self.j(201, t)
        if re.match(r"^/api/v2/job_templates/\d+/credentials/$", path):
            self.send_response(204)
            self.send_header("content-length", "0")
            self.end_headers()
            return None
        b["template"] = path.split("/")[4]
        seen["aap"].append(b)
        return self.j(201, {"job": 900 + len(seen["aap"]), "status": "pending", "ignored_fields": {}})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/v2/project_updates/77/":
            return self.j(200, {"id": 77, "status": "successful"})
        if path == "/api/v2/projects/7/playbooks/":
            return self.j(200, seen["synced"])
        if path == "/api/v2/job_templates/":
            from urllib.parse import parse_qs, urlsplit
            name = parse_qs(urlsplit(self.path).query).get("name", [""])[0]
            return self.j(200, {"results": [t for t in seen["templates"] if t["name"] == name]})
        jid = int(path.rstrip("/").split("/")[-1])
        return self.j(200, {"id": jid, "status": seen["jobs"].get(jid, "pending")})


class FakeLangfuse(BaseHTTPRequestHandler):
    """Langfuse OTLP/HTTP JSON alıcısı: POST /api/public/otel/v1/traces"""
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        import base64
        ok = self.path == "/api/public/otel/v1/traces" and self.headers.get("authorization") == "Basic " + base64.b64encode(b"pk-lf-test:sk-lf-test").decode() \
            and self.headers.get("x-langfuse-ingestion-version") == "4"
        for rs in body.get("resourceSpans", []):
            svc = next((a["value"]["stringValue"] for a in rs["resource"]["attributes"] if a["key"] == "service.name"), "?")
            for ss in rs["scopeSpans"]:
                for sp in ss["spans"]:
                    attrs = {a["key"]: next(iter(a["value"].values())) for a in sp["attributes"]}
                    seen["langfuse"].append({"svc": svc, "ok": ok, "name": sp["name"], "trace": sp["traceId"], "id": sp["spanId"], "parent": sp.get("parentSpanId"), "attrs": attrs})
        self.send_response(200 if ok else 401)
        self.send_header("content-length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


class FakeBitbucket(BaseHTTPRequestHandler):
    """Bitbucket Server REST: PUT /rest/api/1.0/projects/OPS/repos/ansible-ops/browse/<yol>"""
    def log_message(self, *a):
        pass

    def do_PUT(self):
        body = self.rfile.read(int(self.headers["content-length"])).decode()
        def field(name):
            m = re.search(r'name="' + name + r'"\r\n\r\n([\s\S]*?)\r\n--', body)
            return m.group(1) if m else None
        if seen["bb_deny"]:
            code, o = 403, {"errors": [{"message": "You are not permitted to access this resource"}]}
        else:
            fp = self.path.split("/browse/", 1)[1]
            seen["bb"][fp] = {"content": field("content"), "message": field("message"), "branch": field("branch")}
            code, o = 200, {"id": "c0ffee" + "0" * 34}
        out = json.dumps(o).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def call(port, path, body=None, method=None, token=None, headers=None):
    h = {"content-type": "application/json", **(headers or {})}
    if token:
        h["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api{path}", data=json.dumps(body).encode() if body is not None else None,
                                 method=method or ("POST" if body is not None else "GET"), headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: {e.code} {e.read().decode()[:300]}")
    return json.loads(r.read())


def sse_collect(port, sink):
    """GET /api/live akışını okur (canlı olaylar)"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/live", timeout=120) as r:
            sink.append({"type": "_open", "ctype": r.headers.get("content-type")})
            for raw in r:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("data: "):
                    sink.append(json.loads(line[6:]))
    except Exception as e:  # süreç kapanınca biter
        sink.append({"type": "_error", "error": str(e)})


def main():
    for port, h in ((GW, Gateway), (AAP, FakeAAP), (BB, FakeBitbucket), (LF, FakeLangfuse)):
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", port), h).serve_forever, daemon=True).start()
    ud, md = tempfile.mkdtemp(prefix="ux-e2e-"), tempfile.mkdtemp(prefix="mp-e2e-")
    common = {**os.environ, "PYTHONIOENCODING": "utf-8", "LDAP_SERVER": "", "ADMIN_PASSWORD": "",
              "LANGFUSE_URL": f"http://127.0.0.1:{LF}", "LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test", "LANGFUSE_PROJECT_ID": "unixai-morpheus"}
    ux = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                          env={**common, "PORT": str(UX), "HOST": "127.0.0.1", "UNIXAI_DATA_DIR": ud, "AI_LOG_DIR": ud + "/logs", "TMS_TOKEN_URL": "", "CHAT_URL": ""})
    mp = subprocess.Popen(["node", "server.js"], cwd=MDIR, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                          env={**common, "PORT": str(MP), "MORPHEUS_MODE": "genai", "MORPHEUS_DATA_DIR": md, "AI_LOG_FILE": "false",
                               "TMS_TOKEN_URL": f"http://127.0.0.1:{GW}/token", "TMS_BODY": "{}", "TMS_TOKEN_SOURCE": "header",
                               "CHAT_URL": f"http://127.0.0.1:{GW}/chat", "CHAT_MODEL": "gpt-test", "CHAT_BODY_EXTRA": "{}",
                               "AAP_URL": f"http://127.0.0.1:{AAP}", "AAP_TOKEN": "aap", "AAP_API_PREFIX": "/api/v2", "AAP_POLL_MS": "100",
                               "AAP_PROJECT_ID": "7", "AAP_INVENTORY_ID": "3", "AAP_CREDENTIAL_IDS": "11",
                               "BITBUCKET_URL": f"http://127.0.0.1:{BB}", "BITBUCKET_TOKEN": "bb-token", "BITBUCKET_PROJECT": "OPS",
                               "BITBUCKET_REPO": "ansible-ops", "BITBUCKET_BRANCH": "main", "LOG_DIR": md + "/logs", "LOG_FILE": "true"})
    try:
        for _ in range(80):
            try:
                call(UX, "/health"); call(MP, "/health"); break
            except Exception:
                time.sleep(0.2)
        print("\n[Kurulum]")
        call(UX, "/connection", {"tms": {"url": f"http://127.0.0.1:{GW}/token", "method": "POST", "body": "{}", "headers": [], "tokenSource": "header"},
                                 "chat": {"url": f"http://127.0.0.1:{GW}/chat", "model": "gpt-test", "headers": [], "bodyExtra": "{}"}, "timeoutMs": 60000})
        call(UX, "/runs", {"mode": "demo", "ai": False})
        time.sleep(3)
        ux_tok = call(UX, "/mcp/tokens", {"name": "morpheusai", "readonly": False})["token"]
        mp_tok = call(MP, "/mcp/tokens", {"name": "unixai", "readonly": False, "canApprove": True})["token"]  # onay aktarabilir
        c1 = call(UX, "/mcp/connections", {"name": "morpheusai", "url": f"http://127.0.0.1:{MP}/mcp", "token": mp_tok, "description": "Ansible/AAP otomasyon ajanı"})
        c2 = call(MP, "/mcp/connections", {"name": "unixai", "url": f"http://127.0.0.1:{UX}/mcp", "token": ux_tok, "description": "RHEL gözlem ajanı"})
        t1, t2 = call(UX, f"/mcp/connections/{c1['id']}/test", {}), call(MP, f"/mcp/connections/{c2['id']}/test", {})
        check(t1["ok"] and t2["ok"], f"iki yönlü MCP bağlantısı: UnixAI→MorpheusAI ({len(t1['tools'])} araç), MorpheusAI→UnixAI ({len(t2['tools'])} araç)")

        for _ in range(60):  # MorpheusAI düzeltme akışı aktarımı (SSE) bağlansın
            if (call(UX, "/integrations/morpheus").get("relay") or {}).get("connected"):
                break
            time.sleep(0.25)
        check(call(UX, "/integrations/morpheus")["relay"]["connected"], "UnixAI, MorpheusAI düzeltme akışını dinliyor (/api/remediations/events)")
        live_ux, live_mp = [], []
        for port, sink in ((UX, live_ux), (MP, live_mp)):
            threading.Thread(target=sse_collect, args=(port, sink), daemon=True).start()
        time.sleep(0.8)
        check(live_ux[:1] and "text/event-stream" in (live_ux[0].get("ctype") or "") and live_mp[:1] and "text/event-stream" in (live_mp[0].get("ctype") or ""),
              "canlı akış (SSE /api/live) iki uygulamada da açık")
        print("\n[Ajanlar konuşuyor]")
        r = call(UX, "/chat", {"messages": [{"role": "user", "content": "rhel-db01'in P1 bulgusunu doğrula ve MorpheusAI'den düzeltme iste"}]})
        tr = r["trace"]
        print("  UnixAI ajanı cevabı:", r["reply"][:220].replace("\n", " "))
        check(r["mode"] == "native" and [t["tool"] for t in tr] == ["unixai_get_server_findings", "morpheusai__morpheus_agent_ask"], "UnixAI ajanı: yerel veri aracı → MorpheusAI ajanına soru")
        check(tr[1]["remoteAgent"] and tr[1]["where"] == "morpheusai" and tr[1]["ok"], "ajanlar arası çağrı MCP üzerinden başarılı")
        check(re.search(r"rem-\d{8}-[0-9a-f]{8}", r["reply"]) is not None and "onay" in r["reply"], "UnixAI cevabında MorpheusAI'nin yazdığı kayıt ve onay isteği var")
        check(len(r.get("remediations") or []) == 1 and "hosts:" in r["remediations"][0].get("playbook", ""), "sohbet cevabı playbook'u (YAML) onay kartı için içeriyor")
        check("morpheusai__morpheus_agent_ask" in seen["unixai_tools"][0], "UnixAI ajanı MorpheusAI'nin araçlarını görüyor")
        check("unixai__unixai_agent_ask" not in seen["morpheus_tools"][0] and "unixai__unixai_get_server_findings" in seen["morpheus_tools"][0],
              "hop sınırı: MorpheusAI ajanı UnixAI'nin veri araçlarını görüyor, ajanına geri soru soramıyor")
        mconv = call(MP, "/agent/conversations")[0]
        check(mconv["origin"] == "unixai-os" and mconv["hop"] == 1 and mconv["tools"] == ["unixai__unixai_get_server_findings", "morpheus_create_remediation", "morpheus_launch_remediation"],
              "MorpheusAI ajanı: UnixAI'den veri aldı → playbook üretti → onaysız check denemesi reddedildi (konuşma kaydı)")
        check(not seen["aap"] and "onay" in (seen.get("launch_result") or ""), "onay olmadan AAP'ye hiçbir şey gönderilmedi (ajanın check denemesi reddedildi)")
        rems = call(MP, "/remediations")["items"]
        check(len(rems) == 1 and rems[0]["compliance"]["score"] == 95 and rems[0]["status"] == "generated" and not rems[0]["approval"],
              "düzeltme kaydı: uyum 95, onay bekliyor")
        uconv = call(UX, f"/agent/conversations/{r['conversationId']}")
        check(uconv["trace"][1]["result"] and "rem-" in uconv["trace"][1]["result"], "UnixAI konuşma kaydında MorpheusAI ajanının cevabı saklandı")
        print("\n[Langfuse izleri]")
        for _ in range(40):  # arka plan gönderimi (≈2 sn)
            if any(x["name"] == "MorpheusAI ajanı" for x in seen["langfuse"]) and any(x["name"] == "UnixAI ajanı" for x in seen["langfuse"]):
                break
            time.sleep(0.25)
        lf = seen["langfuse"]
        ux_root = next((x for x in lf if x["svc"] == "unixai-os" and x["name"] == "UnixAI ajanı"), None)
        ask = next((x for x in lf if x["svc"] == "unixai-os" and x["name"] == "araç · morpheusai__morpheus_agent_ask"), None)
        mp_root = next((x for x in lf if x["svc"] == "morpheusai" and x["name"] == "MorpheusAI ajanı"), None)
        check(lf and all(x["ok"] for x in lf), f"Langfuse: iki uygulama OTLP/HTTP ile gönderdi, kimlik doğrulama doğru ({len(lf)} gözlem)")
        check(ux_root and mp_root and ask and mp_root["trace"] == ux_root["trace"] and mp_root["parent"] == ask["id"] and ask["parent"] == ux_root["id"],
              "Langfuse: iki ajanın konuşması TEK iz — MorpheusAI ajanı, UnixAI'nin 'morpheus_agent_ask' adımının altında")
        same = [x for x in lf if ux_root and x["trace"] == ux_root["trace"]]
        types = {x["attrs"].get("langfuse.observation.type") for x in same}
        gens = [x for x in same if x["attrs"].get("langfuse.observation.type") == "generation"]
        check({"agent", "tool", "generation", "chain"} <= types and gens and all(x["attrs"].get("gen_ai.usage.input_tokens") == "200" and x["attrs"].get("gen_ai.request.model") for x in gens),
              f"Langfuse: izde ajan, araç, GPT (model + token) ve playbook üretimi var ({len(same)} gözlem, {len(gens)} GPT çağrısı)")
        check(any(x["name"].startswith("Düzeltme playbook'u") for x in same) and "Bearer" not in json.dumps(lf), "Langfuse: playbook üretimi aynı izde, token sızmıyor")
        conv_ux = call(UX, f"/agent/conversations/{r['conversationId']}")
        ts_ = call(UX, "/tracing/status")
        check(conv_ux.get("traceId") == ux_root["trace"] and ts_["enabled"] and ts_["sent"] > 0 and ts_["traceUrl"].endswith("/project/unixai-morpheus/traces/"),
              "Langfuse: konuşma kaydında iz numarası, arayüz bağlantısı hazır")
        print("\n[Canlı akış]")
        time.sleep(0.5)

        def has(evs, **kw):
            return [e for e in evs if all(e.get(k) == v for k, v in kw.items())]
        ux_a2a = has(live_ux, type="a2a", dir="giden", peer="morpheusai")
        check(len(ux_a2a) == 2 and ux_a2a[0]["phase"] == "start" and "u01" in ux_a2a[0].get("question", "") and ux_a2a[1]["phase"] == "end" and ux_a2a[1]["ok"]
              and ux_a2a[1]["rid"] == ux_a2a[0]["rid"] and ux_a2a[1].get("remediations") == 1,
              "UnixAI canlı: MorpheusAI'ye soru başladı → cevap geldi (1 playbook) aynı olay kimliğiyle")
        check(has(live_ux, type="mcp", dir="giden", tool="morpheus_agent_ask", phase="start")
              and has(live_ux, type="mcp", dir="gelen", peer="morpheusai", tool="unixai_get_server_findings", phase="end", ok=True),
              "UnixAI canlı: giden MCP morpheus_agent_ask ve MorpheusAI'den gelen unixai_get_server_findings")
        check([e["tool"] for e in has(live_ux, type="step", phase="start")][:2] == ["unixai_get_server_findings", "morpheusai__morpheus_agent_ask"],
              "UnixAI canlı: ajan adımları sırayla")
        mp_a2a = has(live_mp, type="a2a", dir="gelen")
        check(len(mp_a2a) == 2 and mp_a2a[0]["phase"] == "start" and mp_a2a[1].get("remediations") == 1 and mp_a2a[0]["peer"] == "unixai-os",
              "MorpheusAI canlı: UnixAI'den gelen soru → cevap (1 playbook)")
        mp_steps = [e["tool"] for e in has(live_mp, type="step", phase="start")]
        check(mp_steps[:3] == ["unixai__unixai_get_server_findings", "morpheus_create_remediation", "morpheus_launch_remediation"]
              and has(live_mp, type="mcp", dir="gelen", tool="morpheus_agent_ask", phase="start") and has(live_mp, type="mcp", dir="giden", tool="unixai_get_server_findings"),
              "MorpheusAI canlı: gelen MCP → ajan adımları (UnixAI'ye veri sorusu, playbook üretimi) görünüyor")
        order = [e["id"] for e in live_mp if e.get("type") in ("mcp", "a2a", "step")]
        check(order == sorted(order) and "Bearer" not in json.dumps(live_ux + live_mp), "olaylar sıralı, token sızmıyor")
        print("\n[Ajanlar arası konuşma kaydı]")
        ua = call(UX, "/a2a")["items"]
        check(len(ua) == 1 and ua[0]["direction"] == "giden" and ua[0]["peer"] == "morpheusai" and ua[0]["server"] == "rhel-db01",
              "UnixAI: giden konuşma ayrı kayıtta (karşı ajan, sunucu)")
        udet = call(UX, f"/a2a/{ua[0]['id']}")
        rem = (udet["remediations"] or [{}])[0]
        check("hosts: \"{{ target_host }}\"" in (rem.get("playbook") or "") and rem.get("score") == 95 and rem.get("url", "").endswith(f"/#/remediations/{rem.get('id')}"),
              "UnixAI: MorpheusAI'nin hazırladığı playbook YAML'ı, uyum skoru ve MorpheusAI bağlantısı kayıtta")
        check(len(call(UX, "/a2a?server=rhel-db01")["items"]) == 1 and not call(UX, "/a2a?server=rhel-web01")["items"], "UnixAI: sunucuya göre filtreleme")
        ma = call(MP, "/a2a")["items"]
        check(len(ma) == 1 and ma[0]["direction"] == "gelen" and ma[0]["peer"] == "unixai-os" and ma[0]["server"] == "rhel-db01" and ma[0]["remediations"],
              "MorpheusAI: gelen konuşma ayrı kayıtta (UnixAI ajanından, sunucu, playbook)")
        print("\n[Onay — UnixAI ekranından]")
        rid = rems[0]["id"]
        ap = call(UX, "/approvals")["items"]
        check(len(ap) == 1 and ap[0]["id"] == rid and ap[0]["server"] == "rhel-db01", "UnixAI: onay bekleyen playbook listede (menü rozeti / dashboard)")
        try:
            call(UX, f"/remediations/{rid}/approve", {}, token="sahte-api-token-123456789")
            check(False, "API token ile UnixAI üzerinden onay engellendi")
        except RuntimeError as e:
            check(" 401 " in str(e) or " 403 " in str(e), "API token ile UnixAI üzerinden onay engellendi")
        a = call(UX, f"/remediations/{rid}/approve", {}, headers={"x-unixai-user": "EnisA"})
        check(a["approval"]["by"] == "EnisA (unixai üzerinden)" and a["status"] == "publish_failed" and "403" in (a.get("scm") or {}).get("error", "") and not seen["aap"],
              "onay aktarıldı (EnisA); Bitbucket yetki hatası UnixAI kartında görünüyor, AAP'de iş yok")
        seen["bb_deny"] = False
        a = call(UX, f"/remediations/{rid}/publish", {}, headers={"x-unixai-user": "EnisA"})
        fp = f"playbooks/unixai/rhel-db01/{rid}.yml"
        check(fp in seen["bb"] and 'hosts: "{{ target_host }}"' in seen["bb"][fp]["content"] and seen["bb"][fp]["branch"] == "main" and "EnisA" in seen["bb"][fp]["message"],
              f"UnixAI'den 'Tekrar dene' → playbook Bitbucket'a commit edildi ({fp})")
        t = (seen["templates"] or [{}])[0]
        check(t.get("playbook") == fp and t.get("project") == 7 and a["aapTemplate"]["id"] == str(t.get("id")), "AAP projesi sync edildi → kayda özel job template oluşturuldu")
        check(len(seen["aap"]) == 1 and seen["aap"][0]["job_type"] == "check" and seen["aap"][0]["limit"] == "rhel-db01" and seen["aap"][0]["template"] == str(t.get("id")),
              "yeni template ile AAP'de check (dry-run) başladı, hedef rhel-db01")
        check(not call(UX, "/approvals")["items"], "onaydan sonra bekleyen onay kalmadı")
        try:
            call(UX, f"/remediations/{rid}/launch", {"mode": "run"})
            check(False, "check bitmeden sunucuya uygulama engellendi")
        except RuntimeError as e:
            check(" 409 " in str(e), "check bitmeden sunucuya uygulama engellendi")
        seen["jobs"][901] = "successful"
        time.sleep(3.2)
        check(call(UX, f"/remediations/{rid}")["status"] == "check_ok", "check başarılı (durum MorpheusAI'den yenilendi)")
        run = call(UX, f"/remediations/{rid}/launch", {"mode": "run"}, headers={"x-unixai-user": "EnisA"})
        check(len(seen["aap"]) == 2 and seen["aap"][1]["job_type"] == "run" and run["status"] == "running", "kişi UnixAI'den 'Sunucuya uygula' dedi → AAP run")
        time.sleep(0.4)
        check(has(live_ux, type="http", dir="giden", method="POST", path=f"/api/remediations/{rid}/approve", status=200)
              and [e for e in has(live_mp, type="http", dir="gelen", path=f"/api/remediations/{rid}/approve") if e.get("by") == "EnisA"],
              "canlı: onay isteği UnixAI'den giden ve MorpheusAI'ye gelen REST olarak (onaylayan EnisA) göründü")
        rem_ux = [e for e in live_ux if e.get("type") == "rem" and e.get("src") == "morpheusai" and e.get("rem") == rid]
        stages = [e["stage"] for e in rem_ux]
        check(all(x in stages for x in ("generated", "approved", "commit", "template", "launch")),
              f"UnixAI canlı: MorpheusAI düzeltme akışı aktarıldı (AKIŞ: {' → '.join(dict.fromkeys(stages))})")
        tk = call(UX, "/morpheus/tasks")["items"]
        td = call(UX, f"/remediations/{rid}")
        check(any(x["id"] == rid and x["server"] == "rhel-db01" and x["score"] == 95 for x in tk) and len(td["events"]) >= 6 and td["playbook"],
              f"Geçmiş · Görevler: MorpheusAI'deki görevler UnixAI'de listeleniyor ({len(tk)} görev), zaman çizelgesi {len(td['events'])} olay")
        cats_ux = {c["category"]: c for c in call(UX, "/oplogs")["categories"]}
        cats_mp = {c["category"]: c for c in call(MP, "/oplogs")["categories"]}
        check(cats_ux["canli"]["files"] and cats_mp["canli"]["files"], "canlı akış kalıcı: logs/canli/ iki uygulamada da yazıldı (yeniden başlayınca geri yüklenir)")
        au = [x for x in call(UX, "/audit")["records"] if "Playbook" in str(x.get("action", ""))]
        check(len(au) >= 3, "onay, tekrar deneme ve uygulama İşlem Kayıtları'na düştü")
        print("\n[Log dosyaları]")
        today = time.strftime("%Y-%m-%d", time.gmtime())
        mcats = {c["category"]: c for c in call(MP, "/oplogs")["categories"]}
        check(all(mcats.get(c, {}).get("files") for c in ("islem", "duzeltme", "bitbucket", "aap", "genai", "mcp-sunucu", "mcp-istemci", "ajan", "ajanlar-arasi")),
              "MorpheusAI: islem, duzeltme, bitbucket, aap, genai, mcp-sunucu, mcp-istemci, ajan, ajanlar-arasi ayrı dosyalarda")
        dz = [x["action"] for x in call(MP, f"/oplogs/duzeltme?date={today}")["items"]][::-1]
        steps = ["Playbook üretildi", "Playbook onaylandı", "hazırlığı başarısız", "Bitbucket'a commit", "AAP projesi", "job template", "Check (dry-run)", "Gerçek çalıştırma"]
        pos = [next((i for i, a in enumerate(dz) if k in a), -1) for k in steps]
        check(all(p >= 0 for p in pos) and pos == sorted(pos), "MorpheusAI duzeltme logu: üretim → onay → hata → commit → sync → template → check → run sırasıyla")
        ucats = {c["category"]: c for c in call(UX, "/oplogs")["categories"]}
        check(all(ucats.get(c, {}).get("files") for c in ("islem", "morpheus", "genai", "mcp-istemci", "mcp-sunucu", "ajan", "ajanlar-arasi")),
              "UnixAI: islem, morpheus, genai, mcp-istemci, mcp-sunucu, ajan, ajanlar-arasi ayrı dosyalarda")
        call(UX, "/chat", {"messages": [{"role": "user", "content": "rhel-db01 nasıl?"}], "agent": False})
        check(len(call(UX, "/a2a")["items"]) == 1, "kullanıcı sohbetleri ajanlar arası kayda karışmıyor")
        if os.environ.get("E2E_KEEP"):  # arayüzü incelemek için süreçleri açık tut
            print(f"Arayüzler: UnixAI http://127.0.0.1:{UX}  ·  MorpheusAI http://127.0.0.1:{MP}", flush=True)
            time.sleep(int(os.environ["E2E_KEEP"]))
    finally:
        ux.kill()
        mp.kill()
        shutil.rmtree(ud, ignore_errors=True)
        shutil.rmtree(md, ignore_errors=True)
    print(f"\n{fails} test BAŞARISIZ" if fails else "\nİki ajan MCP üzerinden başarıyla konuştu.")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
