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


# ------------------------------------------------------------------
#  Sunucu bazında zafiyet torbası (data/vuln_bags.json)
#    Satellite: GET /katello/api/errata?host_id=…&errata_restrict_applicable=true  (+ installable)
#    Satellite'ta olmayan / verisi eski sunucu: dnf updateinfo list --security [--with-cve] (SSH)
#    Her errata'nın torbaya ilk girdiği an tutulur; kapananlar "closed" geçmişine düşer.
# ------------------------------------------------------------------
BAGS = "vuln_bags.json"
SEVERITIES = ("Critical", "Important", "Moderate", "Low")
SLA_DAYS = {"Critical": 15, "Important": 30, "Moderate": 90, "Low": 180}  # yayın tarihinden itibaren kapatma süresi


def _sev(s):
    s = str(s or "").split("/")[0].strip().capitalize()
    return s if s in SEVERITY_RANK else None


def _days_since(t):
    t = str(t or "")
    if len(t) < 10:
        return None
    try:
        d = parse_iso(t[:10] + "T00:00:00Z")
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - d).total_seconds() // 86400))


def _sat_host_errata(host_id) -> list:
    base = {"host_id": host_id, "search": "type = security"}
    rows = _all("/katello/api/errata", {**base, "errata_restrict_applicable": "true"}, kind="satellite-host-errata")
    inst = {e.get("errata_id") for e in _all("/katello/api/errata", {**base, "errata_restrict_installable": "true"}, kind="satellite-host-errata")}
    return [{"errataId": e.get("errata_id"), "title": e.get("title"), "severity": _sev(e.get("severity")), "issued": (e.get("issued") or "")[:10] or None,
             "rebootSuggested": bool(e.get("reboot_suggested")), "installable": e.get("errata_id") in inst,
             "cves": sorted({c.get("cve_id") for c in (e.get("cves") or []) if isinstance(c, dict) and c.get("cve_id")})}
            for e in rows if ADVISORY_RE.match(str(e.get("errata_id") or ""))]


def _ssh_host_errata(server: dict) -> dict:
    """dnf'in gördüğü (etkin depolardaki) güvenlik güncellemeleri; CVE'ler paket üzerinden errata'ya bağlanır"""
    from . import collector, settings

    ssh = settings.get()["ssh"]
    cmd = ("export LC_ALL=C; (dnf -q updateinfo list --security 2>/dev/null || yum -q updateinfo list security 2>/dev/null); echo __sep; "
           "(dnf -q updateinfo list --security --with-cve 2>/dev/null || yum -q updateinfo list cves 2>/dev/null); echo __rc=$?")
    r = collector.run_ssh(server, {**ssh, "commandTimeout": min(ssh["commandTimeout"], 180)}, cmd, purpose="Zafiyet torbası: güvenlik güncellemeleri")
    if r["timedOut"] or (r["code"] == 255 and "__rc=" not in r["stdout"]):
        return {"error": "zaman aşımı" if r["timedOut"] else collector.explain(r["stderr"], server, ssh)}
    adv_part, _, cve_part = r["stdout"].partition("__sep")
    by_adv, by_pkg = {}, {}
    for line in adv_part.splitlines():
        p = line.split()
        if len(p) >= 3 and ADVISORY_RE.match(p[0]):
            e = by_adv.setdefault(p[0], {"errataId": p[0], "title": None, "severity": _sev(p[1]), "issued": None, "rebootSuggested": False,
                                         "installable": True, "cves": set(), "packages": set()})
            e["packages"].add(re.sub(r"-[^-]+-[^-]+$", "", p[2]))
            by_pkg[p[2]] = p[0]
    for line in cve_part.splitlines():
        p = line.split()
        if len(p) >= 3 and p[0].startswith("CVE-") and by_pkg.get(p[2]) in by_adv:
            by_adv[by_pkg[p[2]]]["cves"].add(p[0])
    return {"errata": [{**e, "cves": sorted(e["cves"]), "packages": sorted(e["packages"])[:30]} for e in by_adv.values()]}


def _overdue(e) -> bool:
    age = _days_since(e.get("issued"))
    return age is not None and e.get("severity") in SLA_DAYS and age > SLA_DAYS[e["severity"]]


