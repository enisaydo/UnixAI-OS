"""Ham komut çıktılarını yapısal metriklere çevirir."""
from __future__ import annotations

import re
from collections import Counter

IP_RE = re.compile(r"from (\d{1,3}(?:\.\d{1,3}){3})")
# Log satırlarını gruplamak için zaman damgası, PID ve sayıları normalize et
TS_RE = re.compile(r"^\S+\s+")
PID_RE = re.compile(r"\[\d+\]")
NUM_RE = re.compile(r"\b\d+\b")
DISK_ERR_RE = re.compile(r"I/O error|blk_update_request|XFS .*error|EXT4-fs error")


def _int(text: str, default=None):
    lines = [l for l in (text or "").splitlines() if l.strip()]
    try:
        return int(lines[-1].strip())
    except (ValueError, IndexError):
        return default


def parse_df(text: str) -> list:
    out = []
    for line in (text or "").splitlines()[1:]:
        p = line.split()
        if len(p) < 7:
            continue
        try:
            total, used = int(p[2]), int(p[3])
        except ValueError:
            continue
        pct = 0 if p[5] == "-" else int(p[5].rstrip("%") or 0)
        out.append({"device": p[0], "fstype": p[1], "size_kb": total, "used_kb": used, "used_pct": pct, "mount": " ".join(p[6:])})
    return out


def parse_meminfo(text: str) -> dict:
    kv = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        if v.strip():
            kv[k.strip()] = int(v.split()[0])
    total, avail = kv.get("MemTotal", 0), kv.get("MemAvailable", 0)
    st, sf = kv.get("SwapTotal", 0), kv.get("SwapFree", 0)
    return {
        "mem_total_mb": total // 1024,
        "mem_available_pct": round(100 * avail / total, 1) if total else None,
        "swap_total_mb": st // 1024,
        "swap_used_pct": round(100 * (st - sf) / st, 1) if st else 0,
    }


def parse_vmstat(text: str) -> dict:
    rows = [l.split() for l in (text or "").splitlines() if l.strip() and l.split()[0].isdigit()]
    rows = rows[1:] or rows  # ilk satır boot'tan beri ortalama; atla
    if not rows:
        return {}
    cols = ["r", "b", "swpd", "free", "buff", "cache", "si", "so", "bi", "bo", "in", "cs", "us", "sy", "id", "wa", "st"]

    def avg(c):
        i = cols.index(c)
        return round(sum(int(r[i]) for r in rows if len(r) > i) / len(rows), 1)

    return {"cpu_user": avg("us"), "cpu_sys": avg("sy"), "cpu_idle": avg("id"), "cpu_iowait": avg("wa"),
            "run_queue": avg("r"), "blocked": avg("b"), "swap_in": avg("si"), "swap_out": avg("so")}


def parse_chrony(text: str) -> dict:
    out = {"synced": False, "offset_sec": None}
    for line in (text or "").splitlines():
        if line.startswith("Leap status"):
            out["synced"] = "Normal" in line
        elif line.startswith("System time"):
            m = re.search(r"([\d.]+) seconds", line)
            if m:
                out["offset_sec"] = float(m.group(1))
    return out


def parse_ssh_failures(text: str) -> dict:
    lines = [l for l in (text or "").splitlines() if l.strip()]
    ips = Counter(m.group(1) for l in lines if (m := IP_RE.search(l)))
    return {"count": len(lines), "top_sources": [list(x) for x in ips.most_common(5)]}


def summarize_log(text: str, limit: int = 25) -> list:
    """Tekrarlayan log satırlarını gruplayıp en sık olanları döndürür (token tasarrufu)."""
    groups: Counter = Counter()
    sample: dict = {}
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        key = NUM_RE.sub("N", PID_RE.sub("", TS_RE.sub("", line, count=1)))
        groups[key] += 1
        sample.setdefault(key, line)
    return [{"count": c, "example": sample[k]} for k, c in groups.most_common(limit)]


def parse(sections: dict) -> dict:
    def s(k):
        return sections.get(k) or ""

    load = s("loadavg").split()
    kernel_log = s("kernel_errors")
    return {
        "hostname": s("hostname").strip(),
        "os_release": s("os_release").strip(),
        "kernel": s("kernel").strip(),
        "uptime_days": round((_int(s("uptime_seconds"), 0) or 0) / 86400, 1),
        "cpu_count": _int(s("nproc"), 1),
        "load": {"1m": float(load[0]), "5m": float(load[1]), "15m": float(load[2])} if len(load) >= 3 else {},
        "memory": parse_meminfo(s("meminfo")),
        "filesystems": parse_df(s("df")),
        "inodes": parse_df(s("df_inode")),
        "cpu": parse_vmstat(s("vmstat")),
        "failed_units": [l.split()[0] for l in s("failed_units").splitlines() if l.strip()],
        "ssh_failures": parse_ssh_failures(s("ssh_failures")),
        "oom_kills": len(re.findall(r"Out of memory: Kill", kernel_log)),
        "disk_errors": sum(1 for l in kernel_log.splitlines() if DISK_ERR_RE.search(l)),
        "selinux": s("selinux").strip() or "Unknown",
        "avc_denials": _int(s("avc_denials"), 0),
        "time_sync": parse_chrony(s("chrony")),
        "security_updates": _int(s("security_updates"), 0),
        "reboot_required": _int(s("needs_restarting")) == 1,
        "zombies": _int(s("zombies"), 0),
        "top_processes": [(p[:5] + [" ".join(p[5:])]) for p in (l.split() for l in s("top_processes").splitlines() if l.strip())],
        "journal_errors": summarize_log(s("journal_errors")),
        "kernel_messages": summarize_log(kernel_log, limit=15),
    }


def headline(m: dict) -> dict:
    """Panolarda ve trend grafiklerinde kullanılan özet sayılar"""
    fs_max = max(m["filesystems"], key=lambda x: x["used_pct"], default=None)
    os_short = re.sub(r"\s*\(.*\)", "", re.sub(r"Red Hat Enterprise Linux( Server)? release", "RHEL", m["os_release"]))
    return {
        "os": os_short,
        "fs_max_pct": fs_max["used_pct"] if fs_max else None,
        "fs_max_mount": fs_max["mount"] if fs_max else None,
        "mem_available_pct": m["memory"]["mem_available_pct"],
        "swap_used_pct": m["memory"]["swap_used_pct"],
        "load_per_core": round(m["load"]["15m"] / max(m["cpu_count"], 1), 2) if m["load"] else None,
        "cpu_iowait": m["cpu"].get("cpu_iowait"),
        "ssh_failures": m["ssh_failures"]["count"],
        "journal_errors": sum(g["count"] for g in m["journal_errors"]),
        "failed_units": len(m["failed_units"]),
        "security_updates": m["security_updates"],
        "uptime_days": m["uptime_days"],
    }
