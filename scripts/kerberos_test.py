"""Trinity · Kerberos testi:  python3 scripts/kerberos_test.py
  sahte kinit / klist (fake_krb.py) + sahte ssh (fake_ssh_krb.py) → ayarlar, bilet önbelleği, ssh argümanları, dzdo, hata mesajları, API
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
fails = 0


def check(cond, msg):
    global fails
    print(("  ✓ " if cond else "  ✕ ") + msg)
    fails += 0 if cond else 1


tmp = Path(tempfile.mkdtemp(prefix="trinity-krb-"))
data, log = tmp / "data", tmp / "ssh.jsonl"
data.mkdir()
keytab = tmp / "trinity.keytab"
keytab.write_text("anahtar")
py = json.dumps
FAKE = {"UNIXAI_SSH_BIN": sys.executable, "UNIXAI_SSH_PREFIX": py([str(ROOT / "scripts" / "fake_ssh_krb.py")]),
        "UNIXAI_KINIT_BIN": sys.executable, "UNIXAI_KINIT_PREFIX": py([str(ROOT / "scripts" / "fake_krb.py"), "kinit"]),
        "UNIXAI_KLIST_BIN": sys.executable, "UNIXAI_KLIST_PREFIX": py([str(ROOT / "scripts" / "fake_krb.py"), "klist"]),
        "FAKE_SSH_LOG": str(log), "UNIXAI_DATA_DIR": str(data), "LOG_DIR": str(tmp / "logs"), "PYTHONIOENCODING": "utf-8"}
os.environ.update(FAKE)
sys.path.insert(0, str(ROOT))
from unixai import collector, inventory, kerberos, settings  # noqa: E402
from unixai.errors import ApiError  # noqa: E402


def calls():
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def kinits():
    c = data / "krb5cc_trinity.count"
    return int(c.read_text()) if c.exists() else 0


print("Ayarlar")
for bad, why in [({"auth": "parola"}, "bilinmeyen kimlik doğrulama"), ({"privilege": "su"}, "bilinmeyen yetki yükseltme"),
                 ({"auth": "kerberos", "krbPrincipal": "svc-trinity@kurum.local", "krbKeytab": str(keytab)}, "küçük harfli alan adı"),
                 ({"auth": "kerberos", "krbPrincipal": "svc-trinity@KURUM.LOCAL", "krbKeytab": "trinity.keytab"}, "göreli keytab yolu")]:
    try:
        settings.save({"ssh": bad})
        check(False, f"reddedilmeli: {why}")
    except ApiError:
        check(True, f"reddedildi: {why}")
check(settings.get()["ssh"]["auth"] == "key", "varsayılan kimlik doğrulama: anahtar")
s = settings.save({"ssh": {"auth": "kerberos", "user": "svc-trinity", "krbPrincipal": "svc-trinity@KURUM.LOCAL", "krbKeytab": str(keytab),
                           "privilege": "dzdo", "hostKeyPolicy": "accept-new"}})["ssh"]
check(s["auth"] == "kerberos" and s["privilege"] == "dzdo", "Kerberos + dzdo kaydedildi")
try:
    inventory.validate({"name": "x", "host": "x.kurum.local", "auth": "parola"})
    check(False, "envanterde bilinmeyen auth reddedilmeli")
except ApiError:
    check(True, "envanterde bilinmeyen auth reddedildi")

print("Bağlantı ve toplama")
srv = inventory.validate({"name": "rhel-db01", "host": "rhel-db01.kurum.local"})
t = collector.test(srv, s)
c = calls()[-1]
check(t["ok"] and "user=svc-trinity" in t["output"] and "dzdo -n çalışıyor" in t["output"], f"SSH testi Kerberos ile geçti: {t.get('output') or t.get('error')}")
check("GSSAPIAuthentication=yes" in c["args"] and "GSSAPIDelegateCredentials=no" in c["args"] and "-i" not in c["args"],
      "ssh: GSSAPI açık, delegation kapalı, anahtar verilmedi")
check(c["ccache"] == kerberos.ccache() and str(data) in c["ccache"], "ssh Trinity'nin kendi bilet önbelleğiyle çalıştı")
check(kinits() == 1, "keytab ile bir kez kinit")
r = collector.collect_one(srv, s)
check(r["ok"] and not r["partial"] and "journal_errors" in r["sections"], "toplama tamamlandı")
check('SUDO="dzdo -n"' in calls()[-1]["stdin"] and "__PRIV__" not in calls()[-1]["stdin"], "toplama betiği dzdo -n kullanıyor")
check(kinits() == 1, "geçerli bilet tekrar kullanıldı (yeni kinit yok)")
kerberos._state["at"] = time.time() - kerberos.RENEW_AFTER - 1
collector.collect_one(srv, s)
check(kinits() == 2, "eski bilet yenilendi")
(data / "krb5cc_trinity").unlink()
collector.collect_one(srv, s)
check(kinits() == 3, "silinen bilet yeniden alındı")

print("Karışık envanter ve hata mesajları")
key_srv = inventory.validate({"name": "eski01", "host": "10.0.0.5", "auth": "key"})
collector.test(key_srv, s)
c = calls()[-1]
check("-i" in c["args"] and "GSSAPIAuthentication=yes" not in c["args"], "sunucu bazında anahtar: -i ile, GSSAPI'siz")
e = collector.test(inventory.validate({"name": "ipli", "host": "10.0.0.6"}), s)
check(not e["ok"] and "FQDN" in e["error"], f"IP adresi → FQDN uyarısı: {e.get('error')}")
yok = {**s, "krbPrincipal": "yok@KURUM.LOCAL"}
e = collector.test(srv, yok)
check(not e["ok"] and e["error"].startswith("Kerberos:") and "bulunamadı" in e["error"], f"AD'de olmayan hesap: {e.get('error')}")
keytab.write_text("eski")
e = collector.test(srv, {**s, "krbPrincipal": "svc2@KURUM.LOCAL"})
check(not e["ok"] and "Keytab eski" in e["error"], f"eski keytab: {e.get('error')}")
keytab.write_text("anahtar")
e = collector.test(srv, {**s, "krbKeytab": str(tmp / "yok.keytab")})
check(not e["ok"] and "Keytab bulunamadı" in e["error"], "olmayan keytab")
check(collector.collect_script({"privilege": "sudo"}).count('SUDO="sudo -n"') == 1, "anahtar modunda varsayılan sudo -n")

print("API")
kerberos._state.update(at=0.0, key=None, error=None)
port = 3917
p = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=str(ROOT), stdout=open(tmp / "server.out", "wb"), stderr=subprocess.STDOUT,
                     env={**os.environ, "PORT": str(port), "HOST": "127.0.0.1", "ADMIN_PASSWORD": "", "LDAP_SERVER": "", "PIXEL_OFFICE": "false"})


def api(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


try:
    for _ in range(60):
        try:
            h = api("GET", "/health")
            break
        except Exception:
            time.sleep(0.2)
    check(h["ssh"]["auth"] == "kerberos" and h["ssh"]["kerberos"]["principal"] == "svc-trinity@KURUM.LOCAL", "health → ssh.kerberos")
    r = api("POST", "/settings/kerberos/renew", {})
    check(r["ok"] and r["valid"] and r["expires"] and r["principal"] == "svc-trinity@KURUM.LOCAL" and r["hostTickets"], f"bileti şimdi al: bitiş {r.get('expires')}")
    k = api("GET", "/servers")["sshKey"]
    check(k["auth"] == "kerberos" and k["kerberos"]["valid"] and k["privilege"] == "dzdo", "Sunucular → kimlik kartı Kerberos durumunu gösteriyor")
    logs = "".join(x.read_text(encoding="utf-8") for x in (tmp / "logs").rglob("*.jsonl"))
    check("Kerberos bileti alındı" in logs and '"auth": "kerberos"' in logs, "oplog: bilet alımı ve SSH kimlik türü kaydedildi")
finally:
    p.terminate()

print("\nTÜMÜ GEÇTİ" if not fails else f"\n{fails} KONTROL BAŞARISIZ")
sys.exit(1 if fails else 0)
