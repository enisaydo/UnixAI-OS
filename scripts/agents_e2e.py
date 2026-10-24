#!/usr/bin/env python3
"""İki ajanın MCP üzerinden konuşması — uçtan uca test (gerçek UnixAI OS + gerçek MorpheusAI, sahte GPT ve sahte AAP)

  MORPHEUS_DIR=../MorpheusAI python3 scripts/agents_e2e.py

Senaryo:
  kullanıcı → UnixAI ajanı: "rhel-db01'in P1 bulgusunu doğrula, MorpheusAI'den düzeltme iste"
    UnixAI ajanı  → unixai_get_server_findings (yerel)
    UnixAI ajanı  → morpheusai__morpheus_agent_ask  (MCP: UnixAI → MorpheusAI /mcp)
        MorpheusAI ajanı → unixai__unixai_get_server_findings   (MCP: MorpheusAI → UnixAI /mcp, veri aracı)
        MorpheusAI ajanı → morpheus_create_remediation          (GenAI playbook + standart analizi)
        MorpheusAI ajanı → morpheus_launch_remediation (check)  (AAP runner)
    UnixAI ajanı  → kullanıcıya özet
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
GW, AAP, UX, MP = 3951, 3953, 3954, 3955
fails = 0
seen = {"unixai_tools": [], "morpheus_tools": [], "aap": []}


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
                rid = json.loads(res)["id"]
                return self.j(200, tool_call("morpheus_launch_remediation", {"id": rid, "mode": "check"}))
            r = json.loads(res)
            return self.j(200, final(f"{r['id']} oluşturuldu (uyum {r['compliance']['score']}), check çalıştırması #{r['jobs'][0]['id']} başlatıldı; gerçek uygulama için onay gerekli."))
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

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["content-length"])))
        seen["aap"].append(b)
        out = json.dumps({"job": 900 + len(seen["aap"]), "status": "pending", "ignored_fields": {}}).encode()
        self.send_response(201)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        out = json.dumps({"id": int(self.path.rstrip("/").split("/")[-1]), "status": "pending"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def call(port, path, body=None, method=None, token=None):
    h = {"content-type": "application/json"}
    if token:
        h["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api{path}", data=json.dumps(body).encode() if body is not None else None,
                                 method=method or ("POST" if body is not None else "GET"), headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: {e.code} {e.read().decode()[:300]}")
    return json.loads(r.read())


def main():
    for port, h in ((GW, Gateway), (AAP, FakeAAP)):
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", port), h).serve_forever, daemon=True).start()
    ud, md = tempfile.mkdtemp(prefix="ux-e2e-"), tempfile.mkdtemp(prefix="mp-e2e-")
    common = {**os.environ, "PYTHONIOENCODING": "utf-8", "LDAP_SERVER": "", "ADMIN_PASSWORD": ""}
    ux = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                          env={**common, "PORT": str(UX), "HOST": "127.0.0.1", "UNIXAI_DATA_DIR": ud, "AI_LOG_DIR": ud + "/logs", "TMS_TOKEN_URL": "", "CHAT_URL": ""})
    mp = subprocess.Popen(["node", "server.js"], cwd=MDIR, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                          env={**common, "PORT": str(MP), "MORPHEUS_MODE": "genai", "MORPHEUS_DATA_DIR": md, "AI_LOG_FILE": "false",
                               "TMS_TOKEN_URL": f"http://127.0.0.1:{GW}/token", "TMS_BODY": "{}", "TMS_TOKEN_SOURCE": "header",
                               "CHAT_URL": f"http://127.0.0.1:{GW}/chat", "CHAT_MODEL": "gpt-test", "CHAT_BODY_EXTRA": "{}",
                               "AAP_URL": f"http://127.0.0.1:{AAP}", "AAP_TOKEN": "aap", "AAP_API_PREFIX": "/api/v2", "REMEDIATION_JOB_TEMPLATE_ID": "42"})
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
        mp_tok = call(MP, "/mcp/tokens", {"name": "unixai", "readonly": False})["token"]
        c1 = call(UX, "/mcp/connections", {"name": "morpheusai", "url": f"http://127.0.0.1:{MP}/mcp", "token": mp_tok, "description": "Ansible/AAP otomasyon ajanı"})
        c2 = call(MP, "/mcp/connections", {"name": "unixai", "url": f"http://127.0.0.1:{UX}/mcp", "token": ux_tok, "description": "RHEL gözlem ajanı"})
        t1, t2 = call(UX, f"/mcp/connections/{c1['id']}/test", {}), call(MP, f"/mcp/connections/{c2['id']}/test", {})
        check(t1["ok"] and t2["ok"], f"iki yönlü MCP bağlantısı: UnixAI→MorpheusAI ({len(t1['tools'])} araç), MorpheusAI→UnixAI ({len(t2['tools'])} araç)")

        print("\n[Ajanlar konuşuyor]")
        r = call(UX, "/chat", {"messages": [{"role": "user", "content": "rhel-db01'in P1 bulgusunu doğrula ve MorpheusAI'den düzeltme iste"}]})
        tr = r["trace"]
        print("  UnixAI ajanı cevabı:", r["reply"][:220].replace("\n", " "))
        check(r["mode"] == "native" and [t["tool"] for t in tr] == ["unixai_get_server_findings", "morpheusai__morpheus_agent_ask"], "UnixAI ajanı: yerel veri aracı → MorpheusAI ajanına soru")
        check(tr[1]["remoteAgent"] and tr[1]["where"] == "morpheusai" and tr[1]["ok"], "ajanlar arası çağrı MCP üzerinden başarılı")
        check(re.search(r"rem-\d{8}-[0-9a-f]{8}", r["reply"]) is not None and "check" in r["reply"], "UnixAI cevabında MorpheusAI'nin oluşturduğu kayıt ve check bilgisi var")
        check("morpheusai__morpheus_agent_ask" in seen["unixai_tools"][0], "UnixAI ajanı MorpheusAI'nin araçlarını görüyor")
        check("unixai__unixai_agent_ask" not in seen["morpheus_tools"][0] and "unixai__unixai_get_server_findings" in seen["morpheus_tools"][0],
              "hop sınırı: MorpheusAI ajanı UnixAI'nin veri araçlarını görüyor, ajanına geri soru soramıyor")
        mconv = call(MP, "/agent/conversations")[0]
        check(mconv["origin"] == "unixai-os" and mconv["hop"] == 1 and mconv["tools"] == ["unixai__unixai_get_server_findings", "morpheus_create_remediation", "morpheus_launch_remediation"],
              "MorpheusAI ajanı: UnixAI'den veri aldı → playbook üretti → check başlattı (konuşma kaydı)")
        check(len(seen["aap"]) == 1 and seen["aap"][0]["job_type"] == "check" and seen["aap"][0]["limit"] == "rhel-db01", "AAP'de yalnızca check (dry-run) başlatıldı, hedef rhel-db01")
        rems = call(MP, "/remediations")["items"]
        check(len(rems) == 1 and rems[0]["compliance"]["score"] == 95 and rems[0]["status"] == "check_running" and not rems[0]["approval"],
              "düzeltme kaydı: uyum 95, check çalışıyor, gerçek uygulama için onay bekliyor")
        uconv = call(UX, f"/agent/conversations/{r['conversationId']}")
        check(uconv["trace"][1]["result"] and "rem-" in uconv["trace"][1]["result"], "UnixAI konuşma kaydında MorpheusAI ajanının cevabı saklandı")
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
