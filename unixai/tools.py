"""UnixAI OS araçları — hem yerel ajan tarafından hem de barındırılan MCP sunucusu (/mcp) üzerinden kullanılır

Her araç: ad, açıklama, JSON Schema, salt-okunur mu, işleyici. Salt-okunur token'la bağlanan MCP istemcileri
yalnızca readonly araçları görür.
"""
from __future__ import annotations

from . import a2a, diagnostics, inventory, morpheus, runs
from .errors import ApiError


def _s(desc, **kw):
    return {"type": "string", "description": desc, **kw}


def list_servers(_a, _ctx):
    latest = runs.latest_by_server()
    inv = {s["name"]: s for s in inventory.list_servers()}
    names = list(dict.fromkeys(list(inv) + list(latest)))
    out = []
    for n in names:
        l = latest.get(n) or {}
        out.append({"name": n, "host": (inv.get(n) or {}).get("host") or l.get("host"), "inInventory": n in inv,
                    "tags": (inv.get(n) or {}).get("tags", []), "status": "FAILED" if l.get("collect") == "failed" else l.get("status"),
                    "score": l.get("score"), "p1": l.get("p1"), "collectedAt": l.get("collectedAt"), "os": (l.get("headline") or {}).get("os"),
                    "error": l.get("error")})
    return {"servers": out, "fleet_summary": (runs.latest_fleet() or {}).get("fleet_summary")}


def get_findings(a, _ctx):
    s = runs.snapshot(a["server"])
    if not s:
        raise ApiError(f"'{a['server']}' için analiz yok")
    an, m = s["analysis"], s["metrics"]
    return {"server": s["server"], "host": s.get("host"), "collectedAt": s["collectedAt"], "os_release": m["os_release"], "kernel": m["kernel"],
            "health_score": an["health_score"], "status": an["status"], "summary": an["summary"],
            "action_items": [{"index": i, **x} for i, x in enumerate(an["action_items"])], "anomalies": an["anomalies"],
            "rule_findings": [{"severity": f["severity"], "category": f["category"], "title": f["title"]} for f in s["findings"]],
            "headline": s.get("headline"), "filesystems": m["filesystems"], "failed_units": m["failed_units"]}


def fleet_summary(_a, _ctx):
    return runs.latest_fleet() or {"fleet_summary": "Filo özeti yok (en az iki sunucu analiz edilmeli)."}


def raw_logs(a, _ctx):
    s = runs.snapshot(a["server"])
    if not s:
        raise ApiError(f"'{a['server']}' için toplanmış veri yok")
    sec = a.get("section") or "journal_errors"
    if sec not in s["sections"]:
        raise ApiError(f"Bölüm yok: {sec}. Mevcut: {', '.join(s['sections'])}")
    txt = s["sections"][sec]
    return {"server": a["server"], "section": sec, "collectedAt": s["collectedAt"], "text": txt[-15000:], "truncated": len(txt) > 15000}


def run_diagnostic(a, _ctx):
    return diagnostics.run(a["server"], a["check"], {k: a.get(k) for k in ("unit", "package", "path", "lines") if a.get(k) is not None})


def run_collection(a, ctx):
    opts = {"servers": a.get("servers") or None, "ai": a.get("ai", True), "user": ctx.get("user"), "ip": ctx.get("ip")}
    r = runs.start_reanalyze(**opts) if a.get("reanalyze") else runs.start_collect(**opts)
    return {"runId": r["id"], "status": r["status"], "servers": [s["name"] for s in r["servers"]],
            "hint": "Sonucu unixai_get_run ile izleyin."}


def get_run(a, _ctx):
    r = runs.get(a["runId"])
    if not r:
        raise ApiError("Çalıştırma bulunamadı", 404)
    return {"id": r["id"], "status": r["status"], "trigger": r["trigger"],
            "servers": [{k: s.get(k) for k in ("name", "collect", "analysis", "score", "status", "p1", "error")} for s in r["servers"]],
            "fleet_summary": (r.get("fleet") or {}).get("fleet_summary")}


