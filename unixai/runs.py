"""Toplama + analiz çalıştırmaları ve günlük zamanlayıcı

  data/runs/YYYY-MM-DD/<runId>/run.json       özet: kim başlattı, hangi sunucular, durum, skorlar, gelen log bölümleri
  data/runs/YYYY-MM-DD/<runId>/<sunucu>.json  tam kayıt: ham komut çıktıları, metrikler, kural bulguları, AI analizi

  Türler: collect (SSH ile topla + analiz), reanalyze (son ham veriyi yeniden analiz et), demo (örnek veri)
  Aynı anda tek çalıştırma yürür. Zamanlayıcı her gün Ayarlar'daki saatte tüm etkin sunucuları toplar.
"""
from __future__ import annotations

import copy
import os
import re
import secrets
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import analyzer, collector, demo, genai, inventory, parser, rules, settings
from .errors import ApiError
from .store import day_of, file, iso_now, parse_iso, read_json, time_of, write_json

AI_CONCURRENCY = int(os.environ.get("AI_CONCURRENCY", "3"))
RUNS_DIR = file("runs")

_index = None  # run.json özetleri, en yeni önce
_active = None
_lock = threading.RLock()
_ai_sem = threading.BoundedSemaphore(AI_CONCURRENCY)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _load_index() -> list:
    global _index
    with _lock:
        if _index is None:
            _index = []
            if RUNS_DIR.exists():
                for d in sorted(RUNS_DIR.iterdir()):
                    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d.name):
                        continue
                    for rd in d.iterdir():
                        r = read_json(rd / "run.json")
                        if r:
                            if r["status"] == "running":  # servis yeniden başladıysa
                                r["status"] = "interrupted"
                            _index.append(r)
            _index.sort(key=lambda r: r["startedAt"], reverse=True)
        return _index


def _save_run(r: dict) -> None:
    with _lock:
        write_json(RUNS_DIR / r["date"] / r["id"] / "run.json", r)


def _save_server(r: dict, name: str, data: dict) -> None:
    write_json(RUNS_DIR / r["date"] / r["id"] / f"{_safe(name)}.json", data)


def _read_server(r: dict, name: str):
    return read_json(RUNS_DIR / r["date"] / r["id"] / f"{_safe(name)}.json")


def list_runs(limit: int = 100) -> list:
    with _lock:
        return copy.deepcopy(_load_index()[:limit])


def get(run_id: str):
    with _lock:
        r = _active if _active and _active["id"] == run_id else next((x for x in _load_index() if x["id"] == run_id), None)
        return copy.deepcopy(r)


def snapshot(name: str, run_id=None):
    """Bir sunucunun en son başarılı kaydı (veya belirli bir çalıştırmadaki kaydı)"""
    with _lock:
        runs = [x for x in _load_index() if x["id"] == run_id] if run_id else list(_load_index())
    for r in runs:
        e = next((s for s in r["servers"] if s["name"] == name and s["collect"] != "failed" and s.get("analysis") not in (None, "pending")), None)
        if e:
            d = _read_server(r, name)
            if d:
                return {"run": {"id": r["id"], "date": r["date"], "trigger": r["trigger"], "startedAt": r["startedAt"]}, **d}
    return None


def _previous(name: str, before: str):
    """Bu toplamadan önceki son başarılı metrikler (trend için)"""
    with _lock:
        runs = list(_load_index())
    for r in runs:
        e = next((s for s in r["servers"] if s["name"] == name and s.get("collectedAt") and s["collectedAt"] < before and s["collect"] != "failed"), None)
        if e:
            d = _read_server(r, name)
            if d and d.get("metrics"):
                return d
    return None


def history(name: str, limit: int = 60) -> list:
    """Sunucu zaman çizelgesi: grafikler ve geçmiş için (run.json'lardan, dosya okumadan)"""
    out = []
    with _lock:
        for r in _load_index():
            e = next((s for s in r["servers"] if s["name"] == name), None)
            if not e:
                continue
            out.append({"runId": r["id"], "time": e.get("collectedAt") or r["startedAt"], "trigger": r["trigger"], "collect": e["collect"],
                        "error": e.get("error"), "score": e.get("score"), "status": e.get("status"), "engine": e.get("engine"),
                        "findings": e.get("findings"), "p1": e.get("p1"), "headline": e.get("headline")})
            if len(out) >= limit:
                break
    return copy.deepcopy(out)


def latest_by_server() -> dict:
    """Her sunucunun son durumu (Genel Bakış)"""
    m = {}
    with _lock:
        for r in _load_index():
            for e in r["servers"]:
                if e["name"] not in m and e["collect"] != "pending":
                    m[e["name"]] = {**copy.deepcopy(e), "runId": r["id"], "trigger": r["trigger"], "date": r["date"]}
    return m


