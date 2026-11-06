#!/usr/bin/env python3
"""Kerberos testi için sahte kinit / klist (AD'ye bağlanmaz).

  UNIXAI_KINIT_BIN=python3  UNIXAI_KINIT_PREFIX='["scripts/fake_krb.py","kinit"]'
  UNIXAI_KLIST_BIN=python3  UNIXAI_KLIST_PREFIX='["scripts/fake_krb.py","klist"]'
kinit -k -t <keytab> <principal>: principal 'yok@' ile başlarsa "not found", keytab içeriği 'eski' ise "Preauthentication failed";
aksi halde KRB5CCNAME dosyasına bileti yazar (her kinit, dosyanın yanındaki .count'u artırır).
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

mode, args = sys.argv[1], sys.argv[2:]
cc = Path(os.environ["KRB5CCNAME"].removeprefix("FILE:"))

if mode == "kinit":
    keytab, principal = args[args.index("-t") + 1], args[-1]
    if principal.startswith("yok@"):
        sys.stderr.write(f"kinit: Client '{principal}' not found in Kerberos database while getting initial credentials\n")
        sys.exit(1)
    if Path(keytab).read_text().strip() == "eski":
        sys.stderr.write(f"kinit: Preauthentication failed while getting initial credentials\n")
        sys.exit(1)
    cc.write_text(principal)
    count = cc.with_name(cc.name + ".count")
    count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
    sys.exit(0)

if not cc.exists():
    sys.stderr.write(f"klist: No credentials cache found (filename: {cc})\n")
    sys.exit(1)
if "-s" in args:
    sys.exit(0)
p = cc.read_text()
realm = p.split("@")[1]
now = datetime.now()
f = "%m/%d/%Y %H:%M:%S"
print(f"Ticket cache: FILE:{cc}\nDefault principal: {p}\n\nValid starting       Expires              Service principal")
print(f"{now:{f}}  {now + timedelta(hours=10):{f}}  krbtgt/{realm}@{realm}")
print(f"{now:{f}}  {now + timedelta(hours=10):{f}}  host/rhel-db01.kurum.local@{realm}")
