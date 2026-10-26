"""Langfuse izleme (isteğe bağlı) — OpenTelemetry OTLP/HTTP JSON, bağımlılıksız

  .env:
    LANGFUSE_URL=http://langfuse.kurum.local:3000     boşsa izleme kapalı
    LANGFUSE_PUBLIC_KEY=pk-lf-...   LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_PROJECT_ID=unixai-morpheus               arayüzde "Langfuse'ta aç" bağlantısı için (boşsa API'den bulunur)
    LANGFUSE_TLS_INSECURE=false   LANGFUSE_CAPTURE_IO=true (false: soru/cevap/argüman gönderilmez, yalnızca adlar ve süreler)
    LANGFUSE_ENVIRONMENT=production

  Ne gönderilir: ajan çalışması (agent) → araç adımları (tool / agent) → GPT çağrıları (generation: model, token);
  sunucu analizi (chain). UnixAI MorpheusAI'ye soru sorduğunda iz bağlamı MCP 'context.trace' ile aktarılır:
  iki ajanın konuşması Langfuse'ta tek iz olarak görünür.
  Gönderim arka planda toplu yapılır; Langfuse erişilemezse izler düşer, uygulama etkilenmez. Token/parola maskelenir.
"""
from __future__ import annotations

import base64
import contextvars
import json
import queue
import secrets
import ssl
import threading
import time
import urllib.request

from .env import env

SERVICE = "unixai-os"
_cur: contextvars.ContextVar = contextvars.ContextVar("lf_span", default=None)
_q: queue.Queue = queue.Queue(maxsize=5000)
_stats = {"sent": 0, "dropped": 0, "failed": 0, "lastError": None, "lastSentAt": None}
_project = {"id": None, "checked": 0.0}
_worker = None
_lock = threading.Lock()


def enabled() -> bool:
    return bool(env("LANGFUSE_URL") and env("LANGFUSE_PUBLIC_KEY") and env("LANGFUSE_SECRET_KEY"))


def _capture() -> bool:
    return (env("LANGFUSE_CAPTURE_IO") or "true").lower() not in ("0", "false", "no", "off")


def _auth() -> str:
    return "Basic " + base64.b64encode(f"{env('LANGFUSE_PUBLIC_KEY')}:{env('LANGFUSE_SECRET_KEY')}".encode()).decode()


def _ctx():
    return ssl._create_unverified_context() if (env("LANGFUSE_TLS_INSECURE") or "").lower() in ("1", "true", "yes", "on") else None


def _clip(v, n=20000):
    if v is None:
        return None
    if not isinstance(v, str):
        try:
            from . import logger

            v = json.dumps(logger.mask_body(json.dumps(v, ensure_ascii=False, default=str)), ensure_ascii=False, default=str)
        except Exception:
            v = json.dumps(v, ensure_ascii=False, default=str)
    return v if len(v) <= n else v[:n] + f"…[{len(v) - n} karakter kesildi]"


def _val(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (dict, list)):
        return {"stringValue": json.dumps(v, ensure_ascii=False, default=str)}
    return {"stringValue": str(v)}


class _Noop:
    trace_id = span_id = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def set(self, *a, **k):
        return self

    def output(self, *a, **k):
        return self

    def usage(self, *a, **k):
        return self


