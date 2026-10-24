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
  kullanıcı (UnixAI ekranı) → Onayla  → MorpheusAI (X-Approved-By) → AAP check (dry-run)
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
GW, AAP, UX, MP = 3951, 3953, 3954, 3955
fails = 0
seen = {"unixai_tools": [], "morpheus_tools": [], "aap": [], "rid": None, "jobs": {}}


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
        jid = int(self.path.rstrip("/").split("/")[-1])
        out = json.dumps({"id": jid, "status": seen["jobs"].get(jid, "pending")}).encode()
        self.send_response(200)
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
        mp_tok = call(MP, "/mcp/tokens", {"name": "unixai", "readonly": False, "canApprove": True})["token"]  # onay aktarabilir
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
        check(a["approval"]["by"] == "EnisA (unixai üzerinden)" and len(seen["aap"]) == 1 and seen["aap"][0]["job_type"] == "check" and seen["aap"][0]["limit"] == "rhel-db01",
              "onay MorpheusAI'ye aktarıldı (onaylayan EnisA) → AAP'de check (dry-run) başladı, hedef rhel-db01")
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
        au = [x for x in call(UX, "/audit")["records"] if "Playbook" in str(x.get("action", ""))]
        check(len(au) >= 2, "onay ve uygulama İşlem Kayıtları'na düştü")
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
