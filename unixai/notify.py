"""Bildirimler: Microsoft Teams / Slack / genel webhook ve e-posta (SMTP)

  Ayarlar → Bildirimler (data/integrations.json → "notify", 0600)
    webhook  : {enabled, type: teams | slack | generic, url}     url gizlidir (Teams/Slack imzası adreste)
    email    : {enabled, host, port, security: starttls | ssl | none, user, password, from, to[]}
    events   : hangi olaylarda bildirim gider
                 runSummary   günlük / elle toplama bitti (filo özeti, kritik sunucular, P1 aksiyonlar)
                 critical     yalnızca kritik sunucu veya P1 bulgu varsa (runSummary kapalıyken bile)
                 approvals    MorpheusAI playbook'u onay bekliyor
                 vulns        zafiyet taraması bitti: Critical / SLA aşan errata
    appUrl   : bildirimdeki "Trinity'de aç" bağlantısının kökü (ör. https://trinity.kurum.local)

  Gönderim arka planda yapılır; hata toplamayı/taramayı bozmaz, logs/bildirim/ altına yazılır.
  Webhook adresi loglara yazılmaz (yalnızca host).
"""
from __future__ import annotations

import json
import re
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape
from urllib.parse import urlsplit

from .env import env
from .errors import ApiError
from .store import iso_now, read_json, write_json

FILE = "integrations.json"
LAST = "notify_last.json"  # son gönderim sonucu (ayar dosyasına yazılmaz; eşzamanlı kayıtla çakışmasın)
MASK = "••••••••"
EVENTS = {"runSummary": "Toplama ve analiz bitti (günlük özet)", "critical": "Kritik sunucu / P1 bulgu",
          "approvals": "Playbook onay bekliyor", "vulns": "Zafiyet taraması: Critical ve SLA aşan errata"}
WEBHOOK_TYPES = {"teams": "Microsoft Teams (Workflows / gelen webhook)", "slack": "Slack (incoming webhook)", "generic": "Genel JSON webhook"}
SEV_COLOR = {"critical": "attention", "warning": "warning", "ok": "good", "info": "accent"}
_lock = threading.Lock()


def _defaults() -> dict:
    return {"webhook": {"enabled": False, "type": "teams", "url": env("NOTIFY_WEBHOOK_URL")},
            "email": {"enabled": False, "host": env("SMTP_HOST"), "port": int(env("SMTP_PORT", "25") or 25), "security": env("SMTP_SECURITY", "none") or "none",
                      "user": env("SMTP_USER"), "password": env("SMTP_PASSWORD"), "from": env("SMTP_FROM", "trinity@localhost"), "to": []},
            "events": {"runSummary": True, "critical": True, "approvals": True, "vulns": True},
            "appUrl": env("TRINITY_URL"), "last": None}


def get() -> dict:
    d, cur = _defaults(), (read_json(FILE, {}) or {}).get("notify") or {}
    return {**d, **cur, **{k: {**d[k], **(cur.get(k) or {})} for k in ("webhook", "email", "events")}, "last": read_json(LAST, None)}


def channels() -> list:
    c = get()
    out = []
    if c["webhook"]["enabled"] and c["webhook"]["url"]:
        out.append("webhook")
    if c["email"]["enabled"] and c["email"]["host"] and c["email"]["to"]:
        out.append("email")
    return out


def view() -> dict:
    c = get()
    return {**c, "webhook": {**c["webhook"], "url": MASK if c["webhook"]["url"] else ""},
            "email": {**c["email"], "password": MASK if c["email"]["password"] else ""},
            "mask": MASK, "eventLabels": EVENTS, "webhookTypes": WEBHOOK_TYPES, "active": channels()}


