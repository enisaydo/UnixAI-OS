"""Sahte ssh: vuln.host → CVE etkiliyor, bad.host → bağlantı reddi, diğerleri → etkilenmiyor"""
import sys

args = sys.argv[1:]
host = args[args.index("--") + 1]
cmd = args[args.index("--") + 2] if len(args) > args.index("--") + 2 else ""
if host == "bad.host":
    sys.stderr.write("root@bad.host: Permission denied (publickey).\n")
    sys.exit(255)
if "--with-cve" in cmd:  # zafiyet torbası
    if host == "vuln.host":
        sys.stdout.write("RHSA-2024:4312 Important/Sec. openssh-8.7p1-38.el9_4.1.x86_64\n__sep\n"
                         "CVE-2024-6387 Important/Sec. openssh-8.7p1-38.el9_4.1.x86_64\n__rc=0\n")
    else:
        sys.stdout.write("__sep\n__rc=0\n")
elif "updateinfo info RHSA-2021:9999" in cmd and host == "vuln.host":  # errata içeriği (Satellite'ta yok)
    sys.stdout.write("=" * 79 + "\n  Moderate: curl security update\n" + "=" * 79 + "\n"
                     "  Update ID: RHSA-2021:9999\n       Type: security\n    Updated: 2021-05-02 00:00:00\n"
                     "       Bugs: 1918692 - CVE-2021-22876 curl: Leak of authentication credentials\n"
                     "       CVEs: CVE-2021-22876\n           : CVE-2021-22898\n"
                     "Description: The curl packages provide the libcurl library.\n           : \n           : Security Fix(es):\n"
                     "           : * curl: Leak of authentication credentials in URL\n   Severity: Moderate\n__rc=0\n")
elif "updateinfo list --cve" in cmd and host == "vuln.host":
    sys.stdout.write("RHSA-2024:4312 Important/Sec. openssh-8.7p1-38.el9_4.1.x86_64\n__rc=0\n")
else:
    sys.stdout.write("__rc=0\n")
