"""Trinity · bildirim testi:  python3 scripts/notify_test.py
  sahte webhook (Teams / genel) + sahte SMTP sunucusu → server.py API'si ve olay biçimleri
"""
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from email import message_from_bytes
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
fails = 0
hooks, mails = [], []


def check(cond, msg):
    global fails
    print(f"  {'OK ' if cond else 'FAIL'} {msg}", flush=True)
    fails += 0 if cond else 1


class Hook(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        hooks.append({"path": self.path, "body": json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))})
        self.send_response(202 if self.path.startswith("/ok") else 500)
        self.send_header("content-length", "0")
        self.end_headers()


class Smtp(socketserver.StreamRequestHandler):
    """En küçük SMTP: EHLO/MAIL/RCPT/DATA/QUIT"""

    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())  # noqa: E731
        w("220 sahte-smtp")
        rcpt, data = [], None
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd = line.decode("utf-8", "replace").strip()
            up = cmd.upper()
            if data is not None:
                if cmd == ".":
                    mails.append({"rcpt": rcpt, "msg": message_from_bytes(b"".join(data), policy=email_policy)})
                    data = None
                    w("250 kabul")
                else:
                    data.append(line[1:] if line.startswith(b"..") else line)
            elif up.startswith(("EHLO", "HELO")):
                w("250 sahte-smtp")
            elif up.startswith("MAIL"):
                w("250 ok")
            elif up.startswith("RCPT"):
                rcpt.append(cmd.split(":", 1)[1].strip(" <>"))
                w("250 ok")
            elif up == "DATA":
                data = []
                w("354 devam")
            elif up == "QUIT":
                w("221 bye")
                return
            else:
                w("250 ok")


hook = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Smtp)
for s in (hook, smtp):
    threading.Thread(target=s.serve_forever, daemon=True).start()
H, P = f"http://127.0.0.1:{hook.server_address[1]}", smtp.server_address[1]

tmp = Path(tempfile.mkdtemp(prefix="trinity-notify-"))
data = tmp / "data"
data.mkdir()
port = 3913
env = {**os.environ, "UNIXAI_DATA_DIR": str(data), "LOG_DIR": str(tmp / "logs"), "PORT": str(port), "HOST": "127.0.0.1", "ADMIN_PASSWORD": "",
       "LDAP_SERVER": "", "PIXEL_OFFICE": "false", "PYTHONIOENCODING": "utf-8", "NOTIFY_NO_PROXY": "true"}
srv_out = open(tmp / "server.out", "wb")
srv = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=str(ROOT), env=env, stdout=srv_out, stderr=subprocess.STDOUT)