def _bag_summary(errata: list) -> dict:
    counts = {s: 0 for s in SEVERITIES}
    for e in errata:
        if e["severity"] in counts:
            counts[e["severity"]] += 1
    seen = [d for d in (_days_since(e.get("firstSeen")) for e in errata) if d is not None]
    return {"counts": counts, "errata": len(errata), "cves": len({c for e in errata for c in e["cves"]}),
            "installable": sum(1 for e in errata if e["installable"]), "overdue": sum(1 for e in errata if _overdue(e)),
            "rebootSuggested": any(e["rebootSuggested"] for e in errata), "oldestDays": max(seen, default=None)}


def _merge(prev, bag: dict) -> dict:
    """Önceki torbayla birleştirir: ilk görülme tarihi korunur, artık görünmeyen errata 'kapandı' olur"""
    now = iso_now()
    prev = prev or {}
    if bag.get("error"):  # bu taramada veri alınamadı: eski içeriği koru, hatayı göster
        return {**prev, **bag, "errata": prev.get("errata", []), "closed": prev.get("closed", []),
                "summary": prev.get("summary") or _bag_summary([]), "source": prev.get("source"), "fetchedAt": prev.get("fetchedAt")}
    old = {e["errataId"]: e for e in prev.get("errata", [])}
    cur = {e["errataId"] for e in bag["errata"]}
    for e in bag["errata"]:
        e["firstSeen"] = (old.get(e["errataId"]) or {}).get("firstSeen") or now
    closed = [{"errataId": k, "severity": e.get("severity"), "cves": e.get("cves", []), "firstSeen": e.get("firstSeen"), "closedAt": now}
              for k, e in old.items() if k not in cur]
    bag["errata"].sort(key=lambda e: (SEVERITY_RANK.get(e["severity"], 9), e.get("issued") or "", e["errataId"]))
    bag["closed"] = (closed + prev.get("closed", []))[:100]
    bag["summary"] = _bag_summary(bag["errata"])
    if prev.get("remediation"):
        bag["remediation"] = prev["remediation"]
    return bag


def _collect_bag(key: str, sat, srv, stale_days: int, verify: bool) -> dict:
    meta = {"key": key, "name": (sat or {}).get("name") or key, "inventoryName": (srv or {}).get("name"), "satellite": bool(sat),
            "checkedAt": iso_now(), "error": None}
    if sat:
        meta.update({k: sat.get(k) for k in ("ip", "os", "contentView", "lifecycle", "lastCheckin")})
        meta["stale"] = _is_stale(sat, stale_days)
    if sat and not (meta["stale"] and verify and srv):
        try:
            return {**meta, "source": "satellite", "errata": _sat_host_errata(sat["id"]), "fetchedAt": iso_now()}
        except ApiError as e:
            if not (verify and srv):
                return {**meta, "error": str(e)}
    if srv and verify:
        r = _ssh_host_errata(srv)
        if r.get("error"):
            return {**meta, "error": f"SSH: {r['error']}"}
        return {**meta, "source": "ssh", "errata": r["errata"], "fetchedAt": iso_now()}
    return {**meta, "error": "Satellite'ta kayıtlı değil; SSH doğrulaması kapalı"}


def _targets() -> list:
    """[(anahtar, satellite host, envanter sunucusu)] — envanterdekiler kendi adıyla, yalnızca Satellite'ta olanlar host adıyla"""
    from . import inventory

    sat_hosts = [_host_row(h) for h in _all("/api/hosts", _scope(), kind="satellite-hosts")] if is_configured() else []
    inv = [s for s in inventory.list_servers() if s.get("enabled", True)]
    matched = _match(inv, sat_hosts)
    used = {h["id"] for h in matched.values() if h}
    return [(s["name"], matched.get(s["name"]), s) for s in inv] + [(h["name"], h, None) for h in sat_hosts if h["id"] not in used]


