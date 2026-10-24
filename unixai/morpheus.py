"""MorpheusAI köprüsü: UnixAI OS bulgularını düzeltme playbook'u için MorpheusAI'ye gönderir

  Ayarlar → MorpheusAI entegrasyonu (data/integrations.json, 0600):
    url          http://morpheus.kurum.local:3000
    token        MorpheusAI .env → MORPHEUS_API_TOKEN ile aynı (arayüzde maskeli)
    targetField  AAP envanterindeki host adı: "name" (UnixAI sunucu adı) veya "host" (adres)
    autoSend     toplama bitince P1 (veya P1+P2) aksiyonları otomatik gönder

  Akış: POST {url}/api/remediations → MorpheusAI standartlara uygun playbook üretir → İNSAN ONAYI → AAP (check → run)
  Gönderilen kayıtlar data/remediations.json'da tutulur; durum MorpheusAI'den yenilenir.

  Onay aktarımı: UnixAI ekranında bir kişi "Onayla / Reddet / Sunucuya uygula" dediğinde istek MorpheusAI'ye
  X-Approved-By: <kişi> başlığıyla iletilir. MorpheusAI bunu yalnızca "onay aktarabilir" işaretli token'dan kabul eder
  (MorpheusAI → Ajan & MCP → token üret → "Onay aktarabilir"). Ajanlar (MCP araçları) hiçbir zaman onay veremez.
  Entegrasyon ayarı yoksa "morpheus" adlı/adresli MCP bağlantısının adresi ve token'ı kullanılır.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import re

from .env import env
from .errors import ApiError
from .http_client import request
from .store import iso_now, parse_iso, read_json, write_json

FILE = "integrations.json"
RECORDS = "remediations.json"
MASK = "••••••••"
FINAL = {"applied", "rejected"}
_lock = threading.RLock()


def _defaults() -> dict:
    return {"url": env("MORPHEUS_URL"), "token": env("MORPHEUS_API_TOKEN"), "targetField": "name", "insecureTls": False,
            "autoSend": False, "autoSendPriority": "P1", "timeoutMs": 600000}


def get() -> dict:
    return {**_defaults(), **((read_json(FILE, {}) or {}).get("morpheus") or {})}


def _own_configured(c: dict) -> bool:
    return urlsplit(c["url"] or "").scheme in ("http", "https") and bool(c["token"])


def _mcp_fallback() -> dict | None:
    """Entegrasyon ayarı yoksa MorpheusAI MCP bağlantısı (…/mcp) üzerinden REST adresi ve token"""
    from . import mcp_hub

    for conn in mcp_hub._load()["connections"]:
        if conn.get("enabled", True) and conn.get("token") and re.search(r"morpheus", f"{conn.get('name')} {conn.get('url')}", re.I):
            return {"url": re.sub(r"/mcp/?$", "", conn["url"].rstrip("/")), "token": conn["token"], "insecureTls": bool(conn.get("insecureTls"))}
    return None


def target() -> dict | None:
    c = get()
    if _own_configured(c):
        return {"url": c["url"], "token": c["token"], "insecureTls": c["insecureTls"]}
    return _mcp_fallback()


def is_configured() -> bool:
    return target() is not None


def view() -> dict:
    c = get()
    return {**c, "token": MASK if c["token"] else "", "configured": is_configured(), "viaMcp": not _own_configured(c) and is_configured(), "mask": MASK}


def save(data: dict, user=None) -> dict:
    cur = get()
    url = str(data.get("url") or "").strip().rstrip("/")
    if url and urlsplit(url).scheme not in ("http", "https"):
        raise ApiError("MorpheusAI adresi http(s):// ile başlamalı")
    token = cur["token"] if data.get("token") == MASK else str(data.get("token") or "").strip()
    if token and len(token) < 16:
        raise ApiError("API token en az 16 karakter olmalı (MorpheusAI .env → MORPHEUS_API_TOKEN)")
    nxt = {
        "url": url, "token": token,
        "targetField": "host" if data.get("targetField") == "host" else "name",
        "insecureTls": bool(data.get("insecureTls")),
        "autoSend": bool(data.get("autoSend")),
        "autoSendPriority": "P2" if data.get("autoSendPriority") == "P2" else "P1",
        "timeoutMs": cur.get("timeoutMs", 600000),
        "updatedAt": iso_now(), "updatedBy": user,
    }
    all_ = read_json(FILE, {}) or {}
    all_["morpheus"] = nxt
    write_json(FILE, all_)
    return view()


def _call(method: str, path: str, body=None, kind="morpheus", approved_by=None):
    c, t = get(), target()
    if not t:
        raise ApiError("MorpheusAI entegrasyonu yapılandırılmamış (Ayarlar → MorpheusAI veya Ajan & MCP → morpheusai bağlantısı)", 400)
    headers = {"content-type": "application/json", "accept": "application/json", "authorization": f"Bearer {t['token']}", "x-morpheus-client": "unixai-os"}
    if approved_by:
        headers["x-approved-by"] = str(approved_by)[:80]
    res = request("morpheus", kind, t["url"] + "/api" + path, method=method, headers=headers,
                  body=json.dumps(body, ensure_ascii=False) if body is not None else None,
                  insecure_tls=t["insecureTls"], timeout_ms=c.get("timeoutMs", 600000))
    if res["status"] >= 400:
        msg = (res["json"] or {}).get("error") if isinstance(res["json"], dict) else None
        raise ApiError(f"MorpheusAI {res['status']}: {msg or res['text'][:300]}", 502 if res["status"] >= 500 else res["status"])
    return res["json"]


def test() -> dict:
    h = _call("GET", "/health", kind="morpheus-health")
    r = _call("GET", "/remediations", kind="morpheus-remediations")
    cfg = r.get("config", {})
    return {"ok": True, "mode": h.get("mode"), "aap": cfg.get("aap"), "runnerTemplate": cfg.get("runnerTemplate"),
            "requireCheck": cfg.get("requireCheck"), "autoApprove": cfg.get("autoApprove"), "remediations": len(r.get("items", []))}


# ------------------------------------------------------------------
#  Kayıtlar
# ------------------------------------------------------------------
def _records() -> list:
    return read_json(RECORDS, []) or []


def _save_records(rows: list) -> None:
    write_json(RECORDS, rows[-2000:])


def link(rem_id: str) -> str:
    return f"{(target() or get())['url']}/#/remediations/{rem_id}"


def _merge(rec: dict, r: dict) -> dict:
    last = (r.get("jobs") or [None])[0] or r.get("lastJob")
    rec.update({
        "status": r.get("status"), "score": (r.get("compliance") or {}).get("score"), "engine": r.get("engine"),
        "runnable": r.get("runnable"), "approval": r.get("approval"), "playbookTitle": r.get("title"),
        "rejected": r.get("rejected"), "disruptive": r.get("disruptive"), "risk": r.get("risk"), "summary": r.get("summary"),
        "delivery": r.get("delivery"), "scm": r.get("scm"), "aapTemplate": r.get("aapTemplate"),
        "lastJob": {k: last.get(k) for k in ("id", "mode", "status", "url")} if last else None,
        "refreshedAt": iso_now(), "url": link(r["id"]),
    })
    return rec


def build_payload(snap: dict, action_index: int, source: str = "unixai-os") -> dict:
    a = snap["analysis"]
    try:
        act = a["action_items"][action_index]
    except (IndexError, TypeError):
        raise ApiError("Aksiyon bulunamadı", 404)
    related = [x for x in a["anomalies"] if x["id"] in act.get("related_anomalies", [])]
    inv = snap.get("inventory") or {}
    m = snap["metrics"]
    target = (snap.get("host") or snap["server"]) if get()["targetField"] == "host" else snap["server"]
    return {
        "server": {"name": snap["server"], "target": target, "host": snap.get("host"), "os_release": m.get("os_release"),
                   "kernel": m.get("kernel"), "tags": inv.get("tags", [])},
        "finding": {
            "title": act["title"], "priority": act["priority"], "severity": related[0]["severity"] if related else None,
            "category": related[0]["category"] if related else None,
            "evidence": "\n".join(x["evidence"] for x in related if x.get("evidence")) or None,
            "probable_cause": "; ".join(x["probable_cause"] for x in related if x.get("probable_cause") not in (None, "", "-")) or None,
            "rationale": act.get("rationale"), "commands": act.get("commands", []), "verification": act.get("verification"),
            "risk": act.get("risk"), "anomalies": related,
        },
        "source": source,
        "context": {"collectedAt": snap.get("collectedAt"), "runId": (snap.get("run") or {}).get("id"), "healthScore": a.get("health_score")},
    }


def send(snap: dict, action_index: int, user=None, source="unixai-os") -> dict:
    payload = build_payload(snap, action_index, source)
    r = _call("POST", "/remediations", {**payload, "requestedBy": user}, kind="morpheus-remediation")
    rec = _merge({"id": r["id"], "server": snap["server"], "actionIndex": action_index, "title": payload["finding"]["title"],
                  "priority": payload["finding"]["priority"], "target": payload["server"]["target"], "sentAt": iso_now(),
                  "sentBy": user, "source": source, "runId": payload["context"]["runId"]}, r)
    with _lock:
        rows = _records()
        rows.append(rec)
        _save_records(rows)
    return rec


def refresh(rem_id: str) -> dict:
    r = _call("GET", f"/remediations/{rem_id}", kind="morpheus-remediation-status")
    with _lock:
        rows = _records()
        rec = next((x for x in rows if x["id"] == rem_id), None)
        if rec is None:
            rec = {"id": rem_id, "server": r["server"]["name"], "title": r["finding"]["title"], "sentAt": r.get("createdAt")}
            rows.append(rec)
        _merge(rec, r)
        _save_records(rows)
    return {**rec, "playbook": r.get("playbook"), "events": r.get("events", [])[-10:], "jobs": r.get("jobs", []),
            "requireCheck": r.get("requireCheck", True)}


# ------------------------------------------------------------------
#  İnsan onayı (UnixAI ekranından MorpheusAI'ye aktarılır)
# ------------------------------------------------------------------
def decide(rem_id: str, action: str, user: str, body=None) -> dict:
    """action: approve | reject | publish | run | check — onaylayan kişi X-Approved-By ile iletilir"""
    if not re.match(r"^[\w-]{3,64}$", rem_id or ""):
        raise ApiError("Geçersiz kayıt numarası", 400)
    body = body or {}
    if action == "approve":
        _call("POST", f"/remediations/{rem_id}/approve", {}, kind="morpheus-approve", approved_by=user)
    elif action == "reject":
        _call("POST", f"/remediations/{rem_id}/reject", {"reason": str(body.get("reason") or "")[:500]}, kind="morpheus-reject", approved_by=user)
    elif action == "publish":
        _call("POST", f"/remediations/{rem_id}/publish", {}, kind="morpheus-publish", approved_by=user)
    elif action in ("run", "check"):
        _call("POST", f"/remediations/{rem_id}/launch", {"mode": action, "allowDisruptive": body.get("allowDisruptive") is True},
              kind=f"morpheus-launch-{action}", approved_by=user)
    else:
        raise ApiError("Bilinmeyen işlem", 400)
    return refresh(rem_id)


def pending(limit=20) -> list:
    """Onay bekleyen playbook'lar: köprü kayıtları + ajanlar arası konuşmalarda üretilenler (durum MorpheusAI'den yenilenir)"""
    if not is_configured():
        return []
    from . import a2a

    ids = []
    for x in sorted(_records(), key=lambda r: r.get("sentAt") or "", reverse=True):
        if x.get("status") not in FINAL and not x.get("approval"):
            ids.append(x["id"])
    for item in a2a.query(limit=100)["items"]:
        ids += [m["id"] for m in item.get("remediations") or [] if m.get("id")]
    out, seen = [], set()
    for rid in ids:
        if rid in seen or len(seen) >= limit:
            continue
        seen.add(rid)
        rec = next((x for x in _records() if x["id"] == rid), None)
        if rec and (rec.get("approval") or rec.get("status") in FINAL):
            continue
        try:
            r = refresh(rid)
        except Exception:
            continue
        if not r.get("approval") and not r.get("rejected") and r.get("runnable") is not False and r.get("status") == "generated":
            out.append({k: r.get(k) for k in ("id", "server", "title", "playbookTitle", "score", "status", "target", "sentAt", "url", "risk", "disruptive")})
    return out


def list_records(server=None, refresh_pending=True) -> list:
    rows = [x for x in _records() if not server or x["server"] == server]
    if refresh_pending and is_configured():
        for rec in [x for x in rows if x.get("status") not in FINAL][-15:]:
            try:
                refresh(rec["id"])
            except Exception as e:  # MorpheusAI kapalıysa son bilinen durum gösterilir
                rec["refreshError"] = str(e)[:200]
        rows = [x for x in _records() if not server or x["server"] == server] if rows else rows
    return sorted(rows, key=lambda x: x.get("sentAt") or "", reverse=True)


def auto_send(results: list, user="zamanlayıcı") -> list:
    """Toplama sonrası P1 (veya P1+P2) aksiyonları gönderir; aynı sunucu+başlık son 24 saatte gönderildiyse atlar."""
    c = get()
    if not (c["autoSend"] and is_configured()):
        return []
    from . import runs  # döngüsel içe aktarmayı önlemek için

    prios = {"P1"} if c["autoSendPriority"] == "P1" else {"P1", "P2"}
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = {(x["server"], x["title"]) for x in _records() if x.get("sentAt") and parse_iso(x["sentAt"]) > since}
    sent = []
    for res in results:
        if not res:
            continue
        snap = runs.snapshot(res["name"])
        if not snap:
            continue
        for i, act in enumerate(snap["analysis"]["action_items"]):
            if act["priority"] in prios and (res["name"], act["title"]) not in recent and len(sent) < 10:
                try:
                    sent.append(send(snap, i, user, source="unixai-os (otomatik)"))
                    recent.add((res["name"], act["title"]))
                except Exception as e:
                    print(f"[morpheus] otomatik gönderim başarısız ({res['name']}: {act['title']}): {e}", flush=True)
    if sent:
        print(f"[morpheus] {len(sent)} bulgu MorpheusAI'ye gönderildi", flush=True)
    return sent


def status() -> dict:
    c = get()
    return {"configured": is_configured(), "host": urlsplit(c["url"]).netloc if c["url"] else None, "autoSend": c["autoSend"]}