def send_to_morpheus(a, ctx):
    snap = runs.snapshot(a["server"])
    if not snap:
        raise ApiError(f"'{a['server']}' için analiz yok")
    snap["inventory"] = inventory.get(a["server"])
    if a.get("actionIndex") is not None:
        idxs = [int(a["actionIndex"])]
    else:
        prios = {"P1"} if a.get("priority", "P1") == "P1" else {"P1", "P2"}
        idxs = [i for i, x in enumerate(snap["analysis"]["action_items"]) if x["priority"] in prios]
    out = []
    for i in idxs[:10]:
        try:
            r = morpheus.send(snap, i, ctx.get("user"), source=ctx.get("source") or "unixai-os")
            out.append({k: r.get(k) for k in ("id", "title", "priority", "status", "score", "url", "target")})
        except ApiError as e:
            out.append({"actionIndex": i, "error": str(e)})
    return {"sent": out}


def list_remediations(a, _ctx):
    return {"items": morpheus.list_records(a.get("server"))}


def agent_ask(a, ctx):
    import time
    from . import agent  # döngüsel içe aktarma
    c = a.get("context") or {}
    peer = c.get("from") or ctx.get("client") or "mcp"
    server = a2a.detect_server(a.get("question"), explicit=a.get("server"))
    t0 = time.monotonic()
    from . import live

    r_id = live.rid()
    live.publish("a2a", dir="gelen", peer=peer, question=live.short(a.get("question"), 400), server=server, phase="start", rid=r_id)
    try:
        r = _agent_run(agent, a, ctx, c)
    except Exception as e:
        live.publish("a2a", dir="gelen", peer=peer, phase="end", rid=r_id, ok=False, error=live.short(str(e), 300), durationMs=int((time.monotonic() - t0) * 1000))
        a2a.record("gelen", peer, a.get("question"), server=server, remote_conversation_id=c.get("conversation_id"), ok=False, error=e,
                   duration_ms=int((time.monotonic() - t0) * 1000), user=ctx.get("user"))
        raise
    a2a.record("gelen", peer, a.get("question"), r["reply"], server=server, conversation_id=r["conversationId"],
               remote_conversation_id=c.get("conversation_id"), tools=[t["tool"] for t in r["trace"] if t.get("tool")],
               duration_ms=int((time.monotonic() - t0) * 1000), user=ctx.get("user"))
    live.publish("a2a", dir="gelen", peer=peer, phase="end", rid=r_id, ok=True, answer=live.short(r["reply"], 400), server=server,
                 durationMs=int((time.monotonic() - t0) * 1000), tools=[t["tool"] for t in r["trace"] if t.get("tool")])
    return {"answer": r["reply"], "conversationId": r["conversationId"], "steps": len(r["trace"]),
            "tools_used": [t["tool"] for t in r["trace"] if t.get("tool")], "server": server}


def _agent_run(agent, a, ctx, c):
    return agent.run(
        [{"role": "user", "content": a["question"]}],
        context={"server": a["server"]} if a.get("server") else None,
        ctx={**ctx, "source": c.get("from") or ctx.get("source") or "mcp", "readonly": ctx.get("readonly", False)},
        hop=int(c.get("hop") or 1), conversation_id=c.get("conversation_id"), origin=c.get("from") or ctx.get("client"),
    )


DIAG_SCHEMA = {"type": "object", "required": ["server", "check"], "properties": {
    "server": _s("Sunucu adı (envanterde olmalı)"),
    "check": _s("Tanı adı", enum=list(diagnostics.CATALOG)),
    "unit": _s("systemd unit (service_status, service_logs için)"),
    "package": _s("paket adı (package için)"),
    "path": _s("mutlak yol (large_dirs için)"),
    "lines": {"type": "integer", "description": "satır sayısı (loglar için, en fazla 2000)"},
}}