def scan_bags(user=None, verify: bool = True, only=None) -> dict:
    from . import oplog, settings

    t0 = time.monotonic()
    targets = _targets()
    if only:
        targets = [t for t in targets if only in (t[0], (t[1] or {}).get("name"))]
        if not targets:
            raise ApiError("Bu sunucu ne Trinity envanterinde ne de Satellite'ta var", 404)
    stale_days = get()["staleDays"]
    workers = max(1, min(settings.get()["ssh"]["concurrency"], 8, len(targets) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        new = list(pool.map(lambda t: _collect_bag(t[0], t[1], t[2], stale_days, verify), targets))
    with _lock:
        store = read_json(BAGS, {}) or {}
        hosts = store.get("hosts") or {}
        for b in new:
            hosts[b["key"]] = _merge(hosts.get(b["key"]), b)
        if not only:  # artık ne envanterde ne Satellite'ta olanlar torbadan çıkar
            keep = {b["key"] for b in new}
            hosts = {k: v for k, v in hosts.items() if k in keep}
            store.update(lastScan=iso_now(), scanBy=user, scanMs=int((time.monotonic() - t0) * 1000))
        store["hosts"] = hosts
        write_json(BAGS, store)
    oplog.log("satellite", "Zafiyet torbası taraması" + (f": {only}" if only else ""), user=user, hosts=len(new),
              errors=sum(1 for b in new if b.get("error")), ssh=sum(1 for b in new if b.get("source") == "ssh"),
              durationMs=int((time.monotonic() - t0) * 1000))
    return bag(new[0]["key"]) if only else bags()


def bags() -> dict:
    store = read_json(BAGS, {}) or {}
    hosts = list((store.get("hosts") or {}).values())
    keep = ("key", "name", "inventoryName", "satellite", "source", "stale", "os", "contentView", "lifecycle", "lastCheckin", "fetchedAt",
            "checkedAt", "error", "summary", "remediation")
    rank = lambda h: tuple(-((h.get("summary") or {}).get("counts") or {}).get(s, 0) for s in SEVERITIES) + (h["key"],)
    items = sorted(({k: h.get(k) for k in keep} for h in hosts), key=rank)
    cves = {}
    for h in hosts:
        for e in h.get("errata", []):
            for cv in e["cves"] or [e["errataId"]]:
                x = cves.setdefault(cv, {"id": cv, "severity": e["severity"], "hosts": set(), "errata": set(), "installable": set()})
                x["hosts"].add(h["key"])
                x["errata"].add(e["errataId"])
                if e["installable"]:
                    x["installable"].add(h["key"])
                if SEVERITY_RANK.get(e["severity"], 9) < SEVERITY_RANK.get(x["severity"], 9):
                    x["severity"] = e["severity"]
    top = sorted(cves.values(), key=lambda x: (SEVERITY_RANK.get(x["severity"], 9), -len(x["hosts"]), x["id"]))[:25]
    sm = lambda h, k: (h.get("summary") or {}).get(k) or 0
    return {"lastScan": store.get("lastScan"), "scanBy": store.get("scanBy"), "scanMs": store.get("scanMs"), "items": items,
            "sla": SLA_DAYS, "configured": is_configured(),
            "totals": {"hosts": len(hosts), "withVulns": sum(1 for h in hosts if sm(h, "errata")), "errors": sum(1 for h in hosts if h.get("error")),
                       "counts": {s: sum(((h.get("summary") or {}).get("counts") or {}).get(s, 0) for h in hosts) for s in SEVERITIES},
                       "overdue": sum(sm(h, "overdue") for h in hosts), "installable": sum(sm(h, "installable") for h in hosts), "cves": len(cves)},
            "topCves": [{**x, "hosts": len(x["hosts"]), "installable": len(x["installable"]), "errata": sorted(x["errata"])[:5]} for x in top]}


def bag(key: str):
    hosts = (read_json(BAGS, {}) or {}).get("hosts") or {}
    h = hosts.get(key) or next((b for b in hosts.values() if key in (b.get("name"), b.get("inventoryName"))), None)
    if h is None:
        return None
    for e in h.get("errata", []):
        e["age"] = _days_since(e.get("issued"))
        e["overdue"] = _overdue(e)
    return {**h, "sla": SLA_DAYS}


def send_bag(key: str, severities=None, user=None, advisories=None) -> dict:
    """Torbadaki yaması hazır güvenlik errata'larını (seçilen önem dereceleri veya belirli errata'lar) tek bulgu olarak MorpheusAI'ye gönderir"""
    from . import inventory, morpheus

    b = bag(key)
    if not b:
        raise ApiError("Bu sunucu için zafiyet torbası yok; önce tarayın", 404)
    sev = [s for s in (severities or []) if s in SEVERITIES] or ["Critical", "Important"]
    only = {str(a).upper() for a in (advisories or [])}
    pick = [e for e in b.get("errata", []) if e["installable"] and (e["errataId"] in only if only else e["severity"] in sev)]
    if only and not pick:
        raise ApiError(f"{', '.join(sorted(only))}: bu sunucuda yaması hazır değil (content view'de yok veya zaten kapanmış)")
    if not pick:
        cv = any(not e["installable"] and e["severity"] in sev for e in b.get("errata", []))
        raise ApiError(f"{'/'.join(sev)} önem derecesinde yaması hazır errata yok"
                       + ("; yaması content view'de olmayanlar için Satellite'ta publish/promote gerekir" if cv else ""))
    adv = [e["errataId"] for e in pick]
    cves = sorted({c for e in pick for c in e["cves"]})
    reboot = any(e["rebootSuggested"] for e in pick)
    top = min((e["severity"] for e in pick), key=lambda s: SEVERITY_RANK[s])
    inv = inventory.get(b.get("inventoryName") or "") or {}
    name = b.get("inventoryName") or _short(b["name"])
    src = f"Satellite {urlsplit(get()['url']).netloc}" if b.get("source") == "satellite" else "SSH / dnf updateinfo"
    finding = {
        "title": f"{adv[0]} güvenlik güncellemesini uygula" if only and len(adv) == 1 else f"{len(adv)} güvenlik güncellemesini uygula ({'/'.join(sev)})",
        "priority": "P1" if top in ("Critical", "Important") else "P2",
        "severity": "critical" if top == "Critical" else "high" if top == "Important" else "medium",
        "category": "security",
        "evidence": f"Zafiyet torbası ({src}): {', '.join(adv[:40])}" + (f"; CVE: {', '.join(cves[:40])}{' …' if len(cves) > 40 else ''}" if cves else ""),
        "probable_cause": "Sunucuda yayınlanmış güvenlik güncellemeleri kurulu değil",
        "rationale": f"{len(cves)} CVE açık; {sum(1 for e in pick if e['overdue'])} errata kapatma süresini (SLA) aştı.",
        "commands": ["dnf -y update " + " ".join(f"--advisory={a}" for a in adv)] + (["needs-restarting -r || systemctl reboot"] if reboot else []),
        "verification": "dnf -q updateinfo list --security çıktısında bu errata'lar kalmamalı; Trinity → Zafiyet Torbası'nda sunucuyu yeniden tarayın",
        "risk": "Paket güncellemesi; servisler yeniden başlayabilir" + ("; Red Hat yeniden başlatma öneriyor" if reboot else ""),
        "anomalies": [],
    }
    server = {"name": name, "target": (b.get("ip") or b["name"]) if morpheus.get()["targetField"] == "host" else name,
              "host": inv.get("host") or b["name"], "os_release": b.get("os"), "tags": inv.get("tags", [])}
    rec = morpheus.send_finding(server, finding, user, source="trinity (zafiyet torbası)", context={"advisories": adv, "cves": cves[:200]})
    with _lock:
        store = read_json(BAGS, {}) or {}
        h = (store.get("hosts") or {}).get(b["key"])
        if h is not None:
            h["remediation"] = {"id": rec["id"], "status": rec.get("status"), "url": rec.get("url"), "sentAt": iso_now(), "sentBy": user, "advisories": adv}
            write_json(BAGS, store)
    return rec


# ------------------------------------------------------------------
#  Errata içeriği: açıklama, çözüm, CVE / Bugzilla, düzelten paketler
#    Satellite: GET /katello/api/errata?search=errata_id = RHSA-…  →  GET /katello/api/errata/:id
#    Satellite'ta yoksa: dnf updateinfo info RHSA-… (SSH)
#    Errata içeriği yayından sonra nadiren değişir; data/errata_cache.json'da 7 gün tutulur.
# ------------------------------------------------------------------
ERRATA_CACHE = "errata_cache.json"
CACHE_DAYS = 7


def _text(v) -> str:
    return re.sub(r"\n{3,}", "\n\n", str(v or "").replace("\r\n", "\n")).strip()


def _sat_erratum(adv: str):
    rows = _call("/katello/api/errata", {"search": f"errata_id = {adv}", "per_page": 5}, kind="satellite-errata").get("results") or []
    row = next((e for e in rows if e.get("errata_id") == adv), None)
    if not row:
        return None
    d = _call(f"/katello/api/errata/{row['id']}", kind="satellite-erratum")
    return {"advisory": adv, "satelliteId": row["id"], "source": "satellite", "title": d.get("title"), "type": d.get("type"),
            "severity": _sev(d.get("severity")), "issued": (d.get("issued") or "")[:10] or None, "updated": (d.get("updated") or "")[:10] or None,
            "rebootSuggested": bool(d.get("reboot_suggested")), "summary": _text(d.get("summary")), "description": _text(d.get("description")),
            "solution": _text(d.get("solution")),
            "cves": sorted({c.get("cve_id") for c in (d.get("cves") or []) if isinstance(c, dict) and c.get("cve_id")}),
            "bugs": [{"id": str(b.get("bug_id")), "title": b.get("title") or "", "url": b.get("href")} for b in (d.get("bugs") or []) if isinstance(b, dict) and b.get("bug_id")][:100],
            "packages": sorted({p for p in (d.get("packages") or []) if isinstance(p, str)})[:400],
            "moduleStreams": [f"{m.get('name')}:{m.get('stream')}" for m in (d.get("module_streams") or []) if isinstance(m, dict)][:50],
            "hostsApplicable": d.get("hosts_applicable_count"), "hostsAvailable": d.get("hosts_available_count")}


def _parse_updateinfo(adv: str, out: str):
    """dnf updateinfo info çıktısı: '  Anahtar : değer' satırları, devamı '         : …'"""
    lines, title, fields, cur = out.splitlines(), None, {}, None
    for i, line in enumerate(lines):
        if line.startswith("====") and i + 2 < len(lines) and lines[i + 2].startswith("===="):
            title = lines[i + 1].strip()
            continue
        m = re.match(r"^\s*([A-Za-z][A-Za-z ]*?)\s*:\s?(.*)$", line)
        c = re.match(r"^\s+:\s?(.*)$", line)
        if c and cur:
            fields[cur].append(c.group(1))
        elif m:
            cur = m.group(1).strip().lower()
            fields.setdefault(cur, []).append(m.group(2))
    if not fields.get("update id") and not title:
        return None
    one = lambda k: (fields.get(k) or [""])[0].strip() or None
    cves = sorted({c for line in fields.get("cves", []) for c in re.findall(r"CVE-\d{4}-\d{4,7}", line)})
    bugs = [{"id": m.group(1), "title": m.group(2).strip(), "url": f"https://bugzilla.redhat.com/show_bug.cgi?id={m.group(1)}"}
            for m in (re.match(r"^\s*(\d+)\s*-\s*(.*)$", l) for l in fields.get("bugs", [])) if m][:100]
    return {"advisory": adv, "source": "ssh", "title": title, "type": one("type"), "severity": _sev(one("severity")),
            "issued": (one("issued") or "")[:10] or None, "updated": (one("updated") or "")[:10] or None,
            "rebootSuggested": bool(re.search(r"reboot", " ".join(fields.get("reboot", [])), re.I)) or None,
            "summary": "", "description": _text("\n".join(fields.get("description", []))), "solution": "", "cves": cves, "bugs": bugs,
            "packages": [], "moduleStreams": []}


def _ssh_erratum(server: dict, adv: str):
    from . import collector, settings

    ssh = settings.get()["ssh"]
    cmd = f"export LC_ALL=C; (dnf -q updateinfo info {adv} 2>/dev/null || yum -q updateinfo info {adv} 2>/dev/null); echo __rc=$?"
    r = collector.run_ssh(server, {**ssh, "commandTimeout": min(ssh["commandTimeout"], 120)}, cmd, purpose=f"Errata içeriği: {adv}")
    if r["timedOut"] or (r["code"] == 255 and "__rc=" not in r["stdout"]):
        raise ApiError("SSH: " + ("zaman aşımı" if r["timedOut"] else collector.explain(r["stderr"], server, ssh)), 502)
    return _parse_updateinfo(adv, r["stdout"].split("__rc=")[0])


def _host_fix(adv: str, d: dict, b) -> dict:
    """Bu errata'yı bu sunucuda kapatmanın somut adımları"""
    host = (b or {}).get("name")
    e = next((x for x in (b or {}).get("errata", []) if x["errataId"] == adv), None) or {}
    reboot = bool(d.get("rebootSuggested") or e.get("rebootSuggested"))
    steps = [{"title": "Sunucuda (dnf)", "commands": [f"dnf -y update --advisory={adv}"] + (["needs-restarting -r || systemctl reboot"] if reboot else [])},
             {"title": "Önce neyin değişeceğini görmek için", "commands": [f"dnf updateinfo info {adv}", f"dnf -q check-update --advisory={adv}"]}]
    if b and b.get("satellite"):
        steps.append({"title": "Satellite'tan (Remote Execution)", "commands": [
            f'hammer job-invocation create --feature katello_errata_install --inputs errata={adv} --search-query "name = {host}"']})
    steps.append({"title": "Doğrulama", "commands": [f"dnf -q updateinfo list --advisory={adv}   # boş dönmeli"]
                  + ([f"rpm -q --changelog {e['packages'][0]} | grep -m3 CVE-"] if e.get("packages") else [])})
    return {"installable": e.get("installable"), "rebootSuggested": reboot, "packages": e.get("packages") or [], "firstSeen": e.get("firstSeen"),
            "age": _days_since(e.get("issued") or d.get("issued")), "overdue": _overdue({**d, **e}) if (e or d).get("severity") else False,
            "sla": SLA_DAYS.get(e.get("severity") or d.get("severity")), "steps": steps,
            "note": None if e.get("installable") is not False else "Yama bu sunucunun content view'inde yok: Satellite'ta content view'i publish edip "
                                                                   "lifecycle ortamına promote edin (veya errata'yı incremental update ile ekleyin), sonra yukarıdaki adımları uygulayın."}


def erratum(adv: str, key=None, refresh: bool = False) -> dict:
    """Errata'nın içeriği (açıklama, çözüm, CVE, paketler) + istenirse belirli sunucu için düzeltme adımları"""
    from . import inventory

    adv = str(adv or "").strip().upper()
    if not ADVISORY_RE.match(adv):
        raise ApiError("Geçersiz errata numarası (ör. RHSA-2024:1234)")
    b = bag(key) if key else None
    with _lock:
        cache = read_json(ERRATA_CACHE, {}) or {}
    d = cache.get(adv)
    if refresh or not d or (_days_since(d.get("cachedAt")) or 0) >= CACHE_DAYS:
        d, err = None, None
        if is_configured():
            try:
                d = _sat_erratum(adv)
            except ApiError as e:
                err = str(e)
        srv = inventory.get((b or {}).get("inventoryName") or key or "") if not d else None
        if not d and srv:
            try:
                d = _ssh_erratum(srv, adv)
            except ApiError as e:
                err = str(e)
        if not d:
            old = cache.get(adv)
            if old:
                d = {**old, "warning": f"Güncel içerik alınamadı, önbellekteki gösteriliyor: {err}" if err else None}
            else:
                raise ApiError(err or f"{adv} Satellite'ta bulunamadı" + ("" if is_configured() else "; Satellite yapılandırılmamış ve sunucu SSH ile sorgulanamadı"), 404)
        else:
            d["cachedAt"] = iso_now()
            with _lock:
                cache = read_json(ERRATA_CACHE, {}) or {}
                cache[adv] = d
                if len(cache) > 1000:
                    cache = dict(sorted(cache.items(), key=lambda kv: kv[1].get("cachedAt") or "")[-800:])
                write_json(ERRATA_CACHE, cache)
    c = get()
    links = {"redhat": f"https://access.redhat.com/errata/{adv}"}
    if d.get("satelliteId") and c.get("url"):
        links["satellite"] = f"{c['url']}/errata/{d['satelliteId']}"
    out = {**d, "links": links, "cveLinks": {cv: f"https://access.redhat.com/security/cve/{cv}" for cv in d.get("cves", [])}}
    if b:
        out["host"] = {"key": b["key"], "name": b.get("name"), "source": b.get("source"), **_host_fix(adv, d, b)}
    return out