class Span:
    """Bir gözlem (observation). with ile kullanılır; içindeki çağrılar bunun altına bağlanır."""

    def __init__(self, name, type_="span", input=None, parent=None, trace=None, **attrs):
        cur = _cur.get()
        if parent and parent.get("trace_id"):
            self.trace_id, self.parent_id, self.trace_attrs = parent["trace_id"], parent.get("span_id"), dict(trace or {})
        elif cur:
            self.trace_id, self.parent_id, self.trace_attrs = cur.trace_id, cur.span_id, {**cur.trace_attrs, **(trace or {})}
        else:
            self.trace_id, self.parent_id, self.trace_attrs = secrets.token_hex(16), None, {"langfuse.trace.name": name, **(trace or {})}
        self.span_id = secrets.token_hex(8)
        self.name, self.start = name, time.time_ns()
        self.attrs = {"langfuse.observation.type": type_, **{k: v for k, v in attrs.items() if v is not None}}
        if input is not None and _capture():
            self.attrs["langfuse.observation.input"] = _clip(input)
        self.error = None
        self._token = None

    def set(self, **kv):
        self.attrs.update({k: v for k, v in kv.items() if v is not None})
        return self

    def output(self, v):
        if v is not None and _capture():
            self.attrs["langfuse.observation.output"] = _clip(v)
        return self

    def usage(self, u: dict | None, model=None):
        if model:
            self.attrs["gen_ai.response.model"] = model
        if isinstance(u, dict):
            i, o = u.get("prompt_tokens") or u.get("input_tokens"), u.get("completion_tokens") or u.get("output_tokens")
            if i is not None:
                self.attrs["gen_ai.usage.input_tokens"] = int(i)
            if o is not None:
                self.attrs["gen_ai.usage.output_tokens"] = int(o)
            self.attrs["langfuse.observation.usage_details"] = json.dumps(
                {k: int(v) for k, v in (("input", i), ("output", o), ("total", u.get("total_tokens"))) if v is not None})
        return self

    def ids(self) -> dict:
        return {"trace_id": self.trace_id, "span_id": self.span_id}

    def __enter__(self):
        self._token = _cur.set(self)
        return self

    def __exit__(self, et, ev, tb):
        if ev is not None:
            self.error = str(ev)[:500]
        _cur.reset(self._token)
        self.end()
        return False

    def end(self):
        attrs = {**self.trace_attrs, **self.attrs, "langfuse.environment": env("LANGFUSE_ENVIRONMENT") or "production"}
        if self.error:
            attrs["langfuse.observation.level"] = "ERROR"
            attrs["langfuse.observation.status_message"] = self.error
        span = {"traceId": self.trace_id, "spanId": self.span_id, "name": self.name, "kind": 1,
                "startTimeUnixNano": str(self.start), "endTimeUnixNano": str(time.time_ns()),
                "attributes": [{"key": k, "value": _val(v)} for k, v in attrs.items() if v is not None],
                "status": {"code": 2, "message": self.error} if self.error else {"code": 1}}
        if self.parent_id:
            span["parentSpanId"] = self.parent_id
        try:
            _q.put_nowait(span)
        except queue.Full:
            _stats["dropped"] += 1
        _ensure_worker()


def span(name, type_="span", input=None, parent=None, trace=None, **attrs):
    """İzleme kapalıysa hiçbir şey yapmayan nesne döner (maliyetsiz)"""
    if not enabled():
        return _Noop()
    return Span(name, type_, input, parent, trace, **attrs)


def generation(name, messages, model=None, **attrs):
    msgs = [{"role": m.get("role"), "content": (m.get("content") or "")[:4000] if isinstance(m.get("content"), str) else m.get("content")}
            for m in (messages or [])[-12:] if isinstance(m, dict)]
    return span(name, "generation", input=msgs, **{"gen_ai.request.model": model, **attrs})


def current() -> dict | None:
    s = _cur.get()
    return s.ids() if s else None


def current_trace_id() -> str | None:
    s = _cur.get()
    return s.trace_id if s else None


# ---------------- gönderim ----------------
def _ensure_worker():
    global _worker
    with _lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, name="langfuse-export", daemon=True)
        _worker.start()


def _loop():
    while True:
        batch = [_q.get()]
        deadline = time.time() + 2
        while len(batch) < 200 and time.time() < deadline:
            try:
                batch.append(_q.get(timeout=max(0.05, deadline - time.time())))
            except queue.Empty:
                break
        _send(batch)


def _send(spans: list) -> None:
    body = json.dumps({"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": SERVICE}}]},
                                          "scopeSpans": [{"scope": {"name": SERVICE}, "spans": spans}]}]}, ensure_ascii=False).encode()
    req = urllib.request.Request(env("LANGFUSE_URL").rstrip("/") + "/api/public/otel/v1/traces", data=body, method="POST",
                                 headers={"content-type": "application/json", "authorization": _auth(), "x-langfuse-ingestion-version": "4"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=15, context=_ctx()) as r:
                r.read()
            _stats.update(sent=_stats["sent"] + len(spans), lastSentAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), lastError=None)
            return
        except Exception as e:  # Langfuse kapalıysa uygulama etkilenmesin
            _stats["lastError"] = str(e)[:200]
            if attempt == 1:
                time.sleep(2)
    _stats["failed"] += len(spans)


def project_id() -> str | None:
    if env("LANGFUSE_PROJECT_ID"):
        return env("LANGFUSE_PROJECT_ID")
    if _project["id"] or time.time() - _project["checked"] < 300 or not enabled():
        return _project["id"]
    _project["checked"] = time.time()
    try:
        req = urllib.request.Request(env("LANGFUSE_URL").rstrip("/") + "/api/public/projects", headers={"authorization": _auth()})
        with urllib.request.urlopen(req, timeout=5, context=_ctx()) as r:
            data = json.loads(r.read())
        _project["id"] = ((data.get("data") or [{}])[0]).get("id")
    except Exception as e:
        _stats["lastError"] = str(e)[:200]
    return _project["id"]


def status() -> dict:
    on = enabled()
    pid = project_id() if on else None
    base = (env("LANGFUSE_URL") or "").rstrip("/")
    return {"enabled": on, "url": base or None, "projectId": pid, "captureIO": _capture(), "queue": _q.qsize(), **_stats,
            "traceUrl": f"{base}/project/{pid}/traces/" if on and pid else None}