def api(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", "x-unixai-user": "EnisA"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait(pred, sec=10):
    for _ in range(sec * 20):
        if pred():
            return True
        time.sleep(0.05)
    return False


try:
    for _ in range(60):
        try:
            if api("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.2)
    print("Ayarlar")
    st, n = api("GET", "/integrations/notify")
    check(st == 200 and n["active"] == [] and n["events"]["runSummary"], "varsayılan: kanal yok, olaylar açık")
    check(api("POST", "/integrations/notify/test", {})[1].get("ok") is False, "kanal yokken test anlamlı hata verir")
    check(api("POST", "/integrations/notify", {"webhook": {"url": "ftp://x", "type": "teams"}})[0] == 400, "http(s) olmayan webhook reddedilir")
    check(api("POST", "/integrations/notify", {"email": {"to": "kotu-adres", "security": "none"}})[0] == 400, "geçersiz e-posta reddedilir")
    st, n = api("POST", "/integrations/notify", {"webhook": {"enabled": True, "type": "generic", "url": f"{H}/ok/gizli-imza-123"},
                                                 "email": {"enabled": True, "host": "127.0.0.1", "port": P, "security": "none", "from": "trinity@kurum.local",
                                                           "to": "linux@kurum.local; guvenlik@kurum.local"},
                                                 "appUrl": "https://trinity.kurum.local/"})
    check(st == 200 and n["webhook"]["url"] == n["mask"] and n["active"] == ["webhook", "email"] and n["email"]["to"] == ["linux@kurum.local", "guvenlik@kurum.local"],
          f"kaydedildi: adres maskeli, iki kanal etkin {n.get('error', '')}")
    st, n = api("POST", "/integrations/notify", {**n, "webhook": {**n["webhook"], "url": n["mask"]}})
    raw = json.loads((data / "integrations.json").read_text(encoding="utf-8"))["notify"]
    check(raw["webhook"]["url"].endswith("/ok/gizli-imza-123") and raw["appUrl"] == "https://trinity.kurum.local", "maske ile kayıt eski adresi korur, adres sonundaki / atılır")
    check(api("GET", "/health")[1]["notify"]["channels"] == ["webhook", "email"], "health → notify.channels")

    print("Test bildirimi")
    st, t = api("POST", "/integrations/notify/test", {})
    check(st == 200 and t["ok"] and t["result"] == {"webhook": "ok", "email": "ok"}, f"webhook + e-posta gönderildi {t}")
    check(hooks and hooks[-1]["body"]["event"] == "test" and hooks[-1]["body"]["link"] == "https://trinity.kurum.local/#/dashboard", "genel webhook gövdesi ve bağlantı")
    m = mails[-1] if mails else None
    check(m and m["rcpt"] == ["linux@kurum.local", "guvenlik@kurum.local"] and m["msg"]["Subject"] == "[Trinity] Trinity bildirim testi", "e-posta alıcıları ve konu")
    check(m and m["msg"].get_body(("html",)) is not None and "Trinity'de aç" in m["msg"].get_body(("html",)).get_content(), "e-postada HTML gövde ve düğme")
    api("POST", "/integrations/notify", {"webhook": {"enabled": True, "type": "teams", "url": f"{H}/hata"}, "email": {"enabled": False}})
    st, t = api("POST", "/integrations/notify/test", {})
    check(not t["ok"] and "500" in t["result"]["webhook"], f"webhook hatası raporlanır: {t['result']}")
    logs = "".join(x.read_text(encoding="utf-8") for x in (tmp / "logs").rglob("*.jsonl"))
    check("gizli-imza-123" not in logs and (tmp / "logs" / "bildirim").exists(), "webhook adresi loglara yazılmadı; logs/bildirim/ oluştu")
    check(api("GET", "/integrations/notify")[1]["last"]["event"] == "test", "son bildirim kaydedildi")

    print("Olay biçimleri (Teams kartı)")
    sys.path.insert(0, str(ROOT))
    os.environ.update(UNIXAI_DATA_DIR=str(data), LOG_DIR=str(tmp / "logs"), NOTIFY_NO_PROXY="true")
    api("POST", "/integrations/notify", {"webhook": {"enabled": True, "type": "teams", "url": f"{H}/ok/teams"}, "email": {"enabled": False}})
    from unixai import notify

    act = lambda t, p: {"title": t, "priority": p}  # noqa: E731
    results = [{"name": "web01", "analysis": {"status": "CRITICAL", "health_score": 41, "action_items": [act("Disk %97 dolu", "P1"), act("NTP", "P3")]}},
               {"name": "db02", "analysis": {"status": "WARNING", "health_score": 72, "action_items": [act("swap", "P2")]}}, None]
    run = {"trigger": "schedule", "user": "zamanlayıcı", "status": "partial", "fleet": {"fleet_summary": "2 sunucu analiz edildi; 1 kritik."},
           "servers": [{"name": "web01", "collect": "done"}, {"name": "db02", "collect": "done"}, {"name": "bad7", "collect": "failed"}]}
    n0 = len(hooks)
    notify.run_finished(run, results)
    check(wait(lambda: len(hooks) > n0), "toplama özeti gönderildi")
    card = hooks[-1]["body"]["attachments"][0]["content"]
    texts = json.dumps(card, ensure_ascii=False)
    check(card["type"] == "AdaptiveCard" and card["body"][0]["color"] == "attention" and "1 sunucu kritik, 1 P1 aksiyon" in card["body"][0]["text"], f"Teams kartı: başlık ve renk ({card['body'][0]['text']})")
    check("web01: Disk %97 dolu" in texts and "bad7" in texts and "56/100" in texts and card["actions"][0]["url"].endswith("/#/dashboard"), "P1 satırı, toplanamayan, ortalama sağlık, bağlantı")
    api("POST", "/integrations/notify", {"events": {"runSummary": False, "critical": True, "approvals": True, "vulns": True}})
    n0 = len(hooks)
    notify.run_finished({**run, "status": "done", "servers": run["servers"][:2]}, [None, {"name": "db02", "analysis": {"status": "OK", "health_score": 95, "action_items": []}}])
    time.sleep(0.5)
    check(len(hooks) == n0, "özet kapalıyken sağlıklı toplama bildirim göndermez")
    notify.run_finished(run, results)
    check(wait(lambda: len(hooks) > n0), "özet kapalıyken de kritik durum bildirilir")
    n0 = len(hooks)
    notify.approval_pending({"server": "web01", "title": "Diski temizle", "priority": "P1", "sentBy": "EnisA", "source": "trinity"})
    check(wait(lambda: len(hooks) > n0) and "onayınızı bekliyor: web01" in json.dumps(hooks[-1]["body"], ensure_ascii=False), "onay bekleyen playbook bildirildi")
    n0 = len(hooks)
    notify.approvals_batch([{"server": "web01", "title": "Disk"}, {"server": "db02", "title": "Swap"}, {"server": "web01", "title": "NTP"}])
    check(wait(lambda: len(hooks) > n0) and "3 playbook onayınızı bekliyor" in json.dumps(hooks[-1]["body"], ensure_ascii=False), "otomatik gönderimde tek toplu onay bildirimi")
    time.sleep(0.3)
    check(len(hooks) == n0 + 1, "toplu bildirim tek mesaj")
    n0 = len(hooks)
    notify.vulns_scanned({"totals": {"hosts": 5, "withVulns": 2, "counts": {"Critical": 0, "Important": 3}, "overdue": 0}, "items": []})
    time.sleep(0.5)
    check(len(hooks) == n0, "Critical / SLA aşımı yoksa zafiyet bildirimi gitmez")
    notify.vulns_scanned({"totals": {"hosts": 5, "withVulns": 2, "counts": {"Critical": 2, "Important": 3}, "overdue": 4, "installable": 3, "cves": 9},
                          "items": [{"key": "web01", "summary": {"counts": {"Critical": 2}, "overdue": 3}}]})
    check(wait(lambda: len(hooks) > n0) and "2 Critical, 4 SLA aşan" in json.dumps(hooks[-1]["body"], ensure_ascii=False), "zafiyet taraması: Critical / SLA aşımı bildirildi")
    api("POST", "/integrations/notify", {"webhook": {"enabled": True, "type": "slack", "url": f"{H}/ok/slack"}})
    n0 = len(hooks)
    notify.approval_pending({"server": "db02", "title": "x"})
    check(wait(lambda: len(hooks) > n0) and hooks[-1]["body"]["text"].startswith(":large_orange_circle: *Playbook onayınızı bekliyor: db02*"), "Slack biçimi")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
        srv_out.close()
        out = (tmp / "server.out").read_bytes().decode("utf-8", "replace")
    except Exception:
        out = ""
    if fails or sys.exc_info()[0]:
        print(out[-3000:])
    hook.shutdown()
    smtp.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'TÜMÜ GEÇTİ' if not fails else f'{fails} HATA'}")
sys.exit(1 if fails else 0)
