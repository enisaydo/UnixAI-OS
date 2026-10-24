#!/usr/bin/env python3
"""Smoke test için sahte ssh istemcisi (gerçek ağ bağlantısı yapmaz).

  UNIXAI_SSH_BIN=python3  UNIXAI_SSH_PREFIX='["scripts/fake_ssh.py"]'
"--" sonrasındaki adres:  bad.host → Permission denied · diğerleri → demo RHEL çıktısı
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unixai import demo  # noqa: E402

args = sys.argv[1:]
host = args[args.index("--") + 1]
data = sys.stdin.read()
if host == "bad.host":
    sys.stderr.write("root@bad.host: Permission denied (publickey,gssapi-keyex,gssapi-with-mic).\n")
    sys.exit(255)
if "unixai_collect" not in data:
    sys.stdout.write(f"user=root host={host}\nRed Hat Enterprise Linux release 9.4 (Plow)\n")
    sys.exit(0)
sections = {**demo.build(0)[0]["sections"], "hostname": host}
sys.stdout.write("\n".join(f"===UNIXAI:{k}===\n{v}" for k, v in sections.items()) + "\n===UNIXAI:end===\n")