def save(data: dict, user=None) -> dict:
    cur = get()
    w, e = {**cur["webhook"], **(data.get("webhook") or {})}, {**cur["email"], **(data.get("email") or {})}
    if w.get("url") == MASK:
        w["url"] = cur["webhook"]["url"]
    w["url"] = str(w.get("url") or "").strip()
    if w["url"] and urlsplit(w["url"]).scheme not in ("https", "http"):
        raise ApiError("Webhook adresi http(s):// ile başlamalı")
    if w.get("type") not in WEBHOOK_TYPES:
        raise ApiError("Geçersiz webhook tipi")
    w["enabled"] = bool(w.get("enabled"))
    if e.get("password") == MASK:
        e["password"] = cur["email"]["password"]
    to = e.get("to")
    to = [x.strip() for x in (re.split(r"[,;\s]+", to) if isinstance(to, str) else to or []) if str(x).strip()]
    bad = [x for x in to if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", x)]
    if bad:
        raise ApiError(f"Geçersiz e-posta adresi: {', '.join(bad[:3])}")
    try:
        port = int(e.get("port") or 25)
    except (TypeError, ValueError):
        raise ApiError("SMTP portu bir sayı olmalı")
    if not 1 <= port <= 65535:
        raise ApiError("SMTP portu 1 - 65535 arasında olmalı")
    if e.get("security") not in ("starttls", "ssl", "none"):
        raise ApiError("Geçersiz SMTP güvenliği")
    e = {"enabled": bool(e.get("enabled")), "host": str(e.get("host") or "").strip()[:200], "port": port, "security": e["security"],
         "user": str(e.get("user") or "").strip()[:200], "password": str(e.get("password") or ""), "from": str(e.get("from") or "").strip()[:200] or "trinity@localhost",
         "to": to[:50]}
    events = {k: bool((data.get("events") or cur["events"]).get(k, cur["events"][k])) for k in EVENTS}
    app_url = str(data.get("appUrl") if data.get("appUrl") is not None else cur["appUrl"] or "").strip().rstrip("/")
    if app_url and urlsplit(app_url).scheme not in ("https", "http"):
        raise ApiError("Trinity adresi http(s):// ile başlamalı")
    with _lock:
        all_ = read_json(FILE, {}) or {}
        all_["notify"] = {"webhook": w, "email": e, "events": events, "appUrl": app_url, "updatedAt": iso_now(), "updatedBy": user}
        write_json(FILE, all_)
    return view()


# ------------------------------------------------------------------
#  Mesaj biçimleri
# ------------------------------------------------------------------
def _link(path: str):
    base = get()["appUrl"]
    return f"{base}/#/{path.lstrip('#/')}" if base else None


def _teams(m: dict) -> dict:
    body = [{"type": "TextBlock", "text": m["title"], "weight": "Bolder", "size": "Medium", "wrap": True, "color": SEV_COLOR.get(m["severity"], "default")}]
    if m.get("summary"):
        body.append({"type": "TextBlock", "text": m["summary"], "wrap": True, "spacing": "Small"})
    if m.get("facts"):
        body.append({"type": "FactSet", "facts": [{"title": k, "value": str(v)} for k, v in m["facts"]]})
    for line in m.get("lines", [])[:15]:
        body.append({"type": "TextBlock", "text": f"• {line}", "wrap": True, "spacing": "None"})
    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": body, "msteams": {"width": "Full"}}
    if m.get("link"):
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Trinity'de aç", "url": m["link"]}]
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def _slack(m: dict) -> dict:
    icon = {"critical": ":red_circle:", "warning": ":large_orange_circle:", "ok": ":large_green_circle:"}.get(m["severity"], ":large_blue_circle:")
    parts = [f"{icon} *{m['title']}*"] + ([m["summary"]] if m.get("summary") else []) + [f"*{k}:* {v}" for k, v in m.get("facts", [])]
    parts += [f"• {x}" for x in m.get("lines", [])[:15]] + ([f"<{m['link']}|Trinity'de aç>"] if m.get("link") else [])
    return {"text": "\n".join(parts)}


def _email_bodies(m: dict):
    text = "\n".join([m["title"], ""] + ([m["summary"], ""] if m.get("summary") else []) + [f"{k}: {v}" for k, v in m.get("facts", [])]
                     + [""] + [f"- {x}" for x in m.get("lines", [])] + ([f"\nTrinity'de aç: {m['link']}"] if m.get("link") else []) + ["", "— Trinity"])
    color = {"critical": "#c62828", "warning": "#e08a00", "ok": "#2e7d32"}.get(m["severity"], "#2f6fb2")
    html = (f"<div style=\"font:14px/1.5 Segoe UI,Arial,sans-serif;color:#1f2933;max-width:680px\">"
            f"<div style=\"border-left:4px solid {color};padding:4px 12px;margin-bottom:12px\"><div style=\"font-size:17px;font-weight:600\">{escape(m['title'])}</div>"
            + (f"<div style=\"color:#52606d\">{escape(m['summary'])}</div>" if m.get("summary") else "") + "</div>"
            + ("<table style=\"border-collapse:collapse;margin-bottom:10px\">" + "".join(
                f"<tr><td style=\"padding:2px 14px 2px 0;color:#7b8794\">{escape(str(k))}</td><td style=\"padding:2px 0;font-weight:600\">{escape(str(v))}</td></tr>"
                for k, v in m.get("facts", [])) + "</table>" if m.get("facts") else "")
            + ("<ul style=\"padding-left:18px;margin:0 0 12px\">" + "".join(f"<li>{escape(x)}</li>" for x in m.get("lines", [])) + "</ul>" if m.get("lines") else "")
            + (f"<a href=\"{escape(m['link'])}\" style=\"display:inline-block;background:{color};color:#fff;text-decoration:none;padding:7px 14px;border-radius:6px\">Trinity'de aç</a>" if m.get("link") else "")
            + "<div style=\"margin-top:18px;color:#9aa5b1;font-size:12px\">Bu ileti Trinity tarafından otomatik gönderildi. Bildirim ayarları: Ayarlar → Bildirimler.</div></div>")
    return text, html


# ------------------------------------------------------------------
#  Gönderim
# ------------------------------------------------------------------
def _post_webhook(w: dict, m: dict) -> None:
    payload = _teams(m) if w["type"] == "teams" else _slack(m) if w["type"] == "slack" else {
        "source": "trinity", "event": m["event"], "severity": m["severity"], "title": m["title"], "summary": m.get("summary"),
        "facts": dict(m.get("facts", [])), "lines": m.get("lines", []), "link": m.get("link"), "at": iso_now()}
    req = urllib.request.Request(w["url"], data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"content-type": "application/json", "user-agent": "Trinity"})
    # Teams/Slack genelde internette: ortamın https_proxy / no_proxy ayarı kullanılır (NOTIFY_NO_PROXY=true ile doğrudan)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}) if env("NOTIFY_NO_PROXY").lower() in ("1", "true", "yes") else urllib.request.ProxyHandler())
    try:
        with opener.open(req, timeout=20) as r:
            if r.status >= 300:
                raise ApiError(f"webhook {r.status}")
    except urllib.error.HTTPError as e:
        raise ApiError(f"webhook {e.code}: {e.read()[:200].decode('utf-8', 'replace')}", 502)
    except urllib.error.URLError as e:
        raise ApiError(f"webhook adresine ulaşılamadı: {e.reason}", 502)