def latest_fleet():
    with _lock:
        r = next((x for x in _load_index() if x.get("fleet")), None)
        return {**copy.deepcopy(r["fleet"]), "runId": r["id"], "time": r.get("finishedAt") or r["startedAt"]} if r else None


# ------------------------------------------------------------------
#  Çalıştırma
# ------------------------------------------------------------------
def _line_count(t) -> int:
    return sum(1 for l in str(t or "").splitlines() if l.strip())


def _process_server(run: dict, src: dict, use_ai: bool):
    """Bir sunucunun ham verisini işler: parse → kurallar → AI (veya kural) analizi → kayıt"""
    with _lock:
        entry = next(s for s in run["servers"] if s["name"] == src["name"])
        entry.update({
            "collect": ("partial" if src.get("partial") else "ok") if src["ok"] else "failed",
            "error": src.get("error"),
            "durationMs": src.get("durationMs"),
            "collectedAt": src["collectedAt"],
            "sectionLines": {k: _line_count(v) for k, v in (src.get("sections") or {}).items()},
        })
        if not src["ok"]:
            entry["analysis"] = "skipped"
        else:
            entry["analysis"] = "running"
        _save_run(run)
    if not src["ok"]:
        _save_server(run, src["name"], {"server": src["name"], "host": src.get("host"), "collectedAt": src["collectedAt"], "ok": False,
                                        "error": src.get("error"), "sections": src.get("sections") or {}})
        from . import oplog

        oplog.log("toplama", "Sunucu toplanamadı", user=run["user"], run=run["id"], server=src["name"], host=src.get("host"), error=src.get("error") or "hata",
                  durationMs=src.get("durationMs"))
        return None

    s = settings.get()
    metrics = parser.parse(src["sections"])
    prev = _previous(src["name"], src["collectedAt"])
    days = (parse_iso(src["collectedAt"]) - parse_iso(prev["collectedAt"])).total_seconds() / 86400 if prev else 1
    findings = rules.evaluate(metrics, prev and prev["metrics"], s["thresholds"], days)
    head = parser.headline(metrics)
    prev_head = parser.headline(prev["metrics"]) if prev else None

    if use_ai:
        try:
            with _ai_sem:
                analysis = analyzer.analyze_server(src["name"], metrics, findings, prev_head, {"user": run["user"], "ip": run.get("ip")})
        except Exception as e:
            analysis = {**analyzer.rule_only(findings), "aiError": str(e)[:500]}
    else:
        analysis = analyzer.rule_only(findings)

    _save_server(run, src["name"], {
        "server": src["name"], "host": src.get("host"), "collectedAt": src["collectedAt"], "ok": True, "partial": bool(src.get("partial")),
        "missing": src.get("missing") or [], "stderr": src.get("stderr"), "durationMs": src.get("durationMs"),
        "sections": src["sections"], "metrics": metrics, "findings": findings, "analysis": analysis, "headline": head,
        "previous": {"collectedAt": prev["collectedAt"], "headline": prev_head} if prev else None,
    })
    with _lock:
        entry.update({
            "analysis": analysis["engine"], "aiError": analysis.get("aiError"), "score": analysis["health_score"], "status": analysis["status"],
            "engine": analysis["engine"], "findings": len(findings), "anomalies": len(analysis["anomalies"]),
            "actions": len(analysis["action_items"]), "p1": sum(1 for a in analysis["action_items"] if a["priority"] == "P1"),
            "summary": analysis["summary"][:400], "headline": head,
        })
        _save_run(run)
    from . import oplog

    oplog.log("toplama", "Sunucu toplandı ve analiz edildi", user=run["user"], run=run["id"], server=src["name"], score=analysis["health_score"],
              status=analysis["status"], engine=analysis["engine"], findings=len(findings), p1=entry["p1"], aiError=analysis.get("aiError"),
              durationMs=src.get("durationMs"), partial=bool(src.get("partial")) or None, quiet=True)
    return {"name": src["name"], "analysis": analysis}


def _new_run(trigger, user, ip, ai, servers, started: datetime | None = None) -> dict:
    now = started or datetime.now(timezone.utc)
    return {
        "id": f"{day_of(now).replace('-', '')}-{time_of(now).replace(':', '')}-{secrets.token_hex(3)}",
        "date": day_of(now),
        "startedAt": now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "finishedAt": None,
        "trigger": trigger, "user": user or "bilinmiyor", "ip": ip, "ai": bool(ai),
        "status": "running",
        "servers": [{"name": s["name"], "host": s.get("host"), "collect": "pending", "analysis": "pending"} for s in servers],
        "fleet": None, "error": None,
    }


