#!/usr/bin/env python3
"""Kerberos testi için sahte ssh: argümanları ve ortamı FAKE_SSH_LOG dosyasına yazar.

GSSAPI seçilmişse KRB5CCNAME'deki bileti ister; IP adresine bağlanırken veya bilet yokken
"Permission denied (gssapi-with-mic)" döner. Diğerleri fake_ssh.py gibi demo RHEL çıktısı verir.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unixai import demo  # noqa: E402

args = sys.argv[1:]
host = args[args.index("--") + 1]
data = sys.stdin.read()
cc = os.environ.get("KRB5CCNAME", "")
with open(os.environ["FAKE_SSH_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "ccache": cc, "stdin": data}) + "\n")

if "GSSAPIAuthentication=yes" in args:
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", host) or not cc or not Path(cc.removeprefix("FILE:")).exists():
        sys.stderr.write(f"svc-trinity@{host}: Permission denied (gssapi-with-mic).\n")
        sys.exit(255)
if "unixai_collect" not in data:
    user = args[args.index("-l") + 1]
    out = f"user={user} host={host}\nRed Hat Enterprise Linux release 9.4 (Plow)\n"
    if user != "root":
        out += "yetki: dzdo -n çalışıyor\n" if "dzdo -n" in data else "yetki: sudo -n çalışıyor\n"
    sys.stdout.write(out)
    sys.exit(0)
sections = {**demo.build(0)[0]["sections"], "hostname": host}
sys.stdout.write("\n".join(f"===UNIXAI:{k}===\n{v}" for k, v in sections.items()) + "\n===UNIXAI:end===\n")
