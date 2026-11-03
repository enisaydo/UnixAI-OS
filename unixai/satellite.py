"""Red Hat Satellite (6.17) entegrasyonu: CVE etki analizi ve filo güvenlik durumu

  Ayarlar → Satellite (data/integrations.json → "satellite", 0600):
    url           https://gbsatp04  (kısa ad çözülmüyorsa FQDN)
    user          salt-okunur API kullanıcısı (Viewer + errata/host görüntüleme)
    password      parola veya Personal Access Token (arayüzde maskeli)
    organization  boşsa kullanıcının gördüğü tüm organizasyonlar; ad, etiket veya numara
    staleDays     son check-in'i bundan eski olan host'un verisine güvenilmez (varsayılan 3)

  CVE akışı (yalnızca okuma; Satellite'ta ve sunucularda hiçbir şey değişmez):
    1) GET /katello/api/errata?search=cve = CVE-…          → CVE'yi kapatan errata (RHSA)
    2) GET /api/hosts?search=applicable_errata = RHSA-…     → etkilenen host'lar
       GET /api/hosts?search=installable_errata = RHSA-…    → yaması content view'de hazır olanlar
    3) Satellite'ta olmayan veya verisi eski Trinity sunucuları SSH ile doğrulanır: dnf updateinfo list --cve
    4) Rapor data/cve_reports.json'a yazılır; etkilenen sunucu MorpheusAI'ye gönderilebilir (playbook → onay → AAP)
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from .env import env
from .errors import ApiError
from .http_client import request
from .store import iso_now, parse_iso, read_json, write_json

FILE = "integrations.json"
REPORTS = "cve_reports.json"
MASK = "••••••••"
CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,7}$")
ADVISORY_RE = re.compile(r"^[A-Z]{4}-\d{4}:\d{1,6}$")
SEVERITY_RANK = {"Critical": 0, "Important": 1, "Moderate": 2, "Low": 3}
STATUS = {  # anahtar → (etiket, sıra)
    "affected_ready": ("Etkileniyor · yama hazır", 0),
    "affected_cv": ("Etkileniyor · yama content view'de yok", 1),
    "affected_ssh": ("Etkileniyor (SSH ile doğrulandı)", 2),
    "unreachable": ("Doğrulanamadı", 3),
    "stale": ("Satellite verisi eski", 4),
    "unregistered": ("Satellite'ta kayıtlı değil", 5),
    "clean_ssh": ("Etkilenmiyor (SSH ile doğrulandı)", 6),
    "clean": ("Etkilenmiyor / yamalı", 7),
}
_lock = threading.RLock()
_summary_cache: dict = {"at": 0.0, "data": None}


def _defaults() -> dict:
    return {"url": env("SATELLITE_URL", "https://gbsatp04"), "user": env("SATELLITE_USER"), "password": env("SATELLITE_PASSWORD"),
            "organization": env("SATELLITE_ORG"), "insecureTls": env("SATELLITE_TLS_INSECURE").lower() in ("1", "true", "yes"),
            "staleDays": 3, "timeoutMs": 60000}


def get() -> dict:
    return {**_defaults(), **((read_json(FILE, {}) or {}).get("satellite") or {})}


def is_configured() -> bool:
    c = get()
    return urlsplit(c["url"] or "").scheme == "https" and bool(c["user"]) and bool(c["password"])


def view() -> dict:
    c = get()
    return {**c, "password": MASK if c["password"] else "", "configured": is_configured(), "mask": MASK}


def save(data: dict, user=None) -> dict:
    cur = get()
    url = str(data.get("url") or "").strip().rstrip("/")
    if url and urlsplit(url).scheme != "https":
        raise ApiError("Satellite adresi https:// ile başlamalı (ör. https://gbsatp04)")
    password = cur["password"] if data.get("password") == MASK else str(data.get("password") or "")
    try:
        stale = max(1, min(30, int(data.get("staleDays") or 3)))
    except (TypeError, ValueError):
        raise ApiError("Check-in eskime süresi gün olarak bir sayı olmalı")
    nxt = {"url": url, "user": str(data.get("user") or "").strip()[:120], "password": password,
           "organization": str(data.get("organization") or "").strip()[:120], "insecureTls": bool(data.get("insecureTls")),
           "staleDays": stale, "timeoutMs": cur.get("timeoutMs", 60000), "updatedAt": iso_now(), "updatedBy": user}
    all_ = read_json(FILE, {}) or {}
    all_["satellite"] = nxt
    write_json(FILE, all_)
    _summary_cache.update(at=0.0, data=None)
    return view()


# ------------------------------------------------------------------
#  Satellite API
# ------------------------------------------------------------------
def _call(path: str, params=None, kind="satellite"):
    c = get()
    if not is_configured():
        raise ApiError("Satellite entegrasyonu yapılandırılmamış (Ayarlar → Satellite)", 400)
    auth = base64.b64encode(f"{c['user']}:{c['password']}".encode()).decode()
    url = c["url"] + path + (("&" if "?" in path else "?") + urlencode(params) if params else "")
    res = request("satellite", kind, url, headers={"accept": "application/json", "authorization": f"Basic {auth}"},
                  insecure_tls=c["insecureTls"], timeout_ms=c.get("timeoutMs", 60000),
                  tls_hint="Satellite CA'sını güvenin (curl -o /etc/pki/ca-trust/source/anchors/katello-ca.crt "
                           f"{c['url']}/pub/katello-server-ca.crt && update-ca-trust) veya Ayarlar → Satellite'ta doğrulamayı kapatın.")
    if res["status"] == 401:
        raise ApiError("Satellite 401: kullanıcı adı veya parola/token hatalı", 502)
    if res["status"] == 403:
        raise ApiError("Satellite 403: kullanıcının bu kaydı görme yetkisi yok (Viewer rolü + errata/host görüntüleme izni)", 502)
    if res["status"] >= 400:
        j = res["json"] if isinstance(res["json"], dict) else {}
        msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else j.get("displayMessage") or j.get("error")
        raise ApiError(f"Satellite {res['status']}: {msg or res['text'][:300]}", 502)
    if not isinstance(res["json"], (dict, list)):
        raise ApiError("Satellite JSON yerine başka bir yanıt döndü (adres doğru mu? ör. https://gbsatp04)", 502)
    return res["json"]


def _org_id():
    org = get()["organization"]
    if not org:
        return None
    if org.isdigit():
        return int(org)
    for o in _call("/katello/api/organizations", {"per_page": 200}, kind="satellite-orgs").get("results", []):
        if org.lower() in (str(o.get("name", "")).lower(), str(o.get("label", "")).lower()):
            return o["id"]
    raise ApiError(f"Satellite'ta '{org}' organizasyonu bulunamadı")


def _all(path: str, params: dict, kind: str, cap: int = 20000) -> list:
    """Sayfalı liste uçlarını sonuna kadar okur (per_page 500)"""
    out, page = [], 1
    while True:
        r = _call(path, {**params, "page": page, "per_page": 500}, kind=kind)
        rows = r.get("results") or []
        out += rows
        total = r.get("subtotal") if r.get("subtotal") is not None else r.get("total")
        if not rows or len(out) >= (total or 0) or len(out) >= cap:
            return out
        page += 1


def _scope() -> dict:
    oid = _org_id()
    return {"organization_id": oid} if oid else {}


def test() -> dict:
    st = _call("/api/status", kind="satellite-status")
    orgs = _call("/katello/api/organizations", {"per_page": 200}, kind="satellite-orgs").get("results", [])
    hosts = _call("/api/hosts", {**_scope(), "per_page": 1}, kind="satellite-hosts")
    return {"ok": True, "version": st.get("satellite_version") or st.get("version"), "apiVersion": st.get("api_version"),
            "organizations": [o.get("name") for o in orgs], "hosts": hosts.get("subtotal", hosts.get("total"))}


# ------------------------------------------------------------------
#  Host'lar ve Trinity envanteriyle eşleştirme
# ------------------------------------------------------------------
def _short(name) -> str:
    return str(name or "").lower().split(".")[0]


def _host_row(h: dict) -> dict:
    cf, sf = h.get("content_facet_attributes") or {}, h.get("subscription_facet_attributes") or {}
    cve_env = (cf.get("content_view_environments") or [{}])[0] if cf.get("content_view_environments") else {}
    return {"id": h.get("id"), "name": h.get("name"), "ip": h.get("ip"), "os": h.get("operatingsystem_name"),
            "lastCheckin": sf.get("last_checkin") or h.get("last_report"),
            "contentView": (cve_env.get("content_view") or {}).get("name") or cf.get("content_view_name") or (cf.get("content_view") or {}).get("name"),
            "lifecycle": (cve_env.get("lifecycle_environment") or {}).get("name") or cf.get("lifecycle_environment_name") or (cf.get("lifecycle_environment") or {}).get("name"),
            "securityErrata": (cf.get("errata_counts") or {}).get("security")}


def _is_stale(row: dict, days: int) -> bool:
    try:
        last = parse_iso(str(row["lastCheckin"]).replace(" UTC", "Z").replace(" ", "T"))
    except (TypeError, ValueError, KeyError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() > days * 86400


def _match(inv: list, sat_hosts: list) -> dict:
    """Trinity sunucu adı → Satellite host satırı (FQDN, kısa ad veya IP ile)"""
    by_name = {str(h["name"]).lower(): h for h in sat_hosts if h.get("name")}
    by_short, by_ip = {}, {h["ip"]: h for h in sat_hosts if h.get("ip")}
    for h in sat_hosts:
        by_short.setdefault(_short(h["name"]), h)
    out = {}
    for s in inv:
        host = str(s.get("host") or "").lower()
        out[s["name"]] = by_name.get(host) or by_ip.get(host) or by_name.get(s["name"].lower()) or by_short.get(_short(host)) or by_short.get(_short(s["name"]))
    return out


# ------------------------------------------------------------------
#  CVE etki analizi
# ------------------------------------------------------------------
def _errata_for(cve: str, scope: dict) -> list:
    rows = _all("/katello/api/errata", {**scope, "search": f"cve = {cve}"}, kind="satellite-errata")
    out = []
    for e in rows:
        if not ADVISORY_RE.match(str(e.get("errata_id") or "")):
            continue
        try:
            d = _call(f"/katello/api/errata/{e['id']}", kind="satellite-erratum")
        except ApiError:
            d = e
        out.append({"id": e["id"], "errataId": e["errata_id"], "title": e.get("title"), "severity": e.get("severity") or d.get("severity"),
                    "issued": e.get("issued"), "rebootSuggested": bool(d.get("reboot_suggested")),
                    "packages": sorted({re.sub(r"-[^-]+-[^-]+$", "", p) for p in (d.get("packages") or []) if isinstance(p, str)})[:30],
                    "cves": [c.get("cve_id") for c in (d.get("cves") or e.get("cves") or []) if isinstance(c, dict)][:50]})
    return sorted(out, key=lambda x: (SEVERITY_RANK.get(x["severity"], 9), x["errataId"]))


def _ssh_check(server: dict, cve: str) -> dict:
    from . import collector, settings

    ssh = settings.get()["ssh"]
    cmd = (f"export LC_ALL=C; (dnf -q updateinfo list --cve {cve} 2>/dev/null || yum -q updateinfo list --cve {cve} 2>/dev/null); "
           "echo __rc=$?")
    r = collector.run_ssh(server, {**ssh, "commandTimeout": min(ssh["commandTimeout"], 120)}, cmd, purpose=f"CVE doğrulama: {cve}")
    if r["timedOut"] or (r["code"] == 255 and "__rc=" not in r["stdout"]):
        return {"status": "unreachable", "error": "zaman aşımı" if r["timedOut"] else collector.explain(r["stderr"], server, ssh)}
    lines = [l for l in r["stdout"].splitlines() if l.strip() and not l.startswith("__rc=")]
    advisories = sorted({l.split()[0] for l in lines if ADVISORY_RE.match(l.split()[0])})
    return {"status": "affected_ssh" if advisories else "clean_ssh", "advisories": advisories, "output": "\n".join(lines)[-2000:]}


def analyze(cve: str, user=None, verify: bool = True) -> dict:
    from . import inventory, oplog

    cve = str(cve or "").strip().upper()
    if not CVE_RE.match(cve):
        raise ApiError("Geçersiz CVE numarası (ör. CVE-2024-6387)")
    t0 = time.monotonic()
    c, scope = get(), _scope()
    errata = _errata_for(cve, scope)
    applicable, installable = {}, {}
    for e in errata:
        for h in _all("/api/hosts", {**scope, "search": f"applicable_errata = {e['errataId']}"}, kind="satellite-hosts-applicable"):
            applicable.setdefault(h["id"], {"host": h, "errata": []})["errata"].append(e["errataId"])
        for h in _all("/api/hosts", {**scope, "search": f"installable_errata = {e['errataId']}"}, kind="satellite-hosts-installable"):
            installable.setdefault(h["id"], []).append(e["errataId"])
    sat_hosts = [_host_row(h) for h in _all("/api/hosts", scope, kind="satellite-hosts")]
    inv = [s for s in inventory.list_servers() if s.get("enabled", True)]
    matched = _match(inv, sat_hosts)
    inv_by_sat = {h["id"]: name for name, h in matched.items() if h}

    rows = []
    for h in sat_hosts:
        a = applicable.get(h["id"])
        if a:
            status = "affected_ready" if installable.get(h["id"]) else "affected_cv"
        else:
            status = "stale" if _is_stale(h, c["staleDays"]) else "clean"
        rows.append({**h, "satellite": True, "inventoryName": inv_by_sat.get(h["id"]), "status": status,
                     "errata": (a or {}).get("errata", []), "installable": installable.get(h["id"], [])})
    for s in inv:
        if not matched.get(s["name"]):
            rows.append({"name": s["name"], "inventoryName": s["name"], "ip": s.get("host"), "satellite": False, "status": "unregistered",
                         "errata": [], "installable": []})

    verified = 0
    if verify:
        inv_map = {s["name"]: s for s in inv}
        todo = [r for r in rows if r["status"] in ("stale", "unregistered") and r.get("inventoryName") in inv_map]
        from . import settings

        with ThreadPoolExecutor(max_workers=max(1, min(settings.get()["ssh"]["concurrency"], len(todo) or 1))) as pool:
            for r, res in zip(todo, pool.map(lambda r: _ssh_check(inv_map[r["inventoryName"]], cve), todo)):
                r["verify"] = {k: v for k, v in res.items() if k != "status"}
                r["verifiedFrom"] = r["status"]
                r["status"] = res["status"]
                verified += 1
    rows.sort(key=lambda r: (STATUS[r["status"]][1], str(r["name"])))
    counts = {k: 0 for k in STATUS}
    for r in rows:
        counts[r["status"]] += 1
    rep = {
        "id": f"{int(time.time() * 1000):x}", "cve": cve, "createdAt": iso_now(), "by": user, "satellite": urlsplit(c["url"]).netloc,
        "organization": c["organization"] or None, "errata": errata, "noErrata": not errata, "hosts": rows, "counts": counts,
        "affected": counts["affected_ready"] + counts["affected_cv"] + counts["affected_ssh"], "verified": verified,
        "severity": errata[0]["severity"] if errata else None, "durationMs": int((time.monotonic() - t0) * 1000),
    }
    with _lock:
        reps = read_json(REPORTS, []) or []
        reps.append(rep)
        write_json(REPORTS, reps[-300:])
    oplog.log("satellite", f"CVE etki analizi: {cve}", user=user, cve=cve, errata=[e["errataId"] for e in errata], affected=rep["affected"],
              hosts=len(rows), verified=verified, durationMs=rep["durationMs"])
    return rep


def reports(limit: int = 50) -> list:
    keep = ("id", "cve", "createdAt", "by", "severity", "affected", "counts", "noErrata", "verified", "durationMs")
    return [{k: r.get(k) for k in keep} | {"errata": [e["errataId"] for e in r.get("errata", [])]}
            for r in reversed((read_json(REPORTS, []) or [])[-limit:])]


def report(rep_id: str):
    return next((r for r in read_json(REPORTS, []) or [] if r["id"] == rep_id), None)


def latest_for(cve: str):
    return next((r for r in reversed(read_json(REPORTS, []) or []) if r["cve"] == cve), None)


# ------------------------------------------------------------------
#  Etkilenen sunucuyu MorpheusAI'ye gönder (playbook → insan onayı → AAP)
# ------------------------------------------------------------------
def send_remediation(rep_id: str, host_name: str, user=None) -> dict:
    from . import inventory, morpheus

    rep = report(rep_id)
    if not rep:
        raise ApiError("Rapor bulunamadı", 404)
    row = next((h for h in rep["hosts"] if h["name"] == host_name or h.get("inventoryName") == host_name), None)
    if not row:
        raise ApiError("Bu sunucu raporda yok", 404)
    if row["status"] == "affected_cv":
        raise ApiError("Yama bu sunucunun content view'inde yok: önce Satellite'ta content view'i yayınlayıp ortamına taşıyın (publish/promote), sonra yeniden analiz edin")
    advisories = row["installable"] or (row.get("verify") or {}).get("advisories") or []
    if row["status"] not in ("affected_ready", "affected_ssh") or not advisories:
        raise ApiError("Bu sunucu için uygulanabilir bir güvenlik güncellemesi yok")
    errata = {e["errataId"]: e for e in rep["errata"]}
    sev = rep.get("severity")
    reboot = any((errata.get(a) or {}).get("rebootSuggested") for a in advisories)
    pkgs = sorted({p for a in advisories for p in (errata.get(a) or {}).get("packages", [])})
    inv = inventory.get(row.get("inventoryName") or "") or {}
    name = row.get("inventoryName") or _short(row["name"])
    finding = {
        "title": f"{rep['cve']}: {', '.join(advisories)} güvenlik güncellemesini uygula",
        "priority": "P1" if sev in ("Critical", "Important") else "P2",
        "severity": "critical" if sev == "Critical" else "high" if sev == "Important" else "medium",
        "category": "security",
        "evidence": f"Satellite ({rep['satellite']}): {row['name']} için {', '.join(advisories)} uygulanabilir"
                    + (f"; etkilenen paketler: {', '.join(pkgs)}" if pkgs else "") + (" (SSH ile doğrulandı)" if row["status"] == "affected_ssh" else ""),
        "probable_cause": f"Sunucuda {rep['cve']} açığını kapatan güncelleme kurulu değil",
        "rationale": f"{rep['cve']} ({sev or 'önem derecesi bilinmiyor'}) güvenlik ekibinin takibinde; yama Satellite deposunda hazır.",
        "commands": [f"dnf -y update --advisory={a}" for a in advisories] + (["needs-restarting -r || systemctl reboot"] if reboot else []),
        "verification": f"dnf -q updateinfo list --cve {rep['cve']} boş dönmeli; Trinity → CVE Takibi'nde yeniden analiz edin",
        "risk": "Paket güncellemesi; servisler yeniden başlayabilir" + ("; Red Hat yeniden başlatma öneriyor" if reboot else ""),
        "anomalies": [],
    }
    server = {"name": name, "target": (row.get("ip") or row["name"]) if morpheus.get()["targetField"] == "host" else name,
              "host": inv.get("host") or row["name"], "os_release": row.get("os"), "tags": inv.get("tags", [])}
    rec = morpheus.send_finding(server, finding, user, source="trinity (CVE)", context={"cve": rep["cve"], "reportId": rep_id})
    with _lock:
        reps = read_json(REPORTS, []) or []
        r = next((x for x in reps if x["id"] == rep_id), None)
        if r:
            h = next((x for x in r["hosts"] if x["name"] == row["name"]), None)
            if h is not None:
                h["remediation"] = {"id": rec["id"], "status": rec.get("status"), "url": rec.get("url"), "sentAt": rec.get("sentAt"), "sentBy": user}
            write_json(REPORTS, reps)
    return rec


# ------------------------------------------------------------------
#  Genel Bakış için güvenlik özeti (10 dk önbellek)
# ------------------------------------------------------------------
def summary(refresh: bool = False) -> dict:
    if not is_configured():
        return {"configured": False}
    if not refresh and _summary_cache["data"] and time.monotonic() - _summary_cache["at"] < 600:
        return _summary_cache["data"]
    from . import inventory

    c, scope = get(), _scope()
    try:
        hosts = [_host_row(h) for h in _all("/api/hosts", scope, kind="satellite-hosts")]
        errata = _call("/katello/api/errata", {**scope, "errata_restrict_applicable": "true", "per_page": 8, "sort_by": "issued", "sort_order": "desc",
                                               "search": "type = security and (severity = Critical or severity = Important)"},
                       kind="satellite-errata-recent").get("results", [])
    except Exception as e:
        return {"configured": True, "error": str(e), "satellite": urlsplit(c["url"]).netloc}
    inv = [s for s in inventory.list_servers() if s.get("enabled", True)]
    matched = _match(inv, hosts)
    data = {
        "configured": True, "satellite": urlsplit(c["url"]).netloc, "fetchedAt": iso_now(),
        "hosts": len(hosts), "stale": sum(1 for h in hosts if _is_stale(h, c["staleDays"])),
        "withSecurityErrata": sum(1 for h in hosts if (h.get("securityErrata") or 0) > 0),
        "inventory": len(inv), "unregistered": [n for n, h in matched.items() if not h][:50],
        "recent": [{"errataId": e.get("errata_id"), "title": e.get("title"), "severity": e.get("severity"), "issued": e.get("issued"),
                    "hosts": e.get("hosts_applicable_count"), "installable": e.get("hosts_available_count"),
                    "cves": [x.get("cve_id") for x in (e.get("cves") or []) if isinstance(x, dict)][:5]} for e in errata],
        "reports": reports(5),
    }
    _summary_cache.update(at=time.monotonic(), data=data)
    return data


def status() -> dict:
    c = get()
    return {"configured": is_configured(), "host": urlsplit(c["url"]).netloc if c["url"] else None}
