#!/usr/bin/env python3
"""UnixAI OS MCP sunucusu (Model Context Protocol, stdio, yalnızca Python standart kütüphanesi)

UnixAI OS'un REST API'sini MCP araçları olarak sunar. Bir MCP istemcisi (Claude Code / Claude Desktop) bu sunucuyu
MorpheusAI MCP sunucusuyla birlikte kullanarak iki sistemi konuşturur:
  unixai_get_server_findings → morpheus_create_remediation → morpheus_launch_remediation (check) ...
veya doğrudan köprüyle: unixai_send_to_morpheus (UnixAI OS bulguyu kendi MorpheusAI entegrasyonu üzerinden gönderir).

Ortam değişkenleri:
  UNIXAI_URL         http://unixai.kurum.local:3000
  UNIXAI_API_TOKEN   UnixAI OS .env → UNIXAI_API_TOKEN ile aynı
  UNIXAI_TLS_INSECURE=true  (self-signed sertifika)

Çalıştırma: python3 mcp/unixai_mcp.py   (stdout yalnızca JSON-RPC içindir; loglar stderr'e)
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("UNIXAI_URL", "http://127.0.0.1:3000").rstrip("/")
TOKEN = os.environ.get("UNIXAI_API_TOKEN", "")
TIMEOUT = float(os.environ.get("UNIXAI_TIMEOUT_SEC", "600"))
VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
_ctx = ssl.create_default_context()
if os.environ.get("UNIXAI_TLS_INSECURE", "").lower() in ("1", "true", "yes"):
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_ctx))


def log(*a):
    print("[unixai-mcp]", *a, file=sys.stderr, flush=True)


def api(path, method="GET", body=None):
    headers = {"content-type": "application/json", "accept": "application/json", "x-unixai-client": "mcp"}
    if TOKEN:
        headers["authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(BASE + "/api" + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        r = _opener.open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        r = e
    text = r.read().decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = text
    code = getattr(r, "status", None) or r.code
    if code >= 400:
        raise RuntimeError(f"UnixAI OS {code}: {data.get('error') if isinstance(data, dict) else text[:300]}")
    return data


def q(v):
    return urllib.parse.quote(str(v), safe="")


# ------------------------------------------------------------------
#  Araçlar
# ------------------------------------------------------------------
def t_list_servers(_):
    o = api("/overview")
    return {"servers": [{"name": s["name"], "host": s["host"], "tags": s["tags"],
                         **({k: s["latest"].get(k) for k in ("status", "score", "p1", "findings", "collect", "collectedAt", "error")} if s["latest"] else {"status": "toplanmadı"}),
                         "os": ((s["latest"] or {}).get("headline") or {}).get("os")} for s in o["servers"]],
            "fleet_summary": (o.get("fleet") or {}).get("fleet_summary"), "mode": o["mode"]}


def t_get_server_findings(a):
    s = api(f"/servers/{q(a['server'])}/snapshot")
    an = s["analysis"]
    m = s["metrics"]
    return {
        "server": s["server"], "host": s.get("host"), "collectedAt": s["collectedAt"], "os_release": m["os_release"], "kernel": m["kernel"],
        "health_score": an["health_score"], "status": an["status"], "summary": an["summary"], "engine": an.get("engine"),
        "action_items": [{"index": i, **x} for i, x in enumerate(an["action_items"])],
        "anomalies": an["anomalies"],
        "rule_findings": [{"severity": f["severity"], "category": f["category"], "title": f["title"]} for f in s["findings"]],
        "headline": s.get("headline"),
        "hint": "Bir aksiyonu düzeltmek için unixai_send_to_morpheus (server, actionIndex) veya morpheus_create_remediation kullanın.",
    }


def t_get_fleet_summary(_):
    o = api("/overview")
    return o.get("fleet") or {"fleet_summary": "Filo özeti yok (en az iki sunucu analiz edilmeli)."}


def t_run_collection(a):
    body = {"mode": "reanalyze" if a.get("reanalyze") else "collect", "ai": a.get("ai", True)}
    if a.get("servers"):
        body["servers"] = a["servers"]
    r = api("/runs", "POST", body)
    return {"runId": r["id"], "status": r["status"], "servers": [s["name"] for s in r["servers"]],
            "hint": "Sonucu unixai_get_run ile izleyin (status running → done/partial/failed)."}


def t_get_run(a):
    r = api(f"/runs/{q(a['runId'])}")
    return {"id": r["id"], "status": r["status"], "trigger": r["trigger"], "ai": r["ai"],
            "servers": [{k: s.get(k) for k in ("name", "collect", "analysis", "score", "status", "p1", "error")} for s in r["servers"]],
            "fleet_summary": (r.get("fleet") or {}).get("fleet_summary")}


def t_send_to_morpheus(a):
    if a.get("actionIndex") is None:
        snap = api(f"/servers/{q(a['server'])}/snapshot")
        prios = {"P1"} if a.get("priority", "P1") == "P1" else {"P1", "P2"}
        idxs = [i for i, x in enumerate(snap["analysis"]["action_items"]) if x["priority"] in prios]
    else:
        idxs = [int(a["actionIndex"])]
    out = []
    for i in idxs[:10]:
        try:
            r = api("/remediations", "POST", {"server": a["server"], "actionIndex": i})
            out.append({k: r.get(k) for k in ("id", "title", "priority", "status", "score", "url", "target")})
        except RuntimeError as e:
            out.append({"actionIndex": i, "error": str(e)})
    return {"sent": out, "hint": "MorpheusAI playbook'u üretti; durumu unixai_list_remediations veya morpheus_get_remediation ile izleyin. "
                                 "Check (dry-run) için morpheus_launch_remediation mode=check; gerçek uygulama MorpheusAI arayüzünde onay ister."}


def t_list_remediations(a):
    path = "/remediations" + (f"?server={q(a['server'])}" if a.get("server") else "")
    return api(path)


def t_ask(a):
    ctx = {"server": a["server"]} if a.get("server") else {"fleet": True}
    return api("/chat", "POST", {"messages": [{"role": "user", "content": a["question"]}], "context": ctx})["reply"]


def S(desc, **kw):
    return {"type": "string", "description": desc, **kw}


TOOLS = [
    ("unixai_list_servers", "UnixAI OS'un izlediği RHEL sunucularını, son sağlık skorunu, durumunu (OK/WARNING/CRITICAL) ve P1 aksiyon sayısını listeler.",
     {"type": "object", "properties": {}}, t_list_servers),
    ("unixai_get_server_findings", "Bir sunucunun son analizini döndürür: anomaliler, öncelikli aksiyon maddeleri (komutlarıyla, index ile), kural bulguları, OS bilgisi.",
     {"type": "object", "required": ["server"], "properties": {"server": S("Sunucu adı")}}, t_get_server_findings),
    ("unixai_get_fleet_summary", "Filo özeti: sunucular arası örüntüler ve bugünün öncelikli işleri.",
     {"type": "object", "properties": {}}, t_get_fleet_summary),
    ("unixai_run_collection", "Agentless (SSH) toplama ve analiz başlatır; reanalyze=true ise SSH'a gitmeden son ham veriyi yeniden analiz eder.",
     {"type": "object", "properties": {"servers": {"type": "array", "items": {"type": "string"}, "description": "Boş = tüm etkin sunucular"},
                                       "reanalyze": {"type": "boolean", "default": False}, "ai": {"type": "boolean", "default": True}}}, t_run_collection),
    ("unixai_get_run", "Bir toplama/analiz çalıştırmasının durumu ve sunucu bazında sonuçları.",
     {"type": "object", "required": ["runId"], "properties": {"runId": S("Çalıştırma numarası")}}, t_get_run),
    ("unixai_send_to_morpheus",
     "Sunucu bulgusunu UnixAI OS'un MorpheusAI entegrasyonu üzerinden MorpheusAI'ye gönderir; MorpheusAI standartlara uygun düzeltme playbook'u üretir. "
     "actionIndex verilmezse sunucunun P1 (priority=P2 ise P1+P2) aksiyonlarının hepsi gönderilir.",
     {"type": "object", "required": ["server"], "properties": {"server": S("Sunucu adı"), "actionIndex": {"type": "integer", "description": "unixai_get_server_findings'teki index"},
                                                               "priority": S("P1 veya P2", enum=["P1", "P2"])}}, t_send_to_morpheus),
    ("unixai_list_remediations", "MorpheusAI'ye gönderilen düzeltmeler ve güncel durumları (onay bekliyor, check başarılı, uygulandı...).",
     {"type": "object", "properties": {"server": S("Sunucu adı filtresi")}}, t_list_remediations),
    ("unixai_ask", "UnixAI'ye sunucu veya filo bağlamıyla soru sorar (son toplanan metrik, log ve analizlere göre).",
     {"type": "object", "required": ["question"], "properties": {"question": S("Soru"), "server": S("Sunucu adı (boş = filo)")}}, t_ask),
]


# ------------------------------------------------------------------
#  JSON-RPC 2.0 / MCP (stdio, satır başına bir mesaj)
# ------------------------------------------------------------------
def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # bildirim
        return
    if method == "initialize":
        return send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion") if params.get("protocolVersion") in VERSIONS else VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "unixai-os", "title": "UnixAI OS (RHEL agentless gözlem)", "version": "1.0.0"},
            "instructions": "UnixAI OS RHEL sunuculardan agentless log/metrik toplar ve anomalileri aksiyon maddelerine çevirir. "
                            "Düzeltme için bulguyu MorpheusAI'ye gönder (unixai_send_to_morpheus veya morpheus_create_remediation); "
                            "gerçek uygulamadan önce check modunu kullan ve MorpheusAI arayüzündeki onayı bekle.",
        }})
    if method == "ping":
        return send({"jsonrpc": "2.0", "id": mid, "result": {}})
    if method == "tools/list":
        return send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in TOOLS]}})
    if method == "tools/call":
        tool = next((t for t in TOOLS if t[0] == params.get("name")), None)
        if not tool:
            return send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Bilinmeyen araç: {params.get('name')}"}})
        try:
            result = tool[3](params.get("arguments") or {})
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
            res = {"content": [{"type": "text", "text": text}]}
            if isinstance(result, dict):
                res["structuredContent"] = result
            return send({"jsonrpc": "2.0", "id": mid, "result": res})
        except Exception as e:
            return send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}})
    return send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Desteklenmeyen metot: {method}"}})


def main():
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    log(f"hazır → {BASE}" + ("" if TOKEN else " (UNIXAI_API_TOKEN tanımlı değil)"))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        try:
            handle(msg)
        except Exception as e:
            log("hata:", e)


if __name__ == "__main__":
    main()
