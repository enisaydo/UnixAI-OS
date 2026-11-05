"""Yönetim raporu: filo sağlığı, trend, öne çıkan sorunlar, zafiyetler, otomasyon (dönem: son N gün)

  GET /api/report?days=30 → arayüzdeki "Yönetim Raporu" (yazdırılabilir / PDF olarak kaydedilebilir)
  Yalnızca mevcut kayıtlardan derlenir (data/runs, vuln_bags, morpheus kayıtları); hiçbir sisteme gitmez.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import inventory, morpheus, runs, satellite
from .store import iso_now

MANUAL_MIN_PER_SERVER = 15  # elle günlük sağlık kontrolünün sunucu başına tahmini süresi (dk) — raporda varsayım olarak yazılır


def _avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs)) if xs else None


def _trend(since: str) -> list:
    """Gün başına filo ortalama sağlık puanı (o günün son başarılı toplaması)"""
    by_day = {}
    for r in runs.list_runs(1000):
        if r["date"] < since or r.get("status") == "running":
            continue
        scores = [s.get("score") for s in r["servers"] if s.get("collect") in ("ok", "partial")]
        if not scores or r["date"] in by_day:  # liste en yeniden eskiye: günün son çalıştırması önce gelir
            continue
        by_day[r["date"]] = {"date": r["date"], "avg": _avg(scores), "servers": len(scores),
                             "critical": sum(1 for s in r["servers"] if s.get("status") == "CRITICAL"),
                             "p1": sum(s.get("p1") or 0 for s in r["servers"])}
    return sorted(by_day.values(), key=lambda x: x["date"])


def _top_issues(latest: dict, limit: int = 12) -> list:
    out = []
    for name, e in sorted(latest.items(), key=lambda kv: (kv[1].get("score") if isinstance(kv[1].get("score"), (int, float)) else 101)):
        if not e.get("p1"):
            continue
        snap = runs.snapshot(name)
        for a in [x for x in ((snap or {}).get("analysis") or {}).get("action_items", []) if x.get("priority") == "P1"][:3]:  # sunucu başına en fazla 3
            out.append({"server": name, "title": a.get("title"), "category": a.get("category"), "score": e.get("score")})
        if len(out) >= limit:
            break
    return out[:limit]


def build(days: int = 30) -> dict:
    days = max(1, min(365, int(days or 30)))
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    inv = {s["name"]: s for s in inventory.list_servers()}
    latest = runs.latest_by_server()
    if inv:  # envanter varsa yalnızca envanterdekiler (demo verisi rapora girmez)
        latest = {k: v for k, v in latest.items() if k in inv}
    done = {k: v for k, v in latest.items() if v.get("collect") in ("ok", "partial")}
    status = {s: sum(1 for v in done.values() if v.get("status") == s) for s in ("OK", "WARNING", "CRITICAL")}
    period = [r for r in runs.list_runs(1000) if r["date"] >= since and r.get("status") != "running"]
    analyzed = sum(1 for r in period for s in r["servers"] if s.get("collect") in ("ok", "partial"))
    ai = sum(1 for r in period for s in r["servers"] if s.get("collect") in ("ok", "partial") and s.get("engine") not in (None, "rules"))
    trend = _trend(since)

    recs = [x for x in morpheus.list_records(refresh_pending=False) if (x.get("sentAt") or "")[:10] >= since]
    st = lambda *k: sum(1 for x in recs if x.get("status") in k)  # noqa: E731
    remediation = {"sent": len(recs), "approved": sum(1 for x in recs if x.get("approval")), "applied": st("applied"), "rejected": st("rejected"),
                   "pending": sum(1 for x in recs if x.get("status") == "generated" and not x.get("approval") and not x.get("rejected")),
                   "avgCompliance": _avg([x.get("score") for x in recs]),
                   "security": sum(1 for x in recs if "CVE" in (x.get("source") or "") or "zafiyet" in (x.get("source") or ""))}

    vb = satellite.bags()
    vulns = None
    if vb.get("lastScan"):
        vulns = {"lastScan": vb["lastScan"], **vb["totals"], "sla": vb["sla"], "topCves": vb["topCves"][:8],
                 "worst": [{"key": h["key"], "counts": (h.get("summary") or {}).get("counts"), "overdue": (h.get("summary") or {}).get("overdue"),
                            "installable": (h.get("summary") or {}).get("installable")} for h in vb["items"] if (h.get("summary") or {}).get("errata")][:8]}

    worst = sorted(({"server": k, "score": v.get("score"), "status": v.get("status"), "p1": v.get("p1"), "headline": v.get("headline"),
                     "collectedAt": v.get("collectedAt")} for k, v in done.items()), key=lambda x: (x["score"] if isinstance(x["score"], (int, float)) else 101))
    first, last = (trend[0]["avg"], trend[-1]["avg"]) if len(trend) >= 2 else (None, None)
    return {
        "generatedAt": iso_now(), "days": days, "since": since,
        "fleet": {"servers": len(inv) or len(latest), "collected": len(done), "failed": sum(1 for v in latest.values() if v.get("collect") == "failed"),
                  "avgScore": _avg([v.get("score") for v in done.values()]), "status": status,
                  "p1": sum(v.get("p1") or 0 for v in done.values()), "trendDelta": (last - first) if first is not None and last is not None else None},
        "trend": trend, "worst": worst[:10], "topIssues": _top_issues(done),
        "automation": {"runs": len(period), "scheduled": sum(1 for r in period if r["trigger"] == "schedule"), "analyzed": analyzed, "aiAnalyzed": ai,
                       "hoursSaved": round(analyzed * MANUAL_MIN_PER_SERVER / 60, 1), "assumptionMin": MANUAL_MIN_PER_SERVER},
        "remediation": remediation, "vulns": vulns,
    }