def _send_email(e: dict, m: dict) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = f"[Trinity] {m['title']}", e["from"], ", ".join(e["to"])
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(domain="trinity")
    text, html = _email_bodies(m)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    try:
        if e["security"] == "ssl":
            s = smtplib.SMTP_SSL(e["host"], e["port"], timeout=20, context=ctx)
        else:
            s = smtplib.SMTP(e["host"], e["port"], timeout=20)
        with s:
            if e["security"] == "starttls":
                s.starttls(context=ctx)
            if e["user"]:
                s.login(e["user"], e["password"])
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as x:
        raise ApiError(f"SMTP ({e['host']}:{e['port']}): {x}", 502)


def deliver(m: dict, only=None) -> dict:
    """Mesajı etkin kanallara gönderir (eşzamanlı). Dönen: {kanal: 'ok' | hata}"""
    from . import oplog

    c = get()
    out = {}
    for ch in only or channels():
        try:
            if ch == "webhook":
                _post_webhook(c["webhook"], m)
            elif ch == "email":
                _send_email(c["email"], m)
            out[ch] = "ok"
        except Exception as e:  # noqa: BLE001 — her kanal bağımsız
            out[ch] = str(e)[:300]
    if out:
        err = "; ".join(f"{k}: {v}" for k, v in out.items() if v != "ok") or None
        oplog.log("bildirim", m["title"], event=m["event"], channels=list(out), webhookHost=urlsplit(c["webhook"]["url"]).netloc if "webhook" in out else None,
                  to=c["email"]["to"] if "email" in out else None, error=err)
        write_json(LAST, {"at": iso_now(), "event": m["event"], "title": m["title"], "result": out})
    return out


def emit(event: str, title: str, severity: str = "info", summary: str = "", facts=None, lines=None, link_path=None) -> bool:
    """Olay açıksa arka planda gönderir. 'critical' olayı runSummary kapalıyken kritik durumları yine iletir."""
    c = get()
    if not channels() or not c["events"].get(event):
        return False
    m = {"event": event, "title": title, "severity": severity, "summary": summary, "facts": list(facts or []), "lines": list(lines or []),
         "link": _link(link_path) if link_path else None}
    threading.Thread(target=deliver, args=(m,), name=f"notify-{event}", daemon=True).start()
    return True


