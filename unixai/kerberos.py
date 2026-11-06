"""Kerberos (GSSAPI) ile SSH: hedeflerde anahtar yerine AD servis hesabının bileti

  Ayarlar → SSH → Kimlik doğrulama: Kerberos
    krbPrincipal : svc-trinity@KURUM.LOCAL          (alan adı büyük harf)
    krbKeytab    : /etc/trinity/trinity.keytab      (0600, sahibi Trinity servis kullanıcısı)

Bilet Trinity'ye ait ayrı bir önbellekte tutulur (data/krb5cc_trinity); sistemdeki diğer biletlere karışmaz.
SSH'tan önce bilet RENEW_AFTER saniyeden eskiyse veya geçersizse `kinit -k -t <keytab>` ile yeniden alınır.
Merkezde kinit / klist (krb5-workstation) ve /etc/krb5.conf gerekir; Centrify ile AD'ye bağlı (adjoin) makinede hazırdır.
Hedefler AD'ye bağlı olmalı (Centrify), sshd GSSAPIAuthentication açık olmalı ve sunucuya FQDN ile bağlanılmalı.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .store import file

KINIT_BIN = os.environ.get("UNIXAI_KINIT_BIN", "kinit")
KINIT_PREFIX = json.loads(os.environ.get("UNIXAI_KINIT_PREFIX", "[]"))  # test: UNIXAI_KINIT_BIN=python, UNIXAI_KINIT_PREFIX='["scripts/fake_krb.py","kinit"]'
KLIST_BIN = os.environ.get("UNIXAI_KLIST_BIN", "klist")
KLIST_PREFIX = json.loads(os.environ.get("UNIXAI_KLIST_PREFIX", "[]"))
RENEW_AFTER = int(os.environ.get("KRB_RENEW_AFTER", "1800"))  # AD'de bilet ömrü varsayılan 10 saat
PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9._$-]{1,64}(/[A-Za-z0-9._-]{1,253})?@[A-Z0-9][A-Z0-9.-]{0,252}$")
IP_RE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|[0-9a-fA-F:]*:[0-9a-fA-F:%]*)$")

_lock = threading.Lock()
_state = {"at": 0.0, "key": None, "error": None}


class KerberosError(Exception):
    pass


def ccache() -> str:
    return "FILE:" + str(file("krb5cc_trinity"))


def env() -> dict:
    """ssh / kinit / klist için ortam: Trinity'nin kendi bilet önbelleği"""
    return {**os.environ, "KRB5CCNAME": ccache()}


def _run(cmd: list, timeout=30):
    return subprocess.run(cmd, env={**env(), "LC_ALL": "C"}, capture_output=True, timeout=timeout)


