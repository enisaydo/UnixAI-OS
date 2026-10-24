"""Salt-okunur tanı komutları (ajan ve MCP araçları için)

Ajan sunucuda serbest komut çalıştıramaz: yalnızca bu katalogdaki sabit komutlar, doğrulanmış parametrelerle
(unit / paket / yol için katı regex) SSH üzerinden çalıştırılır. Komutların hiçbiri sistemi değiştirmez.
"""
from __future__ import annotations

import re

from . import collector, inventory, settings
from .errors import ApiError

PARAM_RE = {
    "unit": re.compile(r"^[A-Za-z0-9@._:-]{1,128}$"),
    "package": re.compile(r"^[A-Za-z0-9._+-]{1,128}$"),
    "path": re.compile(r"^/[A-Za-z0-9._/-]{0,250}$"),
    "lines": re.compile(r"^\d{1,4}$"),
}

CATALOG = {
    "disk": ("Dosya sistemleri ve doluluk", "df -hPT -x tmpfs -x devtmpfs", []),
    "inodes": ("Inode kullanımı", "df -hPi -x tmpfs -x devtmpfs", []),
    "lvm": ("LVM: VG / LV / PV (boş alan dahil)", "vgs; echo; lvs; echo; pvs", []),
    "block_devices": ("Disk ve bölümler", "lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT", []),
    "large_dirs": ("Bir yol altındaki en büyük dizinler", "du -xh --max-depth=2 {path} 2>/dev/null | sort -h | tail -25", ["path"]),
    "memory": ("Bellek ve swap", "free -m; echo; swapon --show", []),
    "top_cpu": ("En çok CPU kullanan prosesler", "ps -eo pid,user,pcpu,pmem,rss,etime,comm --sort=-pcpu | head -20", []),
    "top_mem": ("En çok bellek kullanan prosesler", "ps -eo pid,user,pcpu,pmem,rss,etime,comm --sort=-rss | head -20", []),
    "load": ("Uptime ve load", "uptime; nproc", []),
    "failed_units": ("Başarısız systemd unit'leri", "systemctl --failed --no-pager --plain", []),
    "service_status": ("Bir servisin durumu", "systemctl status {unit} --no-pager -l | head -60", ["unit"]),
    "service_logs": ("Bir servisin son logları", "journalctl -u {unit} -n {lines} --no-pager -o short-iso", ["unit", "lines"]),
    "errors_today": ("Bugünkü hata seviyesindeki journal kayıtları", "journalctl -p err --since today --no-pager -o short-iso | tail -n {lines}", ["lines"]),
    "kernel_messages": ("Kernel uyarı/hata mesajları", "dmesg -T --level=err,warn 2>/dev/null | tail -n {lines}", ["lines"]),
    "listening_ports": ("Dinleyen portlar", "ss -ltnp", []),
    "package": ("Paket sürümü", "rpm -q {package}", ["package"]),
    "security_updates": ("Bekleyen güvenlik güncellemeleri", "dnf -q updateinfo list --security 2>/dev/null || yum -q updateinfo list security", []),
    "selinux": ("SELinux durumu ve son AVC kayıtları", "sestatus; echo; ausearch -m avc -ts today 2>/dev/null | tail -40", []),
    "time_sync": ("Zaman senkronizasyonu", "chronyc tracking; echo; chronyc sources", []),
    "multipath": ("Multipath yolları", "multipath -ll 2>/dev/null", []),
    "mounts": ("Bağlı dosya sistemleri (fstab karşılaştırması için)", "findmnt -rno TARGET,SOURCE,FSTYPE,OPTIONS; echo; cat /etc/fstab", []),
    "network": ("IP adresleri ve rotalar", "ip -br addr; echo; ip route", []),
    "reboot_needed": ("Reboot gerekiyor mu", "needs-restarting -r; echo rc=$?", []),
}


def catalog() -> list:
    return [{"check": k, "description": d, "params": p} for k, (d, _, p) in CATALOG.items()]


def build_command(check: str, params: dict) -> str:
    if check not in CATALOG:
        raise ApiError(f"Bilinmeyen tanı: {check}. Geçerli olanlar: {', '.join(CATALOG)}")
    _, tpl, needed = CATALOG[check]
    values = {"lines": "200", **{k: str(v) for k, v in (params or {}).items() if v is not None}}
    for p in needed:
        v = values.get(p)
        if v is None or v == "":
            raise ApiError(f"'{check}' için '{p}' parametresi gerekli")
        if not PARAM_RE[p].match(v):
            raise ApiError(f"Geçersiz '{p}' değeri: {v}")
    if int(values["lines"]) > 2000:
        values["lines"] = "2000"
    return "export LC_ALL=C; " + tpl.format(**values)


def run(server_name: str, check: str, params=None) -> dict:
    s = inventory.get(server_name)
    if not s:
        raise ApiError(f"'{server_name}' envanterde yok (demo sunucularında SSH tanısı çalıştırılamaz)", 404)
    cmd = build_command(check, params or {})
    ssh = settings.get()["ssh"]
    r = collector.run_ssh(s, {**ssh, "commandTimeout": min(ssh["commandTimeout"], 120)}, cmd)
    if r["timedOut"]:
        raise ApiError("Tanı komutu zaman aşımına uğradı", 504)
    if r["code"] == 255 and not r["stdout"]:
        raise ApiError(collector.explain(r["stderr"], s, ssh), 502)
    out = r["stdout"]
    return {"server": server_name, "check": check, "description": CATALOG[check][0], "exitCode": r["code"],
            "output": out[-20000:], "truncated": len(out) > 20000, "stderr": r["stderr"][-2000:] or None, "durationMs": r["durationMs"]}