def test(channel=None) -> dict:
    ch = [channel] if channel in ("webhook", "email") else None
    if not (ch or channels()):
        raise ApiError("Etkin bildirim kanalı yok: webhook adresi veya SMTP sunucusu + alıcı girip etkinleştirin")
    m = {"event": "test", "title": "Trinity bildirim testi", "severity": "ok", "summary": "Bu kanal Trinity bildirimlerini alıyor.",
         "facts": [("Gönderen", "Trinity"), ("Zaman", iso_now())], "lines": [f"Açık olaylar: {', '.join(EVENTS[k] for k, v in get()['events'].items() if v) or 'yok'}"],
         "link": _link("dashboard")}
    out = deliver(m, ch)
    return {"ok": all(v == "ok" for v in out.values()), "result": out}


# ------------------------------------------------------------------
#  Olaylar
# ------------------------------------------------------------------
def run_finished(run: dict, results: list) -> None:
    ok = [r for r in results if r]
    if not ok and run["status"] != "failed":
        return
    crit = [r["name"] for r in ok if r["analysis"]["status"] == "CRITICAL"]
    warn = [r["name"] for r in ok if r["analysis"]["status"] == "WARNING"]
    p1 = [(r["name"], a["title"]) for r in ok for a in r["analysis"]["action_items"] if a["priority"] == "P1"]
    failed = [s["name"] for s in run["servers"] if s.get("collect") == "failed"]
    sev = "critical" if crit or p1 else "warning" if warn or failed else "ok"
    title = (f"{len(crit)} sunucu kritik, {len(p1)} P1 aksiyon" if crit or p1 else
             f"{len(warn)} sunucuda uyarı" if warn else "Tüm sunucular sağlıklı") + f" · {len(run['servers'])} sunucu tarandı"
    summary = ((run.get("fleet") or {}).get("fleet_summary") or "")[:600]
    scores = [r["analysis"]["health_score"] for r in ok if isinstance(r["analysis"].get("health_score"), (int, float))]
    facts = [("Kritik", ", ".join(crit[:8]) or "yok"), ("Uyarı", ", ".join(warn[:8]) or "yok"),
             ("Ortalama sağlık", f"{round(sum(scores) / len(scores))}/100" if scores else "—"), ("Toplanamayan", ", ".join(failed[:8]) or "yok"),
             ("Tetikleyen", "zamanlayıcı" if run["trigger"] == "schedule" else run.get("user") or "—")]
    lines = [f"{n}: {t}" for n, t in p1[:10]] + ([f"… ve {len(p1) - 10} P1 daha"] if len(p1) > 10 else [])
    event = "runSummary" if get()["events"].get("runSummary") else "critical" if sev == "critical" else None
    if event:
        emit(event, title, sev, summary, facts, lines, "dashboard")


def approval_pending(rec: dict) -> None:
    emit("approvals", f"Playbook onayınızı bekliyor: {rec.get('server')}", "warning", rec.get("title") or "",
         [("Sunucu", rec.get("server")), ("Öncelik", rec.get("priority") or "—"), ("Gönderen", rec.get("sentBy") or "—"), ("Kaynak", rec.get("source") or "—")],
         ["Onaydan önce AAP'de hiçbir şey çalışmaz. Trinity → MorpheusAI sekmesinden YAML'ı inceleyip onaylayın veya reddedin."], "dashboard")


def vulns_scanned(b: dict) -> None:
    t = b.get("totals") or {}
    c = t.get("counts") or {}
    if not (c.get("Critical") or t.get("overdue")):
        return
    worst = [h for h in b.get("items", []) if ((h.get("summary") or {}).get("counts") or {}).get("Critical") or (h.get("summary") or {}).get("overdue")][:10]
    emit("vulns", f"Zafiyet taraması: {c.get('Critical', 0)} Critical, {t.get('overdue', 0)} SLA aşan errata", "critical" if c.get("Critical") else "warning",
         f"{t.get('withVulns', 0)}/{t.get('hosts', 0)} sunucuda açık güvenlik errata'sı var; {t.get('installable', 0)} tanesinin yaması hazır.",
         [("Critical", c.get("Critical", 0)), ("Important", c.get("Important", 0)), ("SLA aşan", t.get("overdue", 0)), ("Farklı CVE", t.get("cves", 0))],
         [f"{h['key']}: {(h['summary']['counts'] or {}).get('Critical', 0)} Critical, {h['summary'].get('overdue', 0)} SLA aşan" for h in worst], "vulns")
