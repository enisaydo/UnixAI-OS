"""Demo: SSH erişimi olmadan uçtan uca deneme için gerçekçi RHEL komut çıktıları üretir.

Çıktılar toplayıcının ürettiği bölümlerle birebir aynıdır; parser, kural motoru ve AI analizi
gerçek veriyle aynı yoldan geçer. d = kaç gün önce (trend için diskler geçmişte daha boş).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

DF_HDR = "Filesystem     Type 1024-blocks     Used Available Capacity Mounted on"
DFI_HDR = "Filesystem     Type  Inodes  IUsed   IFree IUse% Mounted on"


def _df(rows):
    out = [DF_HDR]
    for dev, fs, gb, pct, mnt in rows:
        size = int(gb * 1024 * 1024)
        used = int(size * pct / 100)
        out.append(f"{dev} {fs} {size} {used} {size - used} {pct}% {mnt}")
    return "\n".join(out)


def _dfi(rows):
    out = [DFI_HDR]
    for dev, fs, total, pct, mnt in rows:
        used = int(total * pct / 100)
        out.append(f"{dev} {fs} {total} {used} {total - used} {pct}% {mnt}")
    return "\n".join(out)


def _vmstat(rnd, r, b, us, sy, wa, si=0, so=0):
    out = ["procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----",
           " r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st"]
    j = lambda v: max(0, v + rnd.randint(-2, 2))  # noqa: E731
    for _ in range(5):
        u, s, w = j(us), j(sy), j(wa)
        out.append(f" {j(r)} {j(b)} 102400 204800 4096 1048576 {j(si)} {j(so)} 1200 3400 2100 4200 {u} {s} {max(0, 100 - u - s - w)} {w} 0")
    return "\n".join(out)


def _meminfo(total_gb, avail_pct, swap_gb, swap_used_pct):
    t, s = total_gb * 1024 * 1024, swap_gb * 1024 * 1024
    return (f"MemTotal:       {t} kB\nMemAvailable:   {int(t * avail_pct / 100)} kB\n"
            f"SwapTotal:      {s} kB\nSwapFree:       {int(s * (100 - swap_used_pct) / 100)} kB")


def _chrony(synced, offset):
    return (f"Reference ID    : C0A80101 (192.168.1.1)\nStratum         : 3\nSystem time     : {offset:.9f} seconds fast of NTP time\n"
            f"Leap status     : {'Normal' if synced else 'Not synchronised'}")


def build(d: int = 0, base: datetime | None = None) -> list:
    """[{name, host, sections}] — d gün önceki durum"""
    rnd = random.Random(42 + d)
    base = (base or datetime.now()).astimezone().replace(tzinfo=None)

    def ts(h_ago):
        return (base - timedelta(hours=h_ago, minutes=rnd.randint(0, 59))).strftime("%Y-%m-%dT%H:%M:%S+0300")

    def ssh_fail(host, n, ips):
        lines = []
        for _ in range(n):
            ip = rnd.choices(ips, weights=[70, 25, 5][: len(ips)])[0]
            user = rnd.choice(["root", "admin", "oracle", "test", "ubuntu", "postgres"])
            kind = rnd.choice([f"Failed password for {user}", f"Failed password for invalid user {user}"])
            lines.append(f"{ts(rnd.randint(0, 23))} {host} sshd[{rnd.randint(1000, 99999)}]: {kind} from {ip} port {rnd.randint(30000, 65000)} ssh2")
        return "\n".join(lines)

    io_err = lambda h: f"{ts(h)} rhel-db01 kernel: blk_update_request: I/O error, dev sdc, sector {rnd.randint(10**8, 10**9)} op 0x0:(READ)"  # noqa: E731
    oom = f"{ts(5)} rhel-db01 kernel: Out of memory: Killed process 48211 (java) total-vm:18874368kB, anon-rss:9437184kB"

    return [
        {"name": "rhel-db01", "host": "10.20.30.11", "sections": {
            "hostname": "rhel-db01.corp.local",
            "os_release": "Red Hat Enterprise Linux release 8.9 (Ootpa)",
            "kernel": "4.18.0-513.24.1.el8_9.x86_64",
            "uptime_seconds": str((143 - d) * 86400),
            "nproc": "16",
            "loadavg": f"{14.2 - 1.1 * d:.2f} {12.85 - 1.1 * d:.2f} {11.4 - 1.1 * d:.2f} 3/812 99812",
            "meminfo": _meminfo(128, 7, 16, 35),
            "df": _df([("/dev/mapper/rhel-root", "xfs", 50, 61, "/"), ("/dev/sda1", "xfs", 1, 34, "/boot"),
                       ("/dev/mapper/rhel-var", "xfs", 20, 72, "/var"), ("/dev/mapper/vg_data-u01", "xfs", 2000, 96 - 3 * d, "/u01"),
                       ("/dev/mapper/vg_data-arch", "xfs", 500, max(40, 88 - 5 * d), "/u02/archive")]),
            "df_inode": _dfi([("/dev/mapper/rhel-root", "xfs", 26214400, 8, "/"), ("/dev/mapper/vg_data-u01", "xfs", 209715200, 4, "/u01")]),
            "vmstat": _vmstat(rnd, 12, 6, 38, 9, 31, si=12, so=40),
            "failed_units": "",
            "journal_errors": "\n".join([io_err(h) for h in range(0, 20, 2)]
                                        + [f"{ts(h)} rhel-db01 multipathd[1201]: sdc: mark as failed" for h in range(0, 20, 4)]
                                        + [f"{ts(h)} rhel-db01 multipathd[1201]: mpatha: remaining active paths: 1" for h in range(0, 20, 4)] + [oom]),
            "ssh_failures": "",
            "kernel_errors": "\n".join([io_err(h) for h in range(0, 20, 2)]
                                       + [f"{ts(h)} rhel-db01 kernel: sd 2:0:1:0: [sdc] tag#12 FAILED Result: hostbyte=DID_ERROR driverbyte=DRIVER_OK"
                                          for h in range(0, 20, 5)] + [oom]),
            "selinux": "Enforcing", "avc_denials": "0", "chrony": _chrony(True, 0.000021), "security_updates": "4", "needs_restarting": "0",
            "top_processes": "\n".join(["3311 oracle 182.4 38.2 50069504 ora_dbw0_PROD", "3315 oracle 96.1 12.4 16252928 ora_lgwr_PROD",
                                        "48900 appusr 74.0 9.1 11927552 java", "3320 oracle 41.3 4.0 5242880 ora_ckpt_PROD",
                                        "1201 root 6.2 0.1 131072 multipathd"]),
            "zombies": "0",
        }},
        {"name": "rhel-web01", "host": "10.20.30.21", "sections": {
            "hostname": "rhel-web01.corp.local",
            "os_release": "Red Hat Enterprise Linux release 9.3 (Plow)",
            "kernel": "5.14.0-362.8.1.el9_3.x86_64",
            "uptime_seconds": str((61 - d) * 86400),
            "nproc": "8",
            "loadavg": "0.85 0.92 0.88 1/402 22113",
            "meminfo": _meminfo(32, 58, 4, 0),
            "df": _df([("/dev/mapper/rhel-root", "xfs", 40, 44, "/"), ("/dev/vda1", "xfs", 1, 29, "/boot"),
                       ("/dev/mapper/rhel-var_log", "xfs", 10, max(50, 81 - 4 * d), "/var/log")]),
            "df_inode": _dfi([("/dev/mapper/rhel-root", "xfs", 20971520, 6, "/")]),
            "vmstat": _vmstat(rnd, 1, 0, 6, 3, 0),
            "failed_units": "httpd.service loaded failed failed The Apache HTTP Server",
            "journal_errors": "\n".join([
                f"{ts(3)} rhel-web01 httpd[8812]: AH00526: Syntax error on line 42 of /etc/httpd/conf.d/ssl.conf:",
                f"{ts(3)} rhel-web01 httpd[8812]: SSLCertificateFile: file '/etc/pki/tls/certs/web01-2025.crt' does not exist or is empty",
                f"{ts(3)} rhel-web01 systemd[1]: httpd.service: Main process exited, code=exited, status=1/FAILURE",
                f"{ts(3)} rhel-web01 systemd[1]: Failed to start The Apache HTTP Server."]
                + [f"{ts(h)} rhel-web01 sshd[{rnd.randint(1000, 9999)}]: error: maximum authentication attempts exceeded for root from "
                   f"185.220.101.47 port 51234 ssh2 [preauth]" for h in range(0, 23, 2)]),
            "ssh_failures": ssh_fail("rhel-web01", int(1873 / (1 + 0.9 * d)), ["185.220.101.47", "45.95.169.12", "10.20.30.15"]),
            "kernel_errors": "",
            "selinux": "Permissive", "avc_denials": "37", "chrony": _chrony(True, 0.00011), "security_updates": "23", "needs_restarting": "1",
            "top_processes": "\n".join(["912 root 2.1 0.3 98304 sshd", "1 root 0.4 0.1 16384 systemd", "1340 root 0.3 0.6 204800 rsyslogd"]),
            "zombies": "0",
        }},
        {"name": "rhel-app02", "host": "10.20.30.32", "sections": {
            "hostname": "rhel-app02.corp.local",
            "os_release": "Red Hat Enterprise Linux Server release 7.9 (Maipo)",
            "kernel": "3.10.0-1160.el7.x86_64",
            "uptime_seconds": str((512 - d) * 86400),
            "nproc": "4",
            "loadavg": "9.80 9.42 8.95 7/1530 31877",
            "meminfo": _meminfo(16, 5 + 3 * d, 8, max(10, 74 - 9 * d)),
            "df": _df([("/dev/mapper/rhel-root", "xfs", 30, 78, "/"), ("/dev/sda1", "xfs", 0.5, 63, "/boot"), ("/dev/mapper/rhel-tmp", "xfs", 5, 40, "/tmp")]),
            "df_inode": _dfi([("/dev/mapper/rhel-root", "xfs", 15728640, 12, "/"), ("/dev/mapper/rhel-tmp", "xfs", 2621440, 91, "/tmp")]),
            "vmstat": _vmstat(rnd, 9, 2, 61, 22, 8, si=220, so=310),
            "failed_units": "kdump.service loaded failed failed Crash recovery kernel arming",
            "journal_errors": "\n".join(
                [f"{ts(h)} rhel-app02 java[{20000 + h}]: java.lang.OutOfMemoryError: GC overhead limit exceeded" for h in range(0, 23, 3)]
                + [f"{ts(h)} rhel-app02 chronyd[811]: Can't synchronise: no selectable sources" for h in range(0, 23, 6)]
                + [f"{ts(10)} rhel-app02 kdumpctl[1022]: No memory reserved for crash kernel"]),
            "ssh_failures": ssh_fail("rhel-app02", 12, ["10.20.30.15"]),
            "kernel_errors": "\n".join(f"{ts(h)} rhel-app02 kernel: INFO: task java:{30000 + h} blocked for more than 120 seconds." for h in range(0, 23, 8)),
            "selinux": "Enforcing", "avc_denials": "0", "chrony": _chrony(False, 2.384112), "security_updates": "57", "needs_restarting": "1",
            "top_processes": "\n".join(["20211 appusr 287.5 61.0 10223616 java", "20311 appusr 22.0 4.2 716800 java", "811 chrony 0.1 0.0 2048 chronyd"]),
            "zombies": "34",
        }},
        {"name": "rhel-nfs01", "host": "10.20.30.41", "sections": {
            "hostname": "rhel-nfs01.corp.local",
            "os_release": "Red Hat Enterprise Linux release 9.4 (Plow)",
            "kernel": "5.14.0-427.13.1.el9_4.x86_64",
            "uptime_seconds": str((27 - d) * 86400),
            "nproc": "8",
            "loadavg": "1.10 1.05 0.98 2/380 11234",
            "meminfo": _meminfo(64, 71, 8, 1),
            "df": _df([("/dev/mapper/rhel-root", "xfs", 40, 38, "/"), ("/dev/nvme0n1p1", "xfs", 1, 31, "/boot"), ("/dev/mapper/vg_exp-export", "xfs", 8000, 69, "/export")]),
            "df_inode": _dfi([("/dev/mapper/vg_exp-export", "xfs", 838860800, 22, "/export")]),
            "vmstat": _vmstat(rnd, 1, 0, 4, 6, 2),
            "failed_units": "",
            "journal_errors": f"{ts(14)} rhel-nfs01 rpc.mountd[1450]: refused mount request from 10.20.40.77 for /export/backup (/export/backup): unmatched host",
            "ssh_failures": "", "kernel_errors": "",
            "selinux": "Enforcing", "avc_denials": "0", "chrony": _chrony(True, 0.000004), "security_updates": "2", "needs_restarting": "0",
            "top_processes": "\n".join(["1455 root 8.0 0.0 0 nfsd", "1456 root 7.5 0.0 0 nfsd"]),
            "zombies": "0",
        }},
    ]


def chat_reply(question: str, context) -> str:
    """GenAI bağlı değilken sohbet cevabı"""
    a = (context or {}).get("data", {}).get("analysis")
    if a:
        acts = "\n".join(f"- **{x['priority']}** {x['title']}" for x in a.get("action_items", [])[:5])
        return (f"_Demo modu: GenAI bağlı değil, cevap kural motoru sonuçlarından üretildi._\n\n**{context['name']}** sağlık skoru "
                f"**{a['health_score']}** ({a['status']}).\n\n{a['summary']}\n\nÖncelikli işler:\n{acts or '- Bulgu yok'}\n\n"
                "Gerçek AI cevabı için Ayarlar → GenAI bağlantısı'nda token servisi ve chat/completions adreslerini girin.")
    return ("_Demo modu: GenAI bağlı değil._\n\nBir sunucu bağlamı seçerseniz son analizine göre özet verebilirim. Gerçek AI cevabı için "
            "**Ayarlar → GenAI bağlantısı**'nda token servisi ve chat/completions (GPT) adreslerini girin.")
