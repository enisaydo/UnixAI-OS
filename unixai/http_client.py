"""Ortak HTTP istemcisi (bağımlılıksız)

- https/http, gzip/deflate çözme, zaman aşımı, TLS doğrulamasını kapatma seçeneği
- Her çağrı logger ile kayıt altına alınır (gizli alanlar maskelenir)
- Ortam proxy değişkenleri (http_proxy) kullanılmaz: kurum içi servislere doğrudan gidilir
"""
from __future__ import annotations

import gzip
import json
import socket
import ssl
import time
import urllib.error
import urllib.request
import zlib
from urllib.parse import urlsplit

from . import logger

_opener_cache: dict = {}


def _opener(insecure: bool) -> urllib.request.OpenerDirector:
    if insecure not in _opener_cache:
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        _opener_cache[insecure] = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
    return _opener_cache[insecure]


def _decode(raw: bytes, enc: str) -> bytes:
    enc = (enc or "").lower()
    if "gzip" in enc:
        return gzip.decompress(raw)
    if "deflate" in enc:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def _friendly(e: Exception, url: str, tls_hint) -> Exception:
    host = urlsplit(url).hostname or url
    reason = getattr(e, "reason", e)
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
        return RuntimeError(f"TLS sertifikası doğrulanamadı ({host}). {tls_hint or 'TLS doğrulamasını kapatma seçeneğini kullanın.'}")
    if isinstance(reason, socket.gaierror):
        return RuntimeError(f"'{host}' adı çözülemedi (DNS). " + ("" if "." in host else "Kısa ad yerine tam adı (FQDN) kullanın. ")
                            + f"Sunucuda 'getent hosts {host}' ile kontrol edin.")
    if isinstance(reason, ConnectionRefusedError):
        return RuntimeError(f"{host} bağlantıyı reddetti (ECONNREFUSED). Adres/port doğru mu?")
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return RuntimeError(f"Zaman aşımı: {host}")
    return RuntimeError(f"{host}: {reason}")


def request(service: str, kind: str, url: str, method: str = "GET", headers=None, body=None,
            insecure_tls: bool = False, timeout_ms: int = 60000, tls_hint=None, meta_fn=None) -> dict:
    """İsteği atar ve loglar. Dönen: {status, headers, text, json, logId}"""
    headers = dict(headers or {})
    # brotli standart kütüphanede yok: sunucu br ile sıkıştırmasın
    for k in list(headers):
        if k.lower() == "accept-encoding":
            parts = [p.strip() for p in headers[k].split(",")]
            headers[k] = ", ".join(p for p in parts if p and not p.startswith("br")) or "identity"
    started = time.monotonic()

    def log(**fields):
        return logger.record(service=service, kind=kind, url=url, method=method, request_headers=headers, request_body=body,
                             duration_ms=int((time.monotonic() - started) * 1000), **fields)

    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        try:
            resp = _opener(insecure_tls).open(req, timeout=timeout_ms / 1000)
        except urllib.error.HTTPError as he:  # 4xx/5xx da bir yanıttır
            resp = he
        raw = resp.read()
        res_headers = {k.lower(): v for k, v in resp.headers.items()}
        try:
            text = _decode(raw, res_headers.get("content-encoding", "")).decode("utf-8", errors="replace")
        except (OSError, zlib.error) as e:
            raise RuntimeError(f"Yanıt açılamadı: {e}") from e
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        res = {"status": getattr(resp, "status", None) or resp.code, "headers": res_headers, "text": text, "json": parsed}
    except RuntimeError as e:
        log(error=str(e))
        raise
    except Exception as e:
        err = _friendly(e, url, tls_hint)
        log(error=str(err))
        raise err from e
    meta = None
    if meta_fn:
        try:
            meta = meta_fn(res)
        except Exception:
            meta = None
    res["logId"] = log(status=res["status"], response_headers=res_headers, response_body=text, meta=meta)["id"]
    return res