def _finish(run: dict, results: list, use_ai: bool) -> None:
    ok = [r for r in results if r]
    fleet = None
    if len(ok) >= 2 and settings.get()["analysis"]["fleetSummary"]:
        if use_ai:
            try:
                fleet = analyzer.analyze_fleet(ok, {"user": run["user"], "ip": run.get("ip")})
            except Exception as e:
                fleet = {**analyzer.fleet_rule_only(ok), "aiError": str(e)[:500]}
        else:
            fleet = analyzer.fleet_rule_only(ok)
    with _lock:
        run["fleet"] = fleet
        failed = sum(1 for s in run["servers"] if s["collect"] == "failed")
        run["status"] = "failed" if failed == len(run["servers"]) else "partial" if failed else "done"
        run["finishedAt"] = iso_now()
        _save_run(run)
    if run["trigger"] in ("manual", "schedule"):
        try:
            from . import morpheus
            morpheus.auto_send(results, run["user"])
        except Exception as e:  # köprü hatası toplamayı bozmasın
            print(f"[morpheus] otomatik gönderim hatası: {e}", flush=True)


def _register(run: dict) -> None:
    with _lock:
        _load_index().append(run)
        _index.sort(key=lambda r: r["startedAt"], reverse=True)
        _save_run(run)


def _start(run: dict, target, register: bool = True) -> dict:
    """Arka plan iş parçacığında çalıştırır; aynı anda tek çalıştırma"""
    global _active

    def wrapper():
        global _active
        try:
            target()
        except Exception as e:
            traceback.print_exc()
            with _lock:
                run.update(status="failed", error=str(e), finishedAt=iso_now())
                _save_run(run)
        finally:
            with _lock:
                _active = None
            print(f"[run] {run['id']} {run['status']} ({len(run['servers'])} sunucu, AI: {'evet' if run['ai'] else 'hayır'})", flush=True)
            from . import oplog

            oplog.log("toplama", "Çalıştırma bitti", user=run["user"], run=run["id"], trigger=run["trigger"], status=run["status"], servers=len(run["servers"]),
                      failed=sum(1 for s in run["servers"] if s.get("collect") == "failed"), ok=run["status"] != "failed", error=run.get("error"), quiet=True)

    with _lock:
        if _active:
            raise ApiError(f"Şu anda bir çalıştırma sürüyor ({_active['id']}). Bitmesini bekleyin.", 409)
        _active = run
        if register:
            _register(run)
    from . import oplog

    oplog.log("toplama", "Çalıştırma başladı", user=run["user"], ip=run.get("ip"), run=run["id"], trigger=run["trigger"], ai=run["ai"],
              servers=[s["name"] for s in run["servers"]], quiet=True)
    threading.Thread(target=wrapper, name=f"run-{run['id']}", daemon=True).start()
    return copy.deepcopy(run)


def _use_ai(ai: bool) -> bool:
    return bool(ai) and settings.get()["analysis"]["ai"] and genai.is_configured()


def start_collect(servers=None, ai=True, trigger="manual", user=None, ip=None) -> dict:
    """Seçilen (veya tüm etkin) sunuculardan SSH ile toplar ve analiz eder"""
    inv = inventory.list_servers()
    targets = [s for s in inv if s["name"] in servers] if servers else [s for s in inv if s["enabled"]]
    if not targets:
        raise ApiError("Toplanacak sunucu yok. Sunucular ekranından sunucu ekleyin (veya Demo veri üretin).")
    if _active:
        raise ApiError(f"Şu anda bir çalıştırma sürüyor ({_active['id']}). Bitmesini bekleyin.", 409)
    use_ai = _use_ai(ai)
    run = _new_run(trigger, user, ip, use_ai, targets)
    _cleanup()

    def target():
        jobs = []
        with ThreadPoolExecutor(max_workers=max(AI_CONCURRENCY, 1) * 2) as pool:
            collector.collect_all(targets, settings.get()["ssh"], lambda r: jobs.append(pool.submit(
                _process_server, run, {**r, "name": r["server"], "collectedAt": iso_now()}, use_ai)))
            results = []
            for j in jobs:
                try:
                    results.append(j.result())
                except Exception:
                    traceback.print_exc()
                    results.append(None)
        _finish(run, results, use_ai)

    return _start(run, target)


