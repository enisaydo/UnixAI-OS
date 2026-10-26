"""Deterministik eşik kontrolleri. AI'dan önce çalışır; AI'a kanıt olarak verilir
ve AI'a ulaşılamazsa tek başına rapor üretebilir."""
from __future__ import annotations

DEFAULTS = {
    "fs_warn_pct": 85, "fs_crit_pct": 95, "inode_warn_pct": 85,
    "mem_avail_warn_pct": 10, "swap_warn_pct": 50,
    "load_per_core_warn": 1.5, "load_per_core_crit": 3.0,
    "iowait_warn_pct": 20, "ssh_fail_warn": 100,
    "time_offset_warn_sec": 0.5, "uptime_warn_days": 365, "zombie_warn": 10,
    "fs_days_to_full_warn": 14,
}
SEV_ORDER = ["critical", "high", "medium", "low"]


def _f(sev, cat, title, evidence):
    return {"severity": sev, "category": cat, "title": title, "evidence": evidence}


def evaluate(m: dict, prev=None, t=None, days: float = 1) -> list:
    """m: bugünkü metrikler, prev: önceki toplamanın metrikleri, days: iki toplama arası gün farkı"""
    t = {**DEFAULTS, **(t or {})}
    out = []

    prev_fs = {f["mount"]: f for f in (prev or {}).get("filesystems", [])}
    for fs in m["filesystems"]:
        pct, mnt = fs["used_pct"], fs["mount"]
        if pct >= t["fs_crit_pct"]:
            out.append(_f("critical", "disk", f"{mnt} doluluk %{pct}", fs))
        elif pct >= t["fs_warn_pct"]:
            out.append(_f("high", "disk", f"{mnt} doluluk %{pct}", fs))
        # Trend: önceki snapshot ile karşılaştırıp dolma süresini tahmin et
        p = prev_fs.get(mnt)
        if p and fs["used_kb"] > p["used_kb"] and days > 0.2:
            growth = (fs["used_kb"] - p["used_kb"]) / days
            left = (fs["size_kb"] - fs["used_kb"]) / growth
            fs["growth_kb_per_day"], fs["days_to_full"] = round(growth), round(left, 1)
            if left <= t["fs_days_to_full_warn"]:
                out.append(_f("high", "disk", f"{mnt} ~{left:.1f} gün içinde dolacak",
                              {"growth_mb_per_day": round(growth / 1024), "used_pct": pct}))
    for fs in m["inodes"]:
        if fs["used_pct"] >= t["inode_warn_pct"]:
            out.append(_f("high", "disk", f"{fs['mount']} inode %{fs['used_pct']}", fs))

    mem = m["memory"]
    if mem["mem_available_pct"] is not None and mem["mem_available_pct"] < t["mem_avail_warn_pct"]:
        out.append(_f("high", "memory", f"Kullanılabilir bellek %{mem['mem_available_pct']}", mem))
    if mem["swap_used_pct"] >= t["swap_warn_pct"]:
        out.append(_f("medium", "memory", f"Swap kullanımı %{mem['swap_used_pct']}", mem))
    if m["oom_kills"]:
        out.append(_f("critical", "memory", f"Son 24 saatte {m['oom_kills']} OOM kill", m["kernel_messages"][:3]))

    if m["load"]:
        per_core = m["load"]["15m"] / max(m["cpu_count"], 1)
        if per_core >= t["load_per_core_crit"]:
            out.append(_f("critical", "cpu", f"Load/core {per_core:.2f} (15dk)", m["load"]))
        elif per_core >= t["load_per_core_warn"]:
            out.append(_f("high", "cpu", f"Load/core {per_core:.2f} (15dk)", m["load"]))
    if m["cpu"].get("cpu_iowait", 0) >= t["iowait_warn_pct"]:
        out.append(_f("high", "io", f"I/O wait %{m['cpu']['cpu_iowait']}", m["cpu"]))
    if m["disk_errors"]:
        out.append(_f("critical", "io", f"Kernel logda {m['disk_errors']} disk I/O hatası", m["kernel_messages"][:3]))

    if m["failed_units"]:
        out.append(_f("high", "service", f"{len(m['failed_units'])} failed systemd unit", m["failed_units"]))
    if m["zombies"] >= t["zombie_warn"]:
        out.append(_f("medium", "process", f"{m['zombies']} zombie proses", m["top_processes"][:3]))

    ssh = m["ssh_failures"]
    if ssh["count"] >= t["ssh_fail_warn"]:
        out.append(_f("high", "security", f"{ssh['count']} başarısız SSH girişi", ssh["top_sources"]))
    if m["selinux"] != "Enforcing":
        out.append(_f("medium", "security", f"SELinux {m['selinux']}", {"avc_denials": m["avc_denials"]}))
    if m["security_updates"]:
        out.append(_f("medium", "patch", f"{m['security_updates']} bekleyen güvenlik güncellemesi (RHSA)", {}))
    boot = m.get("boot") or {}
    if boot.get("crash_dumps"):
        out.append(_f("critical", "stability", f"kdump: /var/crash'te {len(boot['crash_dumps'])} çökme dökümü (en yeni: {boot['crash_dumps'][0]})",
                      {"crash_dumps": boot["crash_dumps"], "son_boot_satirlari": boot.get("prev_boot_tail")}))
    if boot.get("recent_reboot") and (boot.get("unclean_reboot") or boot.get("panic")):
        out.append(_f("critical", "stability", "Beklenmedik yeniden başlama: önceki boot temiz kapanmamış (kernel crash / güç kesintisi / hard reset?)",
                      {"uptime_gun": m["uptime_days"], "panic_izi": boot.get("panic"), "onceki_boot_son_satirlar": boot.get("prev_boot_tail")}))
    elif boot.get("recent_reboot"):
        out.append(_f("low", "stability", "Son 24 saatte yeniden başladı" + (" (temiz kapanış)" if boot.get("unclean_reboot") is False else ""),
                      {"uptime_gun": m["uptime_days"]}))
    if m["reboot_required"]:
        out.append(_f("low", "patch", "Kernel/glibc güncellemesi sonrası reboot bekleniyor", {}))
    if m["uptime_days"] >= t["uptime_warn_days"]:
        out.append(_f("low", "patch", f"Uptime {m['uptime_days']} gün", {}))

    ts = m["time_sync"]
    if not ts["synced"] or (ts["offset_sec"] or 0) >= t["time_offset_warn_sec"]:
        out.append(_f("medium", "time", "Zaman senkronizasyonu bozuk", ts))

    if "release 7" in m["os_release"]:
        out.append(_f("medium", "lifecycle", "RHEL 7 standart destek dışı (ELS gerekir)", m["os_release"]))

    return sorted(out, key=lambda f: SEV_ORDER.index(f["severity"]))
