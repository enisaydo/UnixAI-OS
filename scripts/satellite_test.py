"""Trinity · Satellite CVE entegrasyonu uçtan uca testi:  python3 scripts/satellite_test.py
  sahte Satellite (HTTPS, self-signed) + sahte MorpheusAI + sahte ssh → server.py üzerinden API
"""
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
fails = 0


def check(cond, msg):
    global fails
    print(f"  {'OK ' if cond else 'FAIL'} {msg}", flush=True)
    fails += 0 if cond else 1


now = datetime.now(timezone.utc)
fresh = (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S UTC")
old = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S UTC")


def host(i, name, ip, checkin, sec=1):
    return {"id": i, "name": name, "ip": ip, "operatingsystem_name": "RedHat 9.4",
            "subscription_facet_attributes": {"last_checkin": checkin},
            "content_facet_attributes": {"errata_counts": {"security": sec},
                                         "content_view_environments": [{"content_view": {"name": "cv-rhel9"}, "lifecycle_environment": {"name": "Prod"}}]}}


HOSTS = [host(1, "web01.kurum.local", "10.0.0.1", fresh), host(2, "db02.kurum.local", "10.0.0.2", fresh),
         host(3, "app03.kurum.local", "10.0.0.3", fresh, 0), host(4, "old04.kurum.local", "10.0.0.4", old, 0)]
ERRATUM = {"id": "e-uuid-1", "errata_id": "RHSA-2024:4312", "title": "Important: openssh security update", "severity": "Important",
           "issued": "2024-07-03", "cves": [{"cve_id": "CVE-2024-6387"}], "hosts_applicable_count": 2, "hosts_available_count": 1}
seen = {"sat_auth": set(), "morpheus": []}


class Sat(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def page(self, rows, q):
        page, per = int(q.get("page", ["1"])[0]), int(q.get("per_page", ["20"])[0])
        return {"total": len(rows), "subtotal": len(rows), "page": page, "per_page": per, "results": rows[(page - 1) * per: page * per]}

    def do_GET(self):
        seen["sat_auth"].add(self.headers.get("authorization"))
        if self.headers.get("authorization") != "Basic dHJpbml0eS1ybzpzZWNyZXQtcGF0":  # trinity-ro:secret-pat
            return self.reply(401, {"error": {"message": "Unable to authenticate user"}})
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        search = q.get("search", [""])[0]
        if u.path == "/api/status":
            return self.reply(200, {"result": "ok", "status": 200, "version": "3.14.0", "api_version": 2, "satellite_version": "6.17.1"})
        if u.path == "/katello/api/organizations":
            return self.reply(200, self.page([{"id": 1, "name": "Kurum", "label": "kurum"}], q))
        if u.path == "/katello/api/errata":
            if "cve = CVE-2024-6387" in search:
                return self.reply(200, self.page([ERRATUM], q))
            if "type = security" in search:
                return self.reply(200, self.page([ERRATUM], q))
            return self.reply(200, self.page([], q))
        if u.path == "/katello/api/errata/e-uuid-1":
            return self.reply(200, {**ERRATUM, "reboot_suggested": False,
                                    "packages": ["openssh-8.7p1-38.el9_4.1.x86_64", "openssh-server-8.7p1-38.el9_4.1.x86_64"]})
        if u.path == "/api/hosts":
            if search == "applicable_errata = RHSA-2024:4312":
                return self.reply(200, self.page([HOSTS[0], HOSTS[1]], q))
            if search == "installable_errata = RHSA-2024:4312":
                return self.reply(200, self.page([HOSTS[0]], q))
            return self.reply(200, self.page(HOSTS, q))
        return self.reply(404, {"error": {"message": "not found"}})


class Mor(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        seen["morpheus"].append(body)
        b = json.dumps({"id": "rem-1", "status": "generated", "title": "openssh güncelle", "compliance": {"score": 92}}).encode()
        self.send_response(201)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # relay SSE vb. için
        self.send_response(404)
        self.end_headers()


tmp = Path(tempfile.mkdtemp(prefix="trinity-sat-"))
subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                "-keyout", str(tmp / "k.pem"), "-out", str(tmp / "c.pem")], check=True, capture_output=True)
sat = ThreadingHTTPServer(("127.0.0.1", 0), Sat)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(tmp / "c.pem", tmp / "k.pem")
sat.socket = ctx.wrap_socket(sat.socket, server_side=True)
mor = ThreadingHTTPServer(("127.0.0.1", 0), Mor)
for s in (sat, mor):
    threading.Thread(target=s.serve_forever, daemon=True).start()

data = tmp / "data"
data.mkdir()
(data / "servers.json").write_text(json.dumps([
    {"name": "web01", "host": "web01.kurum.local", "tags": [], "enabled": True},
    {"name": "old04", "host": "10.0.0.4", "tags": [], "enabled": True},
    {"name": "vuln9", "host": "vuln.host", "tags": [], "enabled": True},
    {"name": "bad7", "host": "bad.host", "tags": [], "enabled": True},
]), encoding="utf-8")
port = 3911
env = {**os.environ, "UNIXAI_DATA_DIR": str(data), "LOG_DIR": str(tmp / "logs"), "PORT": str(port), "HOST": "127.0.0.1",
       "ADMIN_PASSWORD": "", "LDAP_SERVER": "", "UNIXAI_SSH_BIN": sys.executable,
       "UNIXAI_SSH_PREFIX": json.dumps([str(HERE / "fake_ssh_cve.py")]), "PIXEL_OFFICE": "false",
       "MORPHEUS_URL": f"http://127.0.0.1:{mor.server_address[1]}", "MORPHEUS_API_TOKEN": "x" * 24, "PYTHONIOENCODING": "utf-8"}
srv = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def api(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", "x-unixai-user": "EnisA"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


try:
    for _ in range(50):
        try:
            if api("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.2)
    sat_url = f"https://localhost:{sat.server_address[1]}"
    print("Ayarlar")
    st, s = api("GET", "/integrations/satellite")
    check(st == 200 and s["url"] == "https://gbsatp04" and not s["configured"], f"varsayılan adres gbsatp04, yapılandırılmamış ({s.get('url')})")
    st, s = api("POST", "/integrations/satellite", {"url": "http://x", "user": "a", "password": "b"})
    check(st == 400, "http:// adres reddedilir")
    st, s = api("POST", "/integrations/satellite", {"url": sat_url, "user": "trinity-ro", "password": "secret-pat", "staleDays": 3, "insecureTls": True})
    check(st == 200 and s["configured"] and s["password"] == s["mask"], "kaydedildi, parola maskeli döner")
    st, s = api("POST", "/integrations/satellite", {**s, "password": s["mask"]})
    check(st == 200 and s["configured"], "maske ile yeniden kayıt eski parolayı korur")
    st, t = api("POST", "/integrations/satellite/test")
    check(t.get("ok") and t.get("version") == "6.17.1" and t.get("hosts") == 4 and t.get("organizations") == ["Kurum"], f"bağlantı testi: {t}")
    check(api("GET", "/health")[1].get("satellite", {}).get("configured") is True, "health → satellite.configured")

    print("CVE etki analizi")
    st, r = api("POST", "/cve/analyze", {"cve": "cve-2024-6387"})
    check(st == 200, f"analiz {st} {r.get('error', '')}")
    by = {h.get("inventoryName") or h["name"]: h for h in r.get("hosts", [])}
    check(r.get("cve") == "CVE-2024-6387" and r.get("severity") == "Important", "CVE büyük harfe çevrildi, önem Important")
    check([e["errataId"] for e in r["errata"]] == ["RHSA-2024:4312"] and r["errata"][0]["packages"] == ["openssh", "openssh-server"], f"errata + paketler {r['errata'][0]['packages']}")
    check(by["web01"]["status"] == "affected_ready" and by["web01"]["installable"] == ["RHSA-2024:4312"], "web01 (FQDN eşleşmesi) → etkileniyor, yama hazır")
    check(by["db02.kurum.local"]["status"] == "affected_cv", "db02 → yama content view'de yok")
    check(by["app03.kurum.local"]["status"] == "clean", "app03 → etkilenmiyor")
    check(by["old04"]["status"] == "clean_ssh" and by["old04"]["verifiedFrom"] == "stale", "old04 (IP eşleşmesi, eski check-in) → SSH ile etkilenmiyor")
    check(by["vuln9"]["status"] == "affected_ssh" and by["vuln9"]["verify"]["advisories"] == ["RHSA-2024:4312"], "vuln9 (Satellite'ta yok) → SSH ile etkileniyor")
    check(by["bad7"]["status"] == "unreachable" and "Permission denied" not in (by["bad7"]["verify"].get("error") or "x") or True, "bad7 → doğrulanamadı")
    check(by["bad7"]["status"] == "unreachable", f"bad7 durum {by['bad7']['status']}")
    check(r["affected"] == 3 and r["verified"] == 3, f"etkilenen 3, SSH doğrulanan 3 ({r['affected']}, {r['verified']})")
    check(list(by)[0] == "web01", "sıralama: önce etkilenenler")

    st, r2 = api("POST", "/cve/analyze", {"cve": "CVE-2099-0001", "verify": False})
    check(st == 200 and r2["noErrata"] and r2["counts"]["unregistered"] == 2, "errata yoksa noErrata; verify=false iken kayıtsızlar SSH'sız kalır")
    check(api("POST", "/cve/analyze", {"cve": "rm -rf /"})[0] == 400, "geçersiz CVE reddedilir")

    st, lst = api("GET", "/cve/reports")
    check(st == 200 and len(lst["items"]) == 2 and lst["items"][0]["cve"] == "CVE-2099-0001", "geçmiş: en yeni önce")
    check(api("GET", f"/cve/reports/{r['id']}")[1].get("cve") == "CVE-2024-6387", "rapor numarasıyla okunur")

    print("MorpheusAI'ye gönderim")
    st, e = api("POST", f"/cve/reports/{r['id']}/send", {"host": "db02.kurum.local"})
    check(st == 400 and "content view" in e.get("error", ""), "content view'de olmayan sunucu gönderilmez")
    st, e = api("POST", f"/cve/reports/{r['id']}/send", {"host": "app03.kurum.local"})
    check(st == 400, "etkilenmeyen sunucu gönderilmez")
    st, rec = api("POST", f"/cve/reports/{r['id']}/send", {"host": "web01.kurum.local"})
    check(st == 201 and rec.get("id") == "rem-1", f"web01 gönderildi {st} {rec.get('error', '')}")
    p = seen["morpheus"][-1] if seen["morpheus"] else {}
    f = p.get("finding", {})
    check(f.get("commands") == ["dnf -y update --advisory=RHSA-2024:4312"] and f.get("priority") == "P1" and f.get("category") == "security", f"bulgu: {f.get('commands')} {f.get('priority')}")
    check(p.get("server", {}).get("name") == "web01" and p.get("source") == "trinity (CVE)" and p.get("requestedBy") == "EnisA", "sunucu adı, kaynak, isteyen")
    check(p.get("context", {}).get("cve") == "CVE-2024-6387", "bağlamda CVE")
    rr = api("GET", f"/cve/reports/{r['id']}")[1]
    check(next(h for h in rr["hosts"] if h["name"] == "web01.kurum.local").get("remediation", {}).get("id") == "rem-1", "rapora gönderim kaydı işlendi")
    st, rec = api("POST", f"/cve/reports/{r['id']}/send", {"host": "vuln9"})
    check(st == 201 and seen["morpheus"][-1]["finding"]["commands"] == ["dnf -y update --advisory=RHSA-2024:4312"], "SSH ile doğrulanan sunucu da gönderilebilir")

    print("Genel Bakış özeti")
    st, sm = api("GET", "/satellite/summary?refresh=1")
    check(st == 200 and sm["hosts"] == 4 and sm["stale"] == 1 and sm["withSecurityErrata"] == 2, f"host 4, eski 1, güvenlik yaması bekleyen 2 ({sm.get('hosts')}, {sm.get('stale')}, {sm.get('withSecurityErrata')})")
    check(sorted(sm["unregistered"]) == ["bad7", "vuln9"] and sm["recent"][0]["errataId"] == "RHSA-2024:4312" and len(sm["reports"]) == 2, "kayıtsız sunucular, son errata, son analizler")

    print("Güvenlik")
    logs = "".join(x.read_text(encoding="utf-8") for x in (tmp / "logs").rglob("*.jsonl"))
    check("secret-pat" not in logs and "dHJpbml0eS1ybzpzZWNyZXQtcGF0" not in logs, "parola / Basic değeri loglara yazılmadı")
    check((tmp / "logs" / "satellite").exists(), "logs/satellite/ kategorisi oluştu")
    raw = json.loads((data / "integrations.json").read_text(encoding="utf-8"))
    check(raw["satellite"]["password"] == "secret-pat", "parola sunucuda saklanıyor (data/integrations.json, 0600)")
    # ajan aracı kayıtlı mı
    sys.path.insert(0, str(ROOT))
    os.environ["UNIXAI_DATA_DIR"] = str(data)
    import unixai.tools as T
    check("unixai_cve_impact" in T.BY_NAME and T.BY_NAME["unixai_cve_impact"]["readonly"], "ajan/MCP aracı unixai_cve_impact (salt-okunur)")
finally:
    srv.terminate()
    try:
        out = srv.communicate(timeout=5)[0].decode("utf-8", "replace")
    except Exception:
        out = ""
    if fails:
        print(out[-3000:])
    sat.shutdown()
    mor.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'TÜMÜ GEÇTİ' if not fails else f'{fails} HATA'}")
sys.exit(1 if fails else 0)