def start_reanalyze(servers=None, ai=True, user=None, ip=None) -> dict:
    """Sunucuların son ham verisini (SSH'a gitmeden) yeniden analiz eder"""
    names = [n for n in latest_by_server() if not servers or n in servers]
    sources = [s for s in (snapshot(n) for n in names) if s and s.get("ok")]
    if not sources:
        raise ApiError("Yeniden analiz edilecek ham veri yok. Önce toplama yapın.")
    if _active:
        raise ApiError(f"Şu anda bir çalıştırma sürüyor ({_active['id']}). Bitmesini bekleyin.", 409)
    use_ai = _use_ai(ai)
    run = _new_run("reanalyze", user, ip, use_ai, [{"name": s["server"], "host": s.get("host")} for s in sources])

    def target():
        with ThreadPoolExecutor(max_workers=max(AI_CONCURRENCY, 1)) as pool:
            futs = [pool.submit(_process_server, run, {"name": s["server"], "host": s.get("host"), "ok": True, "sections": s["sections"],
                                                       "collectedAt": s["collectedAt"], "durationMs": s.get("durationMs"),
                                                       "partial": s.get("partial"), "missing": s.get("missing")}, use_ai) for s in sources]
            results = [f.result() for f in futs]
        _finish(run, results, use_ai)

    return _start(run, target)


def start_demo(ai=True, user=None, ip=None) -> dict:
    """Demo: 4 örnek RHEL sunucusu için son 7 günün verisi (trend grafikleri dolsun diye).
    Geçmiş günler yalnızca kural motoruyla, bugün (GenAI bağlıysa) AI ile analiz edilir."""
    if _active:
        raise ApiError(f"Şu anda bir çalıştırma sürüyor ({_active['id']}). Bitmesini bekleyin.", 409)
    use_ai = _use_ai(ai)
    now = datetime.now(timezone.utc)
    first = _new_run("demo", user, ip, use_ai, demo.build(0, now), started=now)

    def target():
        for d in range(6, -1, -1):
            at = now - timedelta(days=d)
            servers = demo.build(d, at)
            run = first if d == 0 else _new_run("demo", user, ip, False, servers, started=at)
            _register(run)
            collected = at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            with ThreadPoolExecutor(max_workers=max(AI_CONCURRENCY, 1)) as pool:
                futs = [pool.submit(_process_server, run, {"name": s["name"], "host": s["host"], "ok": True, "sections": s["sections"],
                                                           "collectedAt": collected, "durationMs": 900 + len(s["name"]) * 37}, d == 0 and use_ai)
                        for s in servers]
                results = [f.result() for f in futs]
            _finish(run, results, d == 0 and use_ai)
            if d:  # geçmiş günler için gerçekçi süre
                with _lock:
                    run["finishedAt"] = (at + timedelta(seconds=4)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    _save_run(run)

    return _start(first, target, register=False)


def _cleanup() -> None:
    """Saklama süresinden eski gün dizinlerini siler"""
    global _index
    limit = day_of(datetime.now(timezone.utc) - timedelta(days=settings.get()["analysis"]["retentionDays"]))
    if not RUNS_DIR.exists():
        return
    for d in RUNS_DIR.iterdir():
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d.name) and d.name < limit:
            shutil.rmtree(d, ignore_errors=True)
            with _lock:
                if _index is not None:
                    _index = [r for r in _index if r["date"] != d.name]


# ------------------------------------------------------------------
#  Zamanlayıcı
# ------------------------------------------------------------------
def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _tick() -> None:
    s = settings.get()["schedule"]
    if not s["enabled"] or _active:
        return
    today, now, at = day_of(), _minutes(time_of()), _minutes(s["time"])
    # Planlanan saatten sonraki 15 dk içinde ve bugün henüz çalışmadıysa (servis o dakikada kapalı olabilir)
    if s.get("lastRunDate") == today or now < at or now > at + 15:
        return
    settings.mark_scheduled(today)
    if not any(x["enabled"] for x in inventory.list_servers()):
        print("[zamanlayıcı] etkin sunucu yok, atlandı", flush=True)
        return
    try:
        start_collect(trigger="schedule", user="zamanlayıcı")
    except Exception as e:
        print(f"[zamanlayıcı] {e}", flush=True)


def start_scheduler() -> None:
    def loop():
        time.sleep(5)
        while True:
            try:
                _tick()
            except Exception:
                traceback.print_exc()
            time.sleep(30)
    threading.Thread(target=loop, name="scheduler", daemon=True).start()


def scheduler_status() -> dict:
    s = settings.get()["schedule"]
    with _lock:
        act = {"id": _active["id"], "trigger": _active["trigger"]} if _active else None
    return {"enabled": s["enabled"], "time": s["time"], "lastRunDate": s.get("lastRunDate"), "active": act}