def _valid() -> bool:
    try:
        return _run([KLIST_BIN, *KLIST_PREFIX, "-s"], timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def explain_kinit(stderr: str, principal: str, keytab: str) -> str:
    s = stderr.strip()
    if re.search(r"Client .* not found in Kerberos database", s, re.I):
        return f"AD'de '{principal}' hesabı bulunamadı: hesap adını ve alan adını (büyük harf, ör. KURUM.LOCAL) kontrol edin."
    if re.search(r"Key table entry not found|No key table entry|Keytab contains no suitable keys", s, re.I):
        return f"Keytab bu hesaba ait anahtar içermiyor. Merkezde: klist -kt {keytab} (principal ve KVNO hesapla aynı olmalı)."
    if re.search(r"Preauthentication failed|Password incorrect|Decrypt integrity check failed", s, re.I):
        return "Keytab eski: hesabın parolası keytab üretildikten sonra değişmiş. Keytab'ı yeniden üretin (adkeytab / ktpass)."
    if re.search(r"Clock skew too great", s, re.I):
        return "Merkez sunucu ile AD arasında 5 dakikadan fazla saat farkı var: chronyc tracking ile saati eşitleyin."
    if re.search(r"KDC has no support for encryption type|enctype", s, re.I):
        return "Şifreleme türü uyuşmuyor: AD'de hesapta 'This account supports Kerberos AES 256 bit encryption' işaretli olmalı, keytab AES256 ile üretilmeli."
    if re.search(r"Cannot (find|contact any) KDC|Cannot resolve network address|Cannot find KDC", s, re.I):
        return "AD etki alanı denetleyicisine (KDC) ulaşılamadı: /etc/krb5.conf'ta realm ve DC tanımını, DNS'i ve 88/TCP-UDP erişimini kontrol edin."
    if re.search(r"Permission denied|Bad file|Key table file .* not found|No such file", s, re.I):
        return f"Keytab okunamadı: {keytab} (sahibi Trinity servis kullanıcısı olmalı, chmod 600)."
    if re.search(r"Client's credentials have been revoked|account disabled|locked", s, re.I):
        return f"AD'de '{principal}' hesabı kapalı veya kilitli."
    return "kinit: " + (" ".join(s.splitlines()[-2:]) or "bilinmeyen hata")


def _kinit(principal: str, keytab: str) -> None:
    from . import oplog

    if not principal:
        raise KerberosError("Kerberos hesabı tanımlı değil: Ayarlar → SSH → Kerberos hesabı (ör. svc-trinity@KURUM.LOCAL)")
    if not Path(keytab).is_file():
        raise KerberosError(f"Keytab bulunamadı: {keytab}")
    file().mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        p = _run([KINIT_BIN, *KINIT_PREFIX, "-k", "-t", keytab, principal])
    except FileNotFoundError:
        raise KerberosError(f"'{KINIT_BIN}' bulunamadı: merkez sunucuda dnf install krb5-workstation") from None
    except subprocess.TimeoutExpired:
        raise KerberosError("kinit 30 sn'de yanıt vermedi (KDC erişimi?)") from None
    if p.returncode != 0:
        msg = explain_kinit(p.stderr.decode("utf-8", "replace"), principal, keytab)
        _state.update(at=0.0, key=None, error=msg)
        oplog.log("ssh", "Kerberos bileti alınamadı", principal=principal, keytab=keytab, ok=False, error=msg)
        raise KerberosError(msg)
    _state.update(at=time.time(), key=(principal, keytab), error=None)
    oplog.log("ssh", "Kerberos bileti alındı", principal=principal, ok=True, quiet=True)


def ensure(ssh: dict, force: bool = False) -> None:
    """Geçerli bilet yoksa (veya eskiyse) keytab ile alır; alınamazsa KerberosError"""
    key = (ssh.get("krbPrincipal") or "", ssh.get("krbKeytab") or "")
    with _lock:
        if not force and _state["key"] == key and time.time() - _state["at"] < RENEW_AFTER and _valid():
            return
        _kinit(*key)


def _parse_klist(out: str) -> dict:
    """MIT klist (LC_ALL=C): 'Valid starting  Expires  Service principal' tablosundan krbtgt bileti"""
    tickets = []
    for line in out.splitlines():
        m = re.match(r"^\s*(\S+ \S+)\s+(\S+ \S+)\s+(\S+@\S+)\s*$", line)
        if not m:
            continue
        exp = None
        for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                exp = datetime.strptime(m.group(2), fmt).astimezone().isoformat(timespec="seconds")
                break
            except ValueError:
                pass
        tickets.append({"service": m.group(3), "expires": exp or m.group(2)})
    tgt = next((t for t in tickets if t["service"].startswith("krbtgt/")), None)
    principal = re.search(r"Default principal:\s*(\S+)", out)
    return {"principal": principal.group(1) if principal else None, "expires": tgt["expires"] if tgt else None,
            "hostTickets": [t["service"] for t in tickets if not t["service"].startswith("krbtgt/")][:20]}


def status(ssh: dict) -> dict:
    """Ayarlar / Sunucular ekranı ve /api/health için bilet durumu (yalnızca Kerberos seçiliyse klist çalışır)"""
    keytab = ssh.get("krbKeytab") or ""
    out = {"enabled": ssh.get("auth") == "kerberos", "principal": ssh.get("krbPrincipal") or None, "keytab": keytab,
           "keytabExists": bool(keytab) and Path(keytab).is_file(), "ccache": ccache(), "lastError": _state["error"],
           "valid": False, "expires": None, "hostTickets": []}
    if not out["enabled"]:
        return out
    try:
        p = _run([KLIST_BIN, *KLIST_PREFIX], timeout=10)
        if p.returncode == 0:
            out.update(_parse_klist(p.stdout.decode("utf-8", "replace")), valid=_valid())
    except FileNotFoundError:
        out["lastError"] = out["lastError"] or f"'{KLIST_BIN}' bulunamadı: dnf install krb5-workstation"
    except (OSError, subprocess.TimeoutExpired) as e:
        out["lastError"] = out["lastError"] or str(e)
    return out


def renew(ssh: dict) -> dict:
    """Ayarlar'daki 'Bileti şimdi al' düğmesi"""
    try:
        ensure(ssh, force=True)
    except KerberosError as e:
        return {**status(ssh), "ok": False, "error": str(e)}
    return {**status(ssh), "ok": True}


def is_ip(host: str) -> bool:
    return bool(IP_RE.match(host or ""))
