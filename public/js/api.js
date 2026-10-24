/*
 * UnixAI OS - API katmanı
 * Tüm backend çağrıları buradan geçer.
 */
const Settings = (() => {
  const KEY = "unixai.settings";
  const defaults = { userName: "" };
  let cache;
  const load = () => {
    if (cache) return cache;
    try { cache = { ...defaults, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; }
    catch { cache = { ...defaults }; }
    return cache;
  };
  return {
    get: load,
    save(values) {
      cache = { ...load(), ...values };
      try { localStorage.setItem(KEY, JSON.stringify(cache)); } catch {}
      return cache;
    },
  };
})();

const Api = (() => {
  async function request(path, { method = "GET", body } = {}) {
    const s = Settings.get();
    const headers = { "Content-Type": "application/json" };
    if (s.userName) headers["X-UnixAI-User"] = encodeURIComponent(s.userName); // token raporlarında "kim" bilgisi
    const res = await fetch("/api" + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    // Oturum düştüyse giriş ekranını göster
    if (res.status === 401 && data.authRequired && !path.startsWith("/auth/")) window.dispatchEvent(new Event("unixai:auth-required"));
    if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status, data });
    return data;
  }
  const qs = (q) => new URLSearchParams(Object.entries(q).filter(([, v]) => v)).toString();
  const enc = encodeURIComponent;

  return {
    health: (deep = false) => request(deep ? "/health?deep=1" : "/health"),
    me: () => request("/auth/me"),
    login: (username, password) => request("/auth/login", { method: "POST", body: { username, password } }),
    logout: () => request("/auth/logout", { method: "POST", body: {} }),

    overview: () => request("/overview"),
    servers: () => request("/servers"),
    saveServer: (server, original) => request("/servers", { method: "POST", body: { server, original } }),
    deleteServer: (name) => request(`/servers/${enc(name)}`, { method: "DELETE", body: {} }),
    importServers: (text) => request("/servers/import", { method: "POST", body: { text } }),
    testServer: (name, server) => request(`/servers/${enc(name)}/test`, { method: "POST", body: { server } }),
    snapshot: (name, run) => request(`/servers/${enc(name)}/snapshot${run ? `?run=${enc(run)}` : ""}`),
    raw: (name, run) => request(`/servers/${enc(name)}/raw${run ? `?run=${enc(run)}` : ""}`),
    serverHistory: (name) => request(`/servers/${enc(name)}/history`),

    runs: () => request("/runs"),
    run: (id) => request(`/runs/${enc(id)}`),
    startRun: (body) => request("/runs", { method: "POST", body }),
    collected: () => request("/collected"),

    chat: ({ messages, context }) => request("/chat", { method: "POST", body: { messages, context } }),

    morpheus: () => request("/integrations/morpheus"),
    saveMorpheus: (m) => request("/integrations/morpheus", { method: "POST", body: m }),
    testMorpheus: () => request("/integrations/morpheus/test", { method: "POST", body: {} }),
    remediations: (server) => request(`/remediations${server ? `?server=${enc(server)}` : ""}`),
    sendRemediation: (server, actionIndex, run) => request("/remediations", { method: "POST", body: { server, actionIndex, run } }),
    remediation: (id) => request(`/remediations/${enc(id)}`),
    connection: () => request("/connection"),
    saveConnection: (c) => request("/connection", { method: "POST", body: c }),
    testConnection: (step) => request("/connection/test", { method: "POST", body: { step } }),
    settings: () => request("/settings"),
    saveSettings: (s) => request("/settings", { method: "POST", body: s }),
    prompts: () => request("/prompts"),
    savePrompts: (system) => request("/prompts", { method: "POST", body: { system } }),

    logs: () => request("/logs?limit=200"),
    log: (id) => request(`/logs/${enc(id)}`),
    usage: (q = {}) => request(`/usage?${qs(q)}`),
    usageToday: () => request("/usage/today"),
    limits: () => request("/limits"),
    saveLimits: (l) => request("/limits", { method: "POST", body: l }),
  };
})();

const API_CONTRACT = `GET  /api/health[?deep=1]        → { status, mode: "genai"|"demo", genai, auth, scheduler }
GET  /api/overview               → { servers[]: { name, host, tags, latest: { score, status, summary, headline, p1 } }, fleet, lastRun }
GET  /api/servers                → { servers[], defaults (SSH), sshKey }
POST /api/servers                ← { server: { name, host, port?, user?, keyFile?, tags[], enabled }, original? }
POST /api/servers/import         ← { text: "ad adres [kullanıcı] [port]\\n…" }
DELETE /api/servers/:name
POST /api/servers/:name/test     → { ok, output | error }          (SSH bağlantı testi)
GET  /api/servers/:name/snapshot[?run=]  → { metrics, findings, analysis, headline, previous }
GET  /api/servers/:name/raw[?run=]       → { sections: { <bölüm>: "<ham çıktı>" } }
GET  /api/servers/:name/history  → [{ runId, time, score, status, headline }]
GET  /api/runs                   → Run[]
GET  /api/runs/:id               → Run
POST /api/runs                   ← { mode: "collect"|"reanalyze"|"demo", servers?: string[], ai?: boolean }  → 202 Run
GET  /api/collected              → { sections[], servers[]: { name, sectionLines: { <bölüm>: satır } } }
POST /api/chat                   ← { messages: {role, content}[], context?: { server } | { fleet: true } } → { reply }
GET|POST /api/integrations/morpheus   ← { url, token, targetField: name|host, autoSend, autoSendPriority }  (MorpheusAI köprüsü)
POST /api/remediations           ← { server, actionIndex }  → MorpheusAI'de standartlara uygun düzeltme playbook'u
GET  /api/remediations[?server=] → gönderilen düzeltmeler ve MorpheusAI'deki güncel durum
     Servisler arası / MCP: Authorization: Bearer <UNIXAI_API_TOKEN>
GET  /api/connection             → GenAI bağlantısı (gizli alanlar maskeli)
POST /api/connection             ← { tms: { url, method, body, headers[] }, chat: { url, model, modelField, headers[], bodyExtra }, insecureTls, timeoutMs }
POST /api/connection/test        ← { step: "token"|"chat" }   → { token: { ok }, chat?: { ok, reply } }
GET  /api/settings / POST        ← { ssh, schedule, analysis, thresholds }
GET  /api/prompts / POST         ← { system }
GET  /api/usage?from=&to=&user=&kind=   GET /api/usage/today   GET|POST /api/limits
GET  /api/logs                   GET /api/logs/:id   (GenAI istek/cevapları, gizli alanlar maskeli)

Akış:  Ayarlar'daki Token URL ──(body+header)──▶ token ──▶ Chat URL (Authorization: Bearer <token>, model, messages)
Run = { id, trigger: manual|schedule|reanalyze|demo, status: running|done|partial|failed, ai, servers[]: { name, collect, analysis, score, status, error } }`;
