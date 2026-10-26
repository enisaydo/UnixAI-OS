"""Agentless toplayıcı: hedef sunucuya hiçbir şey kurmadan OpenSSH ile salt-okunur komutlar çalıştırır.

Tek bir SSH oturumunda, stdin üzerinden bir bash betiği gönderilir (ssh ... bash -s); her komut çıktısı
`===UNIXAI:<bölüm>===` işaretleriyle ayrılır. Hedefte dosya bırakılmaz.
Sistemdeki `ssh` istemcisi kullanılır (RHEL: openssh-clients); UNIXAI_SSH_BIN ile değiştirilebilir.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

MARKER = "===UNIXAI:"
SSH_BIN = os.environ.get("UNIXAI_SSH_BIN", "ssh")
SSH_PREFIX = json.loads(os.environ.get("UNIXAI_SSH_PREFIX", "[]"))  # test: UNIXAI_SSH_BIN=python, UNIXAI_SSH_PREFIX='["scripts/fake_ssh.py"]'

# Varsayılan olarak root ile bağlanılır. Root değilse ayrıcalıklı komutlar `sudo -n` ile çalışır
# (parola istenirse beklemek yerine hata verir). Hepsi salt-okunur komutlardır.
# Gövde tek fonksiyonda: bash onu tamamen okuduktan sonra stdin'i /dev/null'a bağlayıp çalıştırır.
# Aksi halde stdin'den okuyan komutlar (ör. ausearch) betiğin geri kalanını yutar.
COLLECT_SCRIPT = r"""
unixai_collect() {
export LC_ALL=C
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo -n"; fi
sec(){ echo "===UNIXAI:$1==="; }
sec hostname;        hostname -f 2>/dev/null || hostname
sec os_release;       cat /etc/redhat-release 2>/dev/null
sec kernel;           uname -r
sec uptime_seconds;   cut -d. -f1 /proc/uptime
sec nproc;            nproc
sec loadavg;          cat /proc/loadavg
sec meminfo;          grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
sec df;               df -PTk -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null
sec df_inode;         df -PTi -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null
sec vmstat;           vmstat 1 5
sec failed_units;     systemctl --failed --no-legend --plain 2>/dev/null
sec journal_errors;   $SUDO journalctl -p err --since "24 hours ago" --no-pager -q -o short-iso 2>/dev/null | tail -n 400
sec ssh_failures;     $SUDO journalctl _COMM=sshd --since "24 hours ago" --no-pager -q -o short-iso 2>/dev/null | grep -E 'Failed password|Invalid user|authentication failure' | tail -n 5000
sec kernel_errors;    $SUDO journalctl -k -p warning --since "24 hours ago" --no-pager -q -o short-iso 2>/dev/null | tail -n 300
sec selinux;          getenforce 2>/dev/null
sec avc_denials;      $SUDO ausearch --input-logs -m avc -ts yesterday 2>/dev/null | grep -c 'type=AVC'
sec chrony;           chronyc -n tracking 2>/dev/null
sec security_updates; (dnf -q -C updateinfo list --security 2>/dev/null || yum -q -C updateinfo list security 2>/dev/null) | grep -c -E 'RHSA|RLSA|ALSA'
sec needs_restarting; $SUDO needs-restarting -r >/dev/null 2>&1; echo $?
sec top_processes;    ps -eo pid,user,pcpu,pmem,rss,comm --sort=-pcpu --no-headers | head -n 10
sec zombies;          ps -eo stat --no-headers | grep -c '^Z'
sec end
}
unixai_collect </dev/null
"""

SECTIONS = ["hostname", "os_release", "kernel", "uptime_seconds", "nproc", "loadavg", "meminfo", "df", "df_inode",
            "vmstat", "failed_units", "journal_errors", "ssh_failures", "kernel_errors", "selinux", "avc_denials", "chrony",
            "security_updates", "needs_restarting", "top_processes", "zombies"]


def split_sections(raw: str):
    sections: dict = {}
    current, buf = None, []
    for line in raw.splitlines():
        if line.startswith(MARKER) and line.endswith("==="):
            if current:
                sections[current] = "\n".join(buf).strip()
            current, buf = line[len(MARKER):-3], []
        else:
            buf.append(line)
    complete = "end" in sections or current == "end"
    sections.pop("end", None)
    return sections, complete


def ssh_args(server: dict, ssh: dict, remote_command: str) -> list:
    return [
        "-o", "BatchMode=yes",
        "-o", f"StrictHostKeyChecking={'accept-new' if ssh['hostKeyPolicy'] == 'accept-new' else 'yes'}",
        "-o", f"ConnectTimeout={ssh['connectTimeout']}",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "LogLevel=ERROR",
        "-o", "IdentitiesOnly=yes",
        "-i", server.get("keyFile") or ssh["keyFile"],
        "-p", str(server.get("port") or ssh["port"]),
        "-l", server.get("user") or ssh["user"],
        "--", server["host"], remote_command,
    ]


def run_ssh(server: dict, ssh: dict, remote_command: str, stdin: str = "", purpose: str = "komut") -> dict:
    """ssh çalıştırır (kabuk yok) ve logs/ssh/ dosyasına yazar. {code, stdout, stderr, durationMs, timedOut}"""
    r = _run_ssh(server, ssh, remote_command, stdin)
    from . import oplog

    failed = r["timedOut"] or r["code"] != 0
    oplog.log("ssh", f"SSH {purpose}", server=server.get("name"), host=server.get("host"), sshUser=server.get("user") or ssh["user"],
              port=server.get("port") or ssh["port"], command=remote_command if remote_command != "bash -s" else f"bash -s ({purpose} betiği)",
              exitCode=r["code"], durationMs=r["durationMs"], timedOut=r["timedOut"] or None, stdoutBytes=len(r["stdout"] or ""),
              stderr=(r["stderr"] or "")[-500:] if failed and r["stderr"] else None,
              ok=not failed, error=("zaman aşımı" if r["timedOut"] else f"çıkış kodu {r['code']}") if failed else None, quiet=not failed)
    return r


def _run_ssh(server: dict, ssh: dict, remote_command: str, stdin: str = "") -> dict:
    started = time.monotonic()
    timeout = ssh["connectTimeout"] + ssh["commandTimeout"]
    try:
        p = subprocess.run([SSH_BIN, *SSH_PREFIX, *ssh_args(server, ssh, remote_command)], input=stdin.encode(), capture_output=True,
                           timeout=timeout)
        return {"code": p.returncode, "stdout": p.stdout.decode("utf-8", "replace"), "stderr": p.stderr.decode("utf-8", "replace")[:100000],
                "durationMs": int((time.monotonic() - started) * 1000), "timedOut": False}
    except subprocess.TimeoutExpired as e:
        return {"code": -1, "stdout": (e.stdout or b"").decode("utf-8", "replace"), "stderr": "", "durationMs": int(timeout * 1000), "timedOut": True}
    except FileNotFoundError:
        return {"code": -1, "stdout": "", "stderr": f"'{SSH_BIN}' bulunamadı (openssh-clients kurulu mu?)", "durationMs": 0, "timedOut": False}


def explain(stderr: str, server: dict, ssh: dict) -> str:
    """SSH hata çıktısını kullanıcıya yol gösteren mesaja çevirir"""
    s = stderr.strip()
    user, port, key = server.get("user") or ssh["user"], server.get("port") or ssh["port"], server.get("keyFile") or ssh["keyFile"]
    if re.search(r"Host key verification failed|No .* host key is known", s, re.I):
        return (f"Host key bilinmiyor. Merkez sunucuda: ssh-keyscan -H {server['host']} >> /root/.ssh/known_hosts  "
                "(veya Ayarlar → SSH → host key politikası: accept-new)")
    if re.search(r"REMOTE HOST IDENTIFICATION HAS CHANGED", s, re.I):
        return f"Host key DEĞİŞMİŞ (yeniden kurulum veya MITM). Doğrulayıp eski kaydı silin: ssh-keygen -R {server['host']}"
    if re.search(r"UNPROTECTED PRIVATE KEY FILE|bad permissions", s, re.I):
        return f"Anahtar dosyasının izinleri çok açık, ssh anahtarı kullanmadı. Merkez sunucuda: chmod 600 {key}"
    if re.search(r"no mutual signature algorithm", s, re.I):
        return ("Hedefin OpenSSH sürümü çok eski (ör. RHEL 6): yalnızca SHA1 RSA imzası destekliyor, merkezdeki ssh bunu kapatıyor. "
                "Hedefi güncelleyin veya bu sunucu için ~/.ssh/config'e 'PubkeyAcceptedAlgorithms +ssh-rsa' ekleyin.")
    if re.search(r"passphrase|Load key .*(invalid format|libcrypto)", s, re.I):
        return f"Anahtar okunamadı: {key} parolalı veya bozuk. UnixAI parolasız anahtar ister (BatchMode); ssh-keygen -y -f {key} ile kontrol edin."
    if re.search(r"Too many authentication failures", s, re.I):
        return "Çok fazla anahtar denendi. Hedefte MaxAuthTries düşük olabilir; doğru anahtar dosyası seçili mi kontrol edin."
    if re.search(r"Permission denied", s, re.I):
        return (f"Yetkilendirme reddedildi ({user}@{server['host']}, anahtar {key}). Hedefte kontrol edin: "
                f"1) authorized_keys'te bu anahtarın .pub'ı var mı (ssh-keygen -lf {key}.pub ile parmak izini karşılaştırın) "
                f"2) izinler: chmod 700 ~{user}/.ssh; chmod 600 ~{user}/.ssh/authorized_keys; sahibi {user} "
                f"3) SELinux etiketi: restorecon -Rv ~{user}/.ssh "
                "4) sshd: sshd -T | grep -Ei 'permitrootlogin|pubkeyauthentication|allowusers|authorizedkeysfile' "
                "5) hedefte journalctl -u sshd -n 20 asıl sebebi gösterir.")
    if re.search(r"kex_exchange_identification|Connection closed by|Connection reset", s, re.I):
        return f"{server['host']} bağlantıyı kapattı (sshd AllowUsers/AllowGroups, TCP wrappers, firewall veya MaxStartups). Hedefte journalctl -u sshd -n 20."
    if re.search(r"Could not resolve hostname", s, re.I):
        return f"'{server['host']}' adı çözülemedi (DNS)."
    if re.search(r"Connection refused", s, re.I):
        return f"{server['host']}:{port} bağlantıyı reddetti (sshd çalışıyor mu, firewall?)."
    if re.search(r"timed out", s, re.I):
        return f"{server['host']} bağlantısı zaman aşımına uğradı."
    if re.search(r"Identity file .* not accessible|no such identity", s, re.I):
        return f"SSH anahtar dosyası okunamadı: {key}"
    return " ".join(s.splitlines()[-3:]) or "Bilinmeyen SSH hatası"


def collect_one(server: dict, ssh: dict) -> dict:
    r = run_ssh(server, ssh, "bash -s", COLLECT_SCRIPT, purpose="toplama")
    sections, complete = split_sections(r["stdout"])
    if r["timedOut"]:
        return {"ok": False, "error": f"Zaman aşımı ({ssh['connectTimeout'] + ssh['commandTimeout']} sn)", "sections": sections, "durationMs": r["durationMs"]}
    if not sections:
        return {"ok": False, "error": explain(r["stderr"], server, ssh), "sections": {}, "durationMs": r["durationMs"], "exitCode": r["code"]}
    return {"ok": True, "partial": not complete, "sections": sections, "missing": [k for k in SECTIONS if k not in sections],
            "stderr": r["stderr"].strip()[:2000] or None, "durationMs": r["durationMs"]}


def test(server: dict, ssh: dict) -> dict:
    """Bağlantı testi: kimlik ve OS sürümü"""
    r = run_ssh(server, ssh, "bash -s", 'echo "user=$(id -un) host=$(hostname -f 2>/dev/null || hostname)"; cat /etc/redhat-release 2>/dev/null\n', purpose="bağlantı testi")
    if r["code"] == 0 and "user=" in r["stdout"]:
        return {"ok": True, "output": r["stdout"].strip(), "durationMs": r["durationMs"]}
    return {"ok": False, "error": "Zaman aşımı" if r["timedOut"] else explain(r["stderr"], server, ssh), "durationMs": r["durationMs"]}


def collect_all(servers: list, ssh: dict, on_each=None) -> list:
    """Sınırlı eş zamanlılıkla toplar; her sonuçta on_each(result) çağrılır"""
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(ssh["concurrency"], len(servers) or 1))) as pool:
        futures = {pool.submit(collect_one, s, ssh): s for s in servers}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                r = {"server": s["name"], "host": s["host"], **fut.result()}
            except Exception as e:  # beklenmeyen hata tek sunucuyu etkilesin
                r = {"server": s["name"], "host": s["host"], "ok": False, "error": str(e), "sections": {}}
            results.append(r)
            if on_each:
                on_each(r)
    return results
