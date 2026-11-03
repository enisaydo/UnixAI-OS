"""Sahte ssh: vuln.host → CVE etkiliyor, bad.host → bağlantı reddi, diğerleri → etkilenmiyor"""
import sys

args = sys.argv[1:]
host = args[args.index("--") + 1]
cmd = args[args.index("--") + 2] if len(args) > args.index("--") + 2 else ""
if host == "bad.host":
    sys.stderr.write("root@bad.host: Permission denied (publickey).\n")
    sys.exit(255)
if "updateinfo list --cve" in cmd and host == "vuln.host":
    sys.stdout.write("RHSA-2024:4312 Important/Sec. openssh-8.7p1-38.el9_4.1.x86_64\n__rc=0\n")
else:
    sys.stdout.write("__rc=0\n")
