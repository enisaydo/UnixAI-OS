#!/usr/bin/env python3
"""UnixAI OS MCP — stdio aktarıcısı (yalnızca Python standart kütüphanesi)

UnixAI OS, MCP sunucusunu kendisi barındırır: POST {UNIXAI_URL}/mcp (Streamable HTTP). HTTP destekleyen istemciler
(Claude Code, MorpheusAI ajanı, diğer ajanlar) doğrudan bu adrese bağlanır. Yalnızca stdio destekleyen istemciler için
bu betik stdin'deki JSON-RPC mesajlarını /mcp'ye aktarır, cevapları stdout'a yazar; araç listesi her zaman sunucuyla aynıdır.

Ortam değişkenleri:
  UNIXAI_URL         http://unixai.kurum.local:3000
  UNIXAI_API_TOKEN   UnixAI OS → Ajan & MCP'de üretilen MCP token'ı (veya .env UNIXAI_API_TOKEN)
  UNIXAI_TLS_INSECURE=true  (self-signed sertifika)
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

URL = os.environ.get("UNIXAI_URL", "http://127.0.0.1:3000").rstrip("/") + "/mcp"
TOKEN = os.environ.get("UNIXAI_API_TOKEN", "")
TIMEOUT = float(os.environ.get("UNIXAI_TIMEOUT_SEC", "600"))
_ctx = ssl.create_default_context()
if os.environ.get("UNIXAI_TLS_INSECURE", "").lower() in ("1", "true", "yes"):
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_ctx))
_session = None


def log(*a):
    print("[unixai-mcp]", *a, file=sys.stderr, flush=True)


def forward(msg: dict):
    global _session
    h = {"content-type": "application/json", "accept": "application/json, text/event-stream", "x-unixai-client": "stdio"}
    if TOKEN:
        h["authorization"] = f"Bearer {TOKEN}"
    if _session:
        h["mcp-session-id"] = _session
    req = urllib.request.Request(URL, data=json.dumps(msg).encode(), headers=h, method="POST")
    try:
        r = _opener.open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        r = e
    _session = r.headers.get("mcp-session-id") or _session
    text = r.read().decode("utf-8", "replace")
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": f"UnixAI OS geçersiz yanıt ({getattr(r, 'status', None) or r.code}): {text[:200]}"}}


def main():
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    log(f"aktarıcı hazır → {URL}" + ("" if TOKEN else " (UNIXAI_API_TOKEN tanımlı değil)"))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), flush=True)
            continue
        try:
            out = forward(msg)
        except Exception as e:
            out = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": f"UnixAI OS'a ulaşılamadı: {e}"}} if msg.get("id") is not None else None
        if out is not None and msg.get("id") is not None:
            print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