TOOLS = [
    {"name": "unixai_list_servers", "readonly": True, "handler": list_servers,
     "description": "UnixAI OS'un izlediği RHEL sunucuları: son sağlık skoru, durum (OK/WARNING/CRITICAL/FAILED), P1 aksiyon sayısı, OS.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "unixai_get_server_findings", "readonly": True, "handler": get_findings,
     "description": "Bir sunucunun son analizi: anomaliler, öncelikli aksiyon maddeleri (komutlarıyla, index'li), kural bulguları, dosya sistemleri, OS/kernel.",
     "inputSchema": {"type": "object", "required": ["server"], "properties": {"server": _s("Sunucu adı")}}},
    {"name": "unixai_get_fleet_summary", "readonly": True, "handler": fleet_summary,
     "description": "Filo özeti: sunucular arası ortak örüntüler ve bugünün öncelikli işleri.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "unixai_get_raw_logs", "readonly": True, "handler": raw_logs,
     "description": "Son toplamadaki ham komut çıktısı (ör. journal_errors, kernel_errors, ssh_failures, df, top_processes, failed_units).",
     "inputSchema": {"type": "object", "required": ["server"], "properties": {"server": _s("Sunucu adı"), "section": _s("Bölüm adı (varsayılan journal_errors)")}}},
    {"name": "unixai_list_diagnostics", "readonly": True, "handler": lambda a, c: {"diagnostics": diagnostics.catalog()},
     "description": "Sunucuda çalıştırılabilecek salt-okunur tanı komutlarının kataloğu.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "unixai_run_diagnostic", "readonly": True, "handler": run_diagnostic,
     "description": "Sunucuda katalogdaki SALT-OKUNUR bir tanı komutunu SSH ile canlı çalıştırır (ör. lvm: VG boş alanı, service_logs: servis logları). "
                    "Playbook yazmadan önce eksik bilgiyi toplamak için kullanın. Sistemi değiştirmez.",
     "inputSchema": DIAG_SCHEMA},
    {"name": "unixai_run_collection", "readonly": False, "handler": run_collection,
     "description": "Agentless (SSH) toplama ve analiz başlatır; reanalyze=true ise SSH'a gitmeden son ham veriyi yeniden analiz eder. "
                    "Düzeltme uygulandıktan sonra doğrulama için kullanın.",
     "inputSchema": {"type": "object", "properties": {"servers": {"type": "array", "items": {"type": "string"}}, "reanalyze": {"type": "boolean"},
                                                      "ai": {"type": "boolean"}}}},
    {"name": "unixai_get_run", "readonly": True, "handler": get_run,
     "description": "Toplama/analiz çalıştırmasının durumu ve sunucu bazında sonuçları.",
     "inputSchema": {"type": "object", "required": ["runId"], "properties": {"runId": _s("Çalıştırma numarası")}}},
    {"name": "unixai_send_to_morpheus", "readonly": False, "handler": send_to_morpheus,
     "description": "Sunucu aksiyonunu UnixAI OS'un MorpheusAI köprüsüyle gönderir; MorpheusAI standartlara uygun düzeltme playbook'u üretir. "
                    "actionIndex verilmezse P1 (priority=P2 ise P1+P2) aksiyonların hepsi.",
     "inputSchema": {"type": "object", "required": ["server"], "properties": {"server": _s("Sunucu adı"), "actionIndex": {"type": "integer"},
                                                                             "priority": _s("P1 veya P2", enum=["P1", "P2"])}}},
    {"name": "unixai_list_remediations", "readonly": True, "handler": list_remediations,
     "description": "MorpheusAI'ye gönderilen düzeltmeler ve güncel durumları.",
     "inputSchema": {"type": "object", "properties": {"server": _s("Sunucu adı filtresi")}}},
    {"name": "unixai_agent_ask", "readonly": True, "handler": agent_ask, "agent": True,
     "description": "UnixAI OS AJANINA doğal dilde soru/istek gönderir (ajan kendi araçlarıyla sunucu verisine bakar, gerekirse canlı tanı çalıştırır ve cevaplar). "
                    "Başka bir ajanın UnixAI ile konuşması için kullanılır.",
     "inputSchema": {"type": "object", "required": ["question"], "properties": {
         "question": _s("Soru veya istek"), "server": _s("İlgili sunucu (opsiyonel)"),
         "context": {"type": "object", "description": "Ajanlar arası izleme (otomatik doldurulur)",
                     "properties": {"conversation_id": {"type": "string"}, "hop": {"type": "integer"}, "from": {"type": "string"}}}}}},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def visible(readonly: bool = False, include_agent: bool = True) -> list:
    return [t for t in TOOLS if (t["readonly"] or not readonly) and (include_agent or not t.get("agent"))]


def call(name: str, args: dict, ctx: dict):
    t = BY_NAME.get(name)
    if not t:
        raise ApiError(f"Bilinmeyen araç: {name}", 404)
    if ctx.get("readonly") and not t["readonly"]:
        raise ApiError(f"'{name}' salt-okunur erişimle kullanılamaz", 403)
    return t["handler"](args or {}, ctx)
