/* UnixAI OS - uygulama */
const State = {
  health: null,
  overview: null,
  inventory: [],
  runs: [],
  runTimer: null,
  activeRun: null,
  runSelect: new Set(),
  sd: { name: null, run: null, snap: null, hist: [], tab: "actions" },
  chat: [],
  chatBusy: false,
  logTimer: null,
  settings: null,
  connection: null,
  srvStatus: "all",
  srvView: readView(),
};

function readView() {
  try { return localStorage.getItem("unixai.srvView") || "tiles"; } catch { return "tiles"; }
}

const STATUS_META = {
  OK: { label: "Sağlıklı", cls: "green", icon: "check" },
  WARNING: { label: "Uyarı", cls: "gold", icon: "alert" },
  CRITICAL: { label: "Kritik", cls: "red", icon: "alert" },
  FAILED: { label: "Ulaşılamadı", cls: "red", icon: "alert" },
};
const statusTag = (s) => {
  const m = STATUS_META[s];
  return m ? `<span class="label ${m.cls}">${icon(m.icon, "i i-xs")}${m.label}</span>` : `<span class="label">Toplanmadı</span>`;
};
const prioTag = (p) => `<span class="label prio-${esc(p)}">${esc(p)}</span>`;
/** Sunucunun genel bakıştaki durumu: OK | WARNING | CRITICAL | FAILED | NONE */
const srvState = (s) => (!s.latest ? "NONE" : s.latest.collect === "failed" ? "FAILED" : s.latest.status || "NONE");
const ENGINE_LABEL = { genai: "GenAI", rules: "Kural motoru", "genai-text+rules": "GenAI (metin) + kural", demo: "Demo" };
const TRIGGER_LABEL = { manual: "Elle", schedule: "Zamanlayıcı", reanalyze: "Yeniden analiz", demo: "Demo" };
const RUN_STATUS = { running: ["Sürüyor", "blue"], done: ["Tamamlandı", "green"], partial: ["Kısmen", "gold"], failed: ["Başarısız", "red"], interrupted: ["Yarıda kaldı", "gold"] };
const KIND_LABEL = { "server-analysis": "Sunucu analizi", "fleet-summary": "Filo özeti", chat: "Sohbet", test: "Bağlantı testi", "tms-token": "Token" };
const SECTION_LABEL = {
  hostname: "Host adı", os_release: "OS sürümü", kernel: "Kernel", uptime_seconds: "Uptime", nproc: "CPU sayısı",
  loadavg: "Load", meminfo: "Bellek", df: "Disk (df)", df_inode: "Inode", vmstat: "vmstat", failed_units: "Failed unit",
  journal_errors: "Journal hataları", ssh_failures: "SSH hatalı giriş", kernel_errors: "Kernel uyarıları", selinux: "SELinux",
  avc_denials: "AVC", chrony: "Zaman (chrony)", security_updates: "Güvenlik güncel.", needs_restarting: "Reboot gerekli",
  top_processes: "Top prosesler", zombies: "Zombie",
};
const TH_LABEL = {
  fs_warn_pct: "Disk uyarı %", fs_crit_pct: "Disk kritik %", inode_warn_pct: "Inode uyarı %",
  mem_avail_warn_pct: "Kullanılabilir bellek alt sınır %", swap_warn_pct: "Swap uyarı %",
  load_per_core_warn: "Load/core uyarı", load_per_core_crit: "Load/core kritik", iowait_warn_pct: "I/O wait uyarı %",
  ssh_fail_warn: "Başarısız SSH girişi (24 sa)", time_offset_warn_sec: "Saat kayması (sn)", uptime_warn_days: "Uptime uyarı (gün)",
  zombie_warn: "Zombie proses", fs_days_to_full_warn: "Diskin dolmasına kalan gün",
};

const fmtNum = (n) => Number(n || 0).toLocaleString("tr-TR");
const fmtK = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : String(n));
const isoDay = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const fmtDur = (ms) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : ms < 60000 ? `${(ms / 1000).toFixed(1)} sn` : `${Math.round(ms / 60000)} dk`);
const fmtMb = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB` : `${mb} MB`);
const fmtKb = (kb) => fmtMb(Math.round(kb / 1024));
const ago = (iso) => {
  if (!iso) return "—";
  const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
  return m < 1 ? "az önce" : m < 60 ? `${m} dk önce` : m < 1440 ? `${Math.round(m / 60)} sa önce` : `${Math.round(m / 1440)} gün önce`;
};
const loadingView = (text) => `<div class="loading"><div class="spinner"></div><div>${esc(text)}</div></div>`;
const errorView = (e) => `<div class="alert danger"><b>Hata:</b> ${esc(e.message || e)}</div>`;
const pctCell = (v, warn, crit) =>
  v == null ? `<span class="muted">—</span>` : `<span class="${v >= crit ? "status-err" : v >= warn ? "status-warn" : ""}">%${v}</span>`;

/* ================================================================== */
/*  Router                                                             */
/* ================================================================== */
const ROUTES = ["dashboard", "server", "runs", "collected", "ask", "a2a", "agent", "servers", "usage", "logs", "audit", "settings"];

function route() {
  const [name, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  const arg = rest.length ? decodeURIComponent(rest.join("/")) : null;
  const r = ROUTES.includes(name) ? name : "dashboard";
  $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === r));
  $$(".sidebar a").forEach((a) => a.classList.toggle("active", a.dataset.route === (r === "server" ? "dashboard" : r)));
  $("#sidebar").classList.remove("open");
  if (r === "dashboard") { loadOverview(); refreshApprovals(); }
  if (r === "server" && rest[0]) {
    if (rest[1]) State.sd.tab = rest[1];
    loadServer(decodeURIComponent(rest[0]));
  }
  if (r === "runs") loadRuns();
  if (r === "collected") loadCollected();
  if (r === "ask") { renderChatContext(arg); setTimeout(() => $("#chatText").focus(), 50); }
  if (r === "servers") loadInventory();
  if (r === "agent") loadAgentPage();
  if (r === "a2a") loadA2A();
  if (r === "logs") loadLogs();
  if (r === "audit") loadAudit();
  if (r === "usage") loadUsage();
  if (r === "settings") loadSettingsPage();
  setLogAuto(r === "logs" && $("#logAuto").checked);
  window.scrollTo(0, 0);
}

/* ================================================================== */
/*  Genel bakış                                                        */
/* ================================================================== */
async function loadOverview() {
  try {
    State.overview = await Api.overview();
    renderOverview();
  } catch (e) {
    $("#hero").innerHTML = errorView(e);
  }
}

const thresholds = () => State.settings?.thresholds || { fs_warn_pct: 85, fs_crit_pct: 95, mem_avail_warn_pct: 10, load_per_core_warn: 1.5, load_per_core_crit: 3, iowait_warn_pct: 20, ssh_fail_warn: 100 };

function renderOverview() {
  const o = State.overview;
  const counts = { CRITICAL: 0, WARNING: 0, OK: 0, FAILED: 0, NONE: 0 };
  o.servers.forEach((s) => counts[srvState(s)]++);
  const analyzed = o.servers.filter((s) => s.latest && s.latest.collect !== "failed" && s.latest.score != null);
  const avg = analyzed.length ? Math.round(analyzed.reduce((n, s) => n + s.latest.score, 0) / analyzed.length) : null;
  const p1 = analyzed.reduce((n, s) => n + (s.latest.p1 || 0), 0);
  const findings = analyzed.reduce((n, s) => n + (s.latest.findings || 0), 0);
  const lr = o.lastRun;
  $("#modeLabel").className = `label ${o.mode === "genai" ? "blue" : "gold"}`;
  $("#modeLabel").innerHTML = o.mode === "genai" ? `${icon("sparkles", "i i-xs")}GenAI analizi açık` : `${icon("alert", "i i-xs")}Demo modu · GenAI bağlı değil`;
  const bad = counts.CRITICAL + counts.FAILED;
  $("#navCrit").textContent = bad;
  $("#navCrit").classList.toggle("hidden", !bad);
  renderNavFoot();

  if (!o.servers.length) {
    $("#hero").innerHTML = `<div class="card onboard">
      <div class="empty-ill">${icon("servers")}</div>
      <h2>UnixAI OS'a hoş geldiniz</h2>
      <p class="muted">RHEL sunucularınızdan agentless olarak log ve metrik toplayıp GenAI ile analiz etmek için üç adım:</p>
      <ol class="steps">
        <li><a href="#/settings">${icon("sparkles")}<b>GenAI bağlantısı</b><span>Token servisi ve GPT adreslerini girin</span></a></li>
        <li><a href="#/servers">${icon("servers")}<b>Sunucu ekle</b><span>RHEL sunucularını envantere ekleyin, SSH anahtarını yetkilendirin</span></a></li>
        <li><a href="#/runs">${icon("bolt")}<b>Topla ve analiz et</b><span>İlk toplamayı başlatın; sonra her gün otomatik</span></a></li>
      </ol>
      <button class="btn" id="obDemo">${icon("flask")}Önce demo veriyle dene</button>
    </div>`;
    $("#obDemo").onclick = (e) => startRun("demo", e.currentTarget);
    $("#fleetCard").innerHTML = "";
    renderServers();
    return;
  }

  const total = o.servers.length;
  const seg = (k, cls) => (counts[k] ? `<i class="${cls}" style="flex:${counts[k]}" title="${STATUS_META[k]?.label || "Toplanmadı"}: ${counts[k]}"></i>` : "");
  $("#hero").innerHTML = `<div class="hero">
    <div class="card hero-health">
      ${avg != null ? gauge(avg, { size: 132, label: "filo skoru", stroke: 11 }) : `<div class="empty-ill">${icon("servers")}</div>`}
      <div class="hero-meta">
        <h2>${total} sunucu izleniyor</h2>
        <div class="statbar">${seg("CRITICAL", "s-crit")}${seg("FAILED", "s-crit")}${seg("WARNING", "s-warn")}${seg("OK", "s-ok")}${seg("NONE", "s-off")}</div>
        <div class="legend">
          <span><i class="dot s-crit"></i>Kritik <b>${counts.CRITICAL}</b></span>
          <span><i class="dot s-warn"></i>Uyarı <b>${counts.WARNING}</b></span>
          <span><i class="dot s-ok"></i>Sağlıklı <b>${counts.OK}</b></span>
          ${counts.FAILED ? `<span><i class="dot s-crit"></i>Ulaşılamadı <b>${counts.FAILED}</b></span>` : ""}
          ${counts.NONE ? `<span><i class="dot s-off"></i>Toplanmadı <b>${counts.NONE}</b></span>` : ""}
        </div>
        <div class="muted sm">${icon("clock", "i i-xs")} ${o.scheduler.enabled ? `Otomatik toplama her gün <b>${esc(o.scheduler.time)}</b>` : "Otomatik toplama kapalı"}</div>
      </div>
    </div>
    <div class="hero-kpis">
      <div class="kpi kpi-p1"><div class="k-label">${icon("bolt", "i i-xs")}P1 aksiyonlar</div><div class="k-val">${p1}</div><div class="k-sub">hemen yapılması gereken</div></div>
      <div class="kpi"><div class="k-label">${icon("shield", "i i-xs")}Kural bulguları</div><div class="k-val">${findings}</div><div class="k-sub">${analyzed.length} sunucuda eşik aşımı</div></div>
      <div class="kpi"><div class="k-label">${icon("refresh", "i i-xs")}Son toplama</div><div class="k-val sm">${lr ? esc(ago(lr.finishedAt || lr.startedAt)) : "—"}</div>
        <div class="k-sub">${lr ? `${esc(TRIGGER_LABEL[lr.trigger] || lr.trigger)} · <span class="label ${RUN_STATUS[lr.status]?.[1] || ""}">${esc(RUN_STATUS[lr.status]?.[0] || lr.status)}</span>` : "henüz yok"}</div></div>
    </div>
  </div>`;

  const f = o.fleet;
  $("#fleetCard").innerHTML = !f ? "" : `<div class="panel-grid">
    <div class="card">
      <div class="card-head"><h3>${icon("list")}Bugünün işleri</h3><span class="muted sm">${esc(ENGINE_LABEL[f.engine] || f.engine)} · ${esc(fmtTime(f.time))}</span></div>
      ${f.todays_top_actions.length ? `<ol class="todo">${f.todays_top_actions.map((x) => `<li><div><div class="t-title"><a href="#/server/${encodeURIComponent(x.server)}">${esc(x.server)}</a>${esc(x.title)}</div>
        ${x.why_now ? `<div class="muted sm">${esc(x.why_now)}</div>` : ""}</div></li>`).join("")}</ol>` : `<p class="muted">Bugün için öncelikli iş yok.</p>`}
    </div>
    <div class="card">
      <div class="card-head"><h3>${icon("sparkles")}Filo özeti</h3></div>
      ${f.aiError ? `<div class="alert danger">GenAI filo özeti alınamadı, kural özeti gösteriliyor: ${esc(f.aiError)}</div>` : ""}
      <p class="summary">${esc(f.fleet_summary)}</p>
      ${f.cross_server_patterns.length ? `<div class="sub-h">Sunucular arası örüntüler</div><ul class="patterns">${f.cross_server_patterns.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    </div>
  </div>`;
  renderServers();
  animateGauges($("#hero"));
}

function renderNavFoot() {
  const h = State.health;
  if (!h) return;
  $("#navFoot").innerHTML = `
    <div class="row"><span>Analiz</span><b>${h.mode === "genai" ? esc(h.genai.model || "GenAI") : "Kural motoru"}</b></div>
    <div class="row"><span>Zamanlayıcı</span><b>${h.scheduler.enabled ? esc(h.scheduler.time) : "kapalı"}</b></div>
    <div class="row"><span>Envanter</span><b>${h.servers} sunucu</b></div>`;
}

function meter(label, value, pct, level, title = "") {
  return `<div class="meter" title="${esc(title)}"><span>${label}</span><div class="track"><i class="${level}" style="width:${Math.max(0, Math.min(100, pct))}%"></i></div><b>${value}</b></div>`;
}

function serverMeters(h) {
  const t = thresholds();
  const lvl = (v, w, c) => (v >= c ? "m-crit" : v >= w ? "m-warn" : "");
  const memUsed = h.mem_available_pct != null ? Math.round(100 - h.mem_available_pct) : null;
  const memLvl = h.mem_available_pct == null ? "" : h.mem_available_pct < t.mem_avail_warn_pct ? "m-crit" : h.mem_available_pct < t.mem_avail_warn_pct * 2 ? "m-warn" : "";
  return `<div class="meters">
    ${meter("Disk", h.fs_max_pct != null ? `%${h.fs_max_pct}` : "—", h.fs_max_pct ?? 0, lvl(h.fs_max_pct ?? 0, t.fs_warn_pct, t.fs_crit_pct), h.fs_max_mount ? `En dolu: ${h.fs_max_mount}` : "")}
    ${meter("Bellek", memUsed != null ? `%${memUsed}` : "—", memUsed ?? 0, memLvl, "Kullanılan bellek")}
    ${meter("Load/core", h.load_per_core ?? "—", ((h.load_per_core ?? 0) / t.load_per_core_crit) * 100, lvl(h.load_per_core ?? 0, t.load_per_core_warn, t.load_per_core_crit), "15 dk load / CPU sayısı")}
  </div>`;
}

function filteredServers() {
  const q = $("#srvFilter").value.toLowerCase();
  const order = { FAILED: 0, CRITICAL: 1, WARNING: 2, OK: 3, NONE: 4 };
  return (State.overview?.servers || [])
    .filter((s) => State.srvStatus === "all" || srvState(s) === State.srvStatus)
    .filter((s) => !q || `${s.name} ${s.host} ${s.tags.join(" ")}`.toLowerCase().includes(q))
    .sort((a, b) => order[srvState(a)] - order[srvState(b)] || (a.latest?.score ?? 101) - (b.latest?.score ?? 101) || a.name.localeCompare(b.name));
}

function renderServers() {
  const all = State.overview?.servers || [];
  const c = (k) => all.filter((s) => srvState(s) === k).length;
  const opts = [["all", "Tümü", all.length], ["CRITICAL", "Kritik", c("CRITICAL")], ["WARNING", "Uyarı", c("WARNING")], ["OK", "Sağlıklı", c("OK")], ["FAILED", "Ulaşılamadı", c("FAILED")]]
    .filter(([k, , n]) => k === "all" || n);
  if (!opts.some(([k]) => k === State.srvStatus)) State.srvStatus = "all";
  $("#srvStatus").innerHTML = opts.map(([k, l, n]) => `<button data-st="${k}" class="${State.srvStatus === k ? "on" : ""}">${l} <span class="cnt">${n}</span></button>`).join("");
  $$("#srvView button").forEach((b) => b.classList.toggle("on", b.dataset.view === State.srvView));
  $("#srvTiles").classList.toggle("hidden", State.srvView !== "tiles");
  $("#srvTableWrap").classList.toggle("hidden", State.srvView !== "table");
  const rows = filteredServers();
  const emptyMsg = all.length ? "Filtreye uyan sunucu yok." : "Henüz sunucu yok.";
  if (State.srvView === "tiles") {
    $("#srvTiles").innerHTML = rows.map((s) => {
      const l = s.latest, h = l?.headline || {}, st = srvState(s);
      return `<div class="tile st-${st}" data-href="#/server/${encodeURIComponent(s.name)}" tabindex="0">
        <div class="tile-top">
          <div><div class="tile-name">${esc(s.name)}</div><div class="tile-host">${esc(s.host || "")}${h.os ? ` · ${esc(h.os)}` : ""}</div></div>
          ${l?.score != null && st !== "FAILED" ? `<div class="tile-score"><b style="color:${scoreColor(l.score)}">${l.score}</b><small>skor</small></div>` : ""}
        </div>
        <div class="tile-tags">${statusTag(st === "NONE" ? null : st)}${s.demo ? `<span class="label">demo</span>` : ""}${s.inInventory && !s.enabled ? `<span class="label">pasif</span>` : ""}${s.tags.map((t) => `<span class="label">${esc(t)}</span>`).join("")}</div>
        ${st === "FAILED" ? `<div class="tile-err">${esc(l.error || "Toplama başarısız")}</div>` : l?.headline ? serverMeters(h) : `<div class="tile-err muted">Henüz toplanmadı.</div>`}
        <div class="tile-foot"><span>${icon("clock", "i i-xs")} ${l ? esc(ago(l.collectedAt)) : "—"}${l?.engine ? ` · ${esc(ENGINE_LABEL[l.engine] || l.engine)}` : ""}</span>
          ${l?.p1 != null ? `<span class="p1 ${l.p1 ? "" : "zero"}">P1 ${l.p1}</span>` : ""}</div>
      </div>`;
    }).join("") || `<div class="empty">${emptyMsg}</div>`;
    return;
  }
  const th = thresholds();
  $("#srvTable").innerHTML = `<tr><th>Sunucu</th><th>Durum</th><th>Sağlık</th><th>En dolu disk</th><th class="num">Load/core</th><th class="num">Boş bellek</th><th class="num">P1</th><th>Son toplama</th></tr>` +
    (rows.map((s) => {
      const l = s.latest, h = l?.headline || {};
      return `<tr class="clickable" data-href="#/server/${encodeURIComponent(s.name)}">
        <td><div class="t-name">${esc(s.name)}</div><div class="t-sub">${esc(s.host || "")}${h.os ? ` · ${esc(h.os)}` : ""}</div></td>
        <td>${statusTag(srvState(s) === "NONE" ? null : srvState(s))}</td>
        <td>${l?.score != null && l.collect !== "failed" ? scoreBar(l.score) : `<span class="muted sm">—</span>`}</td>
        <td>${h.fs_max_pct != null ? `${pctCell(h.fs_max_pct, th.fs_warn_pct, th.fs_crit_pct)} <span class="t-sub mono">${esc(h.fs_max_mount)}</span>` : "—"}</td>
        <td class="num">${h.load_per_core ?? "—"}</td>
        <td class="num">${h.mem_available_pct != null ? `%${h.mem_available_pct}` : "—"}</td>
        <td class="num">${l?.p1 ? `<span class="p1">${l.p1}</span>` : l?.p1 === 0 ? "0" : "—"}</td>
        <td class="t-sub">${l ? esc(ago(l.collectedAt)) : "—"}</td></tr>`;
    }).join("") || `<tr><td colspan="8" class="muted">${emptyMsg}</td></tr>`);
}

/* ================================================================== */
/*  Sunucu detayı                                                      */
/* ================================================================== */
async function loadServer(name, run) {
  State.sd = { ...State.sd, name, run: run || null };
  $("#sdName").textContent = name;
  $("#sdStatus").innerHTML = "";
  $("#sdSub").innerHTML = "";
  $("#sdVitals").classList.add("hidden");
  $("#sdAsk").href = `#/ask/${encodeURIComponent(name)}`;
  $("#sdBody").innerHTML = loadingView("Yükleniyor…");
  const [snap, hist, rems, a2as] = await Promise.allSettled([Api.snapshot(name, run), Api.serverHistory(name), Api.remediations(name), Api.a2a({ server: name })]);
  State.sd.a2aCount = a2as.status === "fulfilled" ? a2as.value.items.length : 0;
  State.sd.hist = hist.status === "fulfilled" ? hist.value : [];
  State.sd.rems = rems.status === "fulfilled" ? rems.value.items : [];
  $("#sdRun").innerHTML = State.sd.hist.map((h) => `<option value="${esc(h.runId)}">${esc(fmtTime(h.time))} · ${h.collect === "failed" ? "ulaşılamadı" : `skor ${h.score ?? "—"}`}</option>`).join("");
  if (snap.status === "rejected") {
    const last = State.sd.hist[0];
    if (last?.collect === "failed") $("#sdStatus").innerHTML = statusTag("FAILED");
    $("#sdBody").innerHTML = `<div class="card">${last?.error ? `<div class="alert danger"><b>Son toplama başarısız:</b> ${esc(last.error)}</div>` : ""}
      <div class="empty"><div class="empty-ill">${icon("servers")}</div><h4>Bu sunucu için analiz yok</h4><p>${esc(snap.reason.message)}</p>
      <p class="muted sm">SSH erişimini Sunucular ekranındaki <b>SSH testi</b> ile kontrol edip <b>Bu sunucuyu topla</b>'ya basın.</p></div></div>`;
    return;
  }
  const s = State.sd.snap = snap.value;
  $("#sdRun").value = s.run.id;
  const m = s.metrics;
  $("#sdStatus").innerHTML = statusTag(s.analysis.status);
  const fact = (v) => `<span class="fact">${esc(v)}</span>`;
  $("#sdSub").innerHTML = [s.host, m.os_release.replace(/Red Hat Enterprise Linux( Server)? release/, "RHEL"), `kernel ${m.kernel}`, `uptime ${m.uptime_days} gün`,
    `${m.cpu_count} CPU`, `${fmtMb(m.memory.mem_total_mb)} RAM`].filter(Boolean).map(fact).join("") +
    (s.inventory?.tags || []).map((t) => `<span class="label">${esc(t)}</span>`).join("");
  renderVitals(s);
  renderServer();
}

function renderVitals(s) {
  const h = s.headline || {}, t = thresholds(), m = s.metrics;
  const v = (label, value, sub, cls = "") => `<div class="vital ${cls}"><div class="v-l">${label}</div><div class="v-v">${value}</div><div class="v-s">${esc(sub)}</div></div>`;
  const lvl = (x, w, c) => (x == null ? "" : x >= c ? "v-crit" : x >= w ? "v-warn" : "");
  const memLvl = h.mem_available_pct == null ? "" : h.mem_available_pct < t.mem_avail_warn_pct ? "v-crit" : h.mem_available_pct < t.mem_avail_warn_pct * 2 ? "v-warn" : "";
  $("#sdVitals").innerHTML = [
    v("En dolu disk", h.fs_max_pct != null ? `%${h.fs_max_pct}` : "—", h.fs_max_mount || "", lvl(h.fs_max_pct, t.fs_warn_pct, t.fs_crit_pct)),
    v("Boş bellek", h.mem_available_pct != null ? `%${h.mem_available_pct}` : "—", `swap %${h.swap_used_pct ?? 0}`, memLvl),
    v("Load / core", h.load_per_core ?? "—", `15 dk · ${m.cpu_count} CPU`, lvl(h.load_per_core, t.load_per_core_warn, t.load_per_core_crit)),
    v("I/O wait", h.cpu_iowait != null ? `%${h.cpu_iowait}` : "—", "vmstat ortalaması", lvl(h.cpu_iowait, t.iowait_warn_pct, 999)),
    v("Hatalı SSH", fmtNum(h.ssh_failures), "son 24 saat", lvl(h.ssh_failures, t.ssh_fail_warn, 1e9)),
    v("Güvenlik güncel.", h.security_updates ?? 0, m.reboot_required ? "reboot bekliyor" : "RHSA", h.security_updates ? "v-warn" : ""),
  ].join("");
  $("#sdVitals").classList.remove("hidden");
}

function renderServer() {
  const s = State.sd.snap, a = s.analysis, last = State.sd.hist[0];
  const counts = ["critical", "high", "medium", "low"].map((k) => [k, a.anomalies.filter((x) => x.severity === k).length]).filter(([, n]) => n);
  const p1 = a.action_items.filter((x) => x.priority === "P1").length;
  const tabs = [
    ["actions", `Aksiyonlar · ${a.action_items.length}`], ["anomalies", `Anomaliler · ${a.anomalies.length}`],
    ["rules", `Kural bulguları · ${s.findings.length}`], ["rems", `MorpheusAI · ${(State.sd.a2aCount || 0) + (State.sd.rems || []).length}`], ["metrics", "Metrikler"], ["trend", "Trend"], ["raw", "Ham loglar"], ["ai", "AI çağrısı"],
  ];
  $("#sdBody").innerHTML = `
    ${last && last.runId !== s.run.id && !State.sd.run && last.collect === "failed" ? `<div class="alert danger"><b>Son toplama başarısız (${esc(fmtTime(last.time))}):</b> ${esc(last.error)} — aşağıda son başarılı veri gösteriliyor.</div>` : ""}
    <div class="card">
      <div class="result-head">
        ${gauge(a.health_score, { size: 120, stroke: 10 })}
        <div>
          <div class="engine">${icon(a.engine === "genai" ? "sparkles" : "shield", "i i-xs")} ${esc(ENGINE_LABEL[a.engine] || a.engine)}${a.model ? ` · ${esc(a.model)}` : ""} · ${esc(fmtTime(s.collectedAt))} · ${esc(TRIGGER_LABEL[s.run.trigger] || "")}</div>
          <h2>${p1 ? `${p1} acil aksiyon` : a.action_items.length ? "Acil aksiyon yok" : "Sorun bulunmadı"}</h2>
          <p class="summary">${esc(a.summary)}</p>
          <div class="sev-summary">${counts.map(([k, n]) => `${sevTag(k)}<b class="mono">${n}</b>`).join(" ")}</div>
        </div>
      </div>
      ${a.aiError ? `<div class="alert danger"><b>GenAI analizi yapılamadı</b>, kural motoru sonucu gösteriliyor: ${esc(a.aiError)}</div>` : ""}
      ${s.partial ? `<div class="alert info">Toplama eksik tamamlandı; gelmeyen bölümler: ${esc((s.missing || []).join(", ") || "?")}</div>` : ""}
      <div class="tabs" id="sdTabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${State.sd.tab === k ? "on" : ""}">${esc(l)}</button>`).join("")}</div>
      <div id="sdPane"></div>
    </div>`;
  animateGauges($("#sdBody"));
  renderServerTab();
}

/** Komut listesini terminal bloğu olarak gösterir; '#' ile başlayan satırlar açıklamadır */
const termBlock = (cmds, i) => `<div class="fix"><pre class="term">${cmds.map((c) =>
  /^\s*#/.test(c) ? `<span class="cm">${esc(c)}</span>` : `<span class="ln">${esc(c)}</span>`).join("")}</pre><button class="btn link sm copy" data-copy-cmd="${i}">Kopyala</button></div>`;

function renderServerTab() {
  const s = State.sd.snap, a = s.analysis, pane = $("#sdPane");
  const t = State.sd.tab;
  if (t === "actions") {
    pane.innerHTML = a.action_items.map((x, i) => `
      <div class="action prio-${esc(x.priority)}">
        <div class="finding-top">${prioTag(x.priority)} <span class="finding-title">${esc(x.title)}</span>
          <span class="finding-meta"><span>${esc(x.owner)}</span>${x.timeframe && x.timeframe !== "-" ? `<span>${esc(x.timeframe)}</span>` : ""}${x.related_anomalies.length ? `<span>${esc(x.related_anomalies.join(", "))}</span>` : ""}</span></div>
        ${x.rationale && x.rationale !== "-" ? `<p>${esc(x.rationale)}</p>` : ""}
        ${x.commands.length ? termBlock(x.commands, i) : ""}
        <div class="action-foot">${remButton(i, x.title)}</div>
        <dl class="kv sm-kv">
          ${x.verification && x.verification !== "-" ? `<dt>Doğrulama</dt><dd>${esc(x.verification)}</dd>` : ""}
          ${x.risk && x.risk !== "-" ? `<dt>Risk</dt><dd>${esc(x.risk)}</dd>` : ""}
        </dl>
      </div>`).join("") || `<div class="empty"><div class="empty-ill">${icon("check")}</div><h4>Aksiyon yok</h4><p>Bu sunucuda anomali bulunmadı.</p></div>`;
    pane.querySelectorAll("[data-copy-cmd]").forEach((b) => (b.onclick = () => copyText(a.action_items[+b.dataset.copyCmd].commands.join("\n"))));
    pane.querySelectorAll("[data-send-rem]").forEach((b) => (b.onclick = () => sendRemediation(+b.dataset.sendRem, b)));
  } else if (t === "rems") {
    pane.innerHTML = `<div class="sub-h">Ajanlar arası konuşmalar (yalnızca bu sunucu)</div>
      <div class="a2a-layout"><div class="a2a-list" id="sdA2aList"></div><div class="a2a-detail" id="sdA2aDetail"></div></div>
      <div class="sub-h">MorpheusAI'ye gönderilen aksiyonlar</div>` + remsPane();
    loadA2A("#sdA2aList", "#sdA2aDetail", State.sd.name);
    pane.querySelectorAll("[data-rem-yaml]").forEach((b) => (b.onclick = () => showRemYaml(b.dataset.remYaml)));
    $("#remReload").onclick = async () => {
      try { State.sd.rems = (await Api.remediations(State.sd.name)).items; renderServer(); } catch (e) { toast(e.message, true); }
    };
  } else if (t === "anomalies") {
    pane.innerHTML = a.anomalies.map((x) => `
      <div class="finding ${esc(x.severity)}">
        <div class="finding-top">${sevTag(x.severity)} <span class="mono muted">${esc(x.id)}</span> <span class="finding-title">${esc(x.title)}</span>
          <span class="finding-meta"><span>${esc(x.category)}</span></span></div>
        ${x.evidence ? `<pre class="code-block mt-s">${esc(x.evidence)}</pre>` : ""}
        ${x.probable_cause && x.probable_cause !== "-" ? `<p><b>Olası neden:</b> ${esc(x.probable_cause)}</p>` : ""}
      </div>`).join("") || `<div class="empty"><div class="empty-ill">${icon("check")}</div><h4>Anomali yok</h4></div>`;
  } else if (t === "rules") {
    pane.innerHTML = `<p class="muted sm">Deterministik eşik kontrolleri (Ayarlar → Kural motoru eşikleri). GenAI'a kanıt olarak gönderilir.</p>
      <div class="table-wrap"><table class="table"><tr><th>Önem</th><th>Kategori</th><th>Bulgu</th><th>Kanıt</th></tr>${
      s.findings.map((f) => `<tr><td>${sevTag(f.severity)}</td><td>${esc(f.category)}</td><td>${esc(f.title)}</td>
        <td class="mono t-sub ev-cell">${esc(typeof f.evidence === "string" ? f.evidence : JSON.stringify(f.evidence))}</td></tr>`).join("") ||
      `<tr><td colspan="4" class="muted">Eşik aşımı yok.</td></tr>`}</table></div>`;
  } else if (t === "metrics") pane.innerHTML = metricsHtml(s);
  else if (t === "trend") renderTrend(pane);
  else if (t === "raw") renderRaw(pane);
  else if (t === "ai") {
    pane.innerHTML = `<dl class="kv">
        <dt>Motor</dt><dd>${esc(ENGINE_LABEL[a.engine] || a.engine)}</dd>
        <dt>Model</dt><dd>${esc(a.model || "—")}</dd>
        <dt>Toplama</dt><dd>${esc(fmtTime(s.collectedAt))} · ${fmtDur(s.durationMs)}</dd>
        <dt>Önceki toplama</dt><dd>${s.previous ? esc(fmtTime(s.previous.collectedAt)) : "—"}</dd>
      </dl>
      ${a.logId ? `<div class="toolbar mt"><button class="btn" id="sdOpenLog">${icon("file")}GPT'ye gönderilen istek ve cevabı göster</button></div>` :
        `<p class="muted">Bu analiz GenAI kullanılmadan (kural motoru) üretildi.</p>`}`;
    if (a.logId) $("#sdOpenLog").onclick = () => showLog(a.logId);
  }
}

function metricsHtml(s) {
  const m = s.metrics, th = State.settings?.thresholds || {};
  const bar = (pct) => `<div class="scorebar"><div class="bar"><i class="${pct >= (th.fs_crit_pct || 95) ? "b-crit" : pct >= (th.fs_warn_pct || 85) ? "b-warn" : "b-ok"}" style="width:${pct}%"></i></div><b>${pct}</b></div>`;
  const row = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
  const lg = m.load || {}, c = m.cpu || {}, mem = m.memory, ts = m.time_sync;
  return `<div class="grid-2">
    <div><h4 class="sub-h">Dosya sistemleri</h4><div class="table-wrap"><table class="table compact">
      <tr><th>Mount</th><th>FS</th><th class="num">Boyut</th><th>Doluluk</th><th class="num">Büyüme/gün</th><th class="num">Dolmaya</th></tr>
      ${m.filesystems.map((f) => `<tr><td class="mono">${esc(f.mount)}</td><td>${esc(f.fstype)}</td><td class="num">${fmtKb(f.size_kb)}</td><td>${bar(f.used_pct)}</td>
        <td class="num">${f.growth_kb_per_day ? fmtKb(f.growth_kb_per_day) : "—"}</td><td class="num">${f.days_to_full != null ? `${f.days_to_full} gün` : "—"}</td></tr>`).join("")}
    </table></div>
    ${m.inodes.length ? `<h4 class="sub-h">Inode</h4><div class="table-wrap"><table class="table compact"><tr><th>Mount</th><th>Kullanım</th></tr>
      ${m.inodes.map((f) => `<tr><td class="mono">${esc(f.mount)}</td><td>${bar(f.used_pct)}</td></tr>`).join("")}</table></div>` : ""}
    <h4 class="sub-h">En çok CPU kullanan prosesler</h4><div class="table-wrap"><table class="table compact">
      <tr><th>PID</th><th>Kullanıcı</th><th class="num">%CPU</th><th class="num">%MEM</th><th>Komut</th></tr>
      ${m.top_processes.map((p) => `<tr><td class="mono">${esc(p[0])}</td><td>${esc(p[1])}</td><td class="num">${esc(p[2])}</td><td class="num">${esc(p[3])}</td><td class="mono">${esc(p[5])}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">—</td></tr>`}
    </table></div></div>
    <div><h4 class="sub-h">Sistem</h4><dl class="kv">
      ${row("Load (1/5/15)", `${lg["1m"] ?? "—"} / ${lg["5m"] ?? "—"} / ${lg["15m"] ?? "—"} <span class="muted sm">(${m.cpu_count} CPU)</span>`)}
      ${row("CPU (vmstat ort.)", `user %${c.cpu_user ?? "—"} · sys %${c.cpu_sys ?? "—"} · iowait %${c.cpu_iowait ?? "—"} · idle %${c.cpu_idle ?? "—"}`)}
      ${row("Bellek", `${fmtMb(mem.mem_total_mb)} · kullanılabilir %${mem.mem_available_pct ?? "—"}`)}
      ${row("Swap", mem.swap_total_mb ? `${fmtMb(mem.swap_total_mb)} · kullanılan %${mem.swap_used_pct} · si/so ${c.swap_in ?? 0}/${c.swap_out ?? 0}` : "yok")}
      ${row("Zaman senkronu", ts.synced ? `<span class="status-ok">senkron</span> · kayma ${ts.offset_sec ?? "—"} sn` : `<span class="status-err">senkron değil</span>${ts.offset_sec != null ? ` · kayma ${ts.offset_sec} sn` : ""}`)}
      ${row("SELinux", m.selinux === "Enforcing" ? `<span class="status-ok">Enforcing</span>` : `<span class="status-warn">${esc(m.selinux)}</span>`)}
      ${row("AVC denial (dün)", m.avc_denials)}
      ${row("Güvenlik güncellemesi", m.security_updates ? `<span class="status-warn">${m.security_updates} bekliyor</span>` : "0")}
      ${row("Reboot gerekli", m.reboot_required ? `<span class="status-warn">evet</span>` : "hayır")}
      ${row("OOM kill / disk hatası", `${m.oom_kills} / ${m.disk_errors}`)}
      ${row("Zombie", m.zombies)}
      ${row("Failed unit", m.failed_units.length ? m.failed_units.map((u) => `<code>${esc(u)}</code>`).join(" ") : "yok")}
      ${row("Başarısız SSH (24 sa)", `${m.ssh_failures.count}${m.ssh_failures.top_sources.length ? ` · ${m.ssh_failures.top_sources.map(([ip, n]) => `<code>${esc(ip)}</code> ${n}`).join(", ")}` : ""}`)}
    </dl>
    <h4 class="sub-h">Journal hataları (gruplanmış, 24 sa)</h4>${logGroups(m.journal_errors)}
    <h4 class="sub-h">Kernel uyarıları (gruplanmış)</h4>${logGroups(m.kernel_messages)}
    </div></div>`;
}

const logGroups = (g) => g.length
  ? `<div class="table-wrap"><table class="table compact"><tr><th class="num">Adet</th><th>Örnek satır</th></tr>
      ${g.map((x) => `<tr><td class="num">${x.count}</td><td class="mono t-sub ev-cell">${esc(x.example)}</td></tr>`).join("")}</table></div>`
  : `<p class="muted sm">Kayıt yok.</p>`;

/* ---- Trend: her ölçü kendi grafiğinde (tek eksen), crosshair + tooltip ---- */
const TREND_SERIES = [
  ["score", "Sağlık skoru", (h) => h.score, 100],
  ["fs", "En dolu disk %", (h) => h.headline?.fs_max_pct, 100],
  ["mem", "Kullanılabilir bellek %", (h) => h.headline?.mem_available_pct, 100],
  ["load", "Load / core (15 dk)", (h) => h.headline?.load_per_core, null],
  ["ssh", "Başarısız SSH girişi", (h) => h.headline?.ssh_failures, null],
  ["jerr", "Journal hata satırı", (h) => h.headline?.journal_errors, null],
];

function renderTrend(pane) {
  const pts = State.sd.hist.filter((h) => h.collect !== "failed" && h.headline).slice().reverse();
  if (pts.length < 2) { pane.innerHTML = `<div class="empty"><h4>Trend için en az iki toplama gerekli</h4></div>`; return; }
  pane.innerHTML = `<div class="trend-grid">${TREND_SERIES.map(([k, label]) => `<div class="trend"><h4 class="sub-h">${esc(label)}</h4><div class="chart line" data-series="${k}"></div></div>`).join("")}</div>`;
  for (const [k, label, get, fixedMax] of TREND_SERIES)
    lineChart(pane.querySelector(`[data-series="${k}"]`), pts.map((h) => ({ t: h.time, v: get(h) ?? null })), { label, fixedMax });
}

function lineChart(el, data, { label, fixedMax }) {
  const W = 520, H = 180, L = 40, R = 12, T = 12, B = 26;
  const vals = data.map((d) => d.v).filter((v) => v != null);
  const max = fixedMax || Math.max(1, ...vals) * 1.15;
  const x = (i) => L + ((W - L - R) * i) / Math.max(1, data.length - 1);
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, max / 2, max].map((v) => (max >= 10 ? Math.round(v) : Math.round(v * 10) / 10));
  const path = data.map((d, i) => (d.v == null ? null : `${x(i)},${y(d.v)}`)).filter(Boolean);
  const labelEvery = Math.ceil(data.length / 6);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
    <g class="grid">${ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`).join("")}</g>
    <g class="axis">${ticks.map((v) => `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${fmtK(v)}</text>`).join("")}
      ${data.map((d, i) => (i % labelEvery === 0 || i === data.length - 1 ? `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${esc(new Date(d.t).toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit" }))}</text>` : "")).join("")}</g>
    ${path.length === data.length ? `<path class="area" d="M${x(0)},${y(0)} L${path.join(" L")} L${x(data.length - 1)},${y(0)} Z"/>` : ""}
    <polyline class="line" points="${path.join(" ")}"/>
    ${data.map((d, i) => (d.v == null ? "" : `<circle class="dot" cx="${x(i)}" cy="${y(d.v)}" r="4"/>`)).join("")}
    <line class="xhair hidden" y1="${T}" y2="${H - B}"/>
    <rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/>
  </svg><div class="tip hidden"></div>`;
  const svg = $("svg", el), tip = $(".tip", el), xh = $(".xhair", el);
  svg.onmousemove = (e) => {
    const box = svg.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(data.length - 1, Math.round(((px - L) / (W - L - R)) * (data.length - 1))));
    const d = data[i];
    xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.classList.remove("hidden");
    tip.innerHTML = `<b>${esc(fmtTime(d.t))}</b><br>${esc(label)}: ${d.v ?? "—"}`;
    tip.style.left = `${(x(i) / W) * box.width}px`;
    tip.style.top = `${((d.v == null ? T : y(d.v)) / H) * box.height}px`;
    tip.classList.remove("hidden");
  };
  svg.onmouseleave = () => { tip.classList.add("hidden"); xh.classList.add("hidden"); };
}

async function renderRaw(pane, section) {
  pane.innerHTML = loadingView("Ham çıktılar yükleniyor…");
  try {
    const r = await Api.raw(State.sd.name, State.sd.snap.run.id);
    const keys = Object.keys(r.sections);
    const cur = section && keys.includes(section) ? section : keys.includes("journal_errors") ? "journal_errors" : keys[0];
    pane.innerHTML = `<div class="toolbar">
        <select class="input sm" id="rawSel">${keys.map((k) => `<option value="${esc(k)}" ${k === cur ? "selected" : ""}>${esc(SECTION_LABEL[k] || k)} (${r.sections[k] ? r.sections[k].split("\n").length : 0} satır)</option>`).join("")}</select>
        <button class="btn link sm" id="rawDl">Tümünü indir</button>
        <span class="muted sm">Hedefte çalıştırılan komutlar salt-okunurdur; çıktı olduğu gibi saklanır.</span></div>
      <pre class="code-block raw">${esc(r.sections[cur] || "(boş)")}</pre>
      ${r.stderr ? `<h4 class="sub-h">stderr</h4><pre class="code-block">${esc(r.stderr)}</pre>` : ""}`;
    $("#rawSel").onchange = (e) => renderRaw(pane, e.target.value);
    $("#rawDl").onclick = () => download(`${State.sd.name}-${r.run.id}.txt`,
      keys.map((k) => `===UNIXAI:${k}===\n${r.sections[k]}`).join("\n"), "text/plain;charset=utf-8");
  } catch (e) { pane.innerHTML = errorView(e); }
}

/* ================================================================== */
/*  Toplama & analiz                                                   */
/* ================================================================== */
async function loadRuns() {
  try {
    const [runs, inv] = await Promise.all([Api.runs(), Api.servers()]);
    State.runs = runs;
    State.inventory = inv.servers;
    renderRunServers();
    renderRuns();
    const running = runs.find((r) => r.status === "running");
    if (running) pollRun(running.id);
  } catch (e) { $("#runTable").innerHTML = `<tr><td>${errorView(e)}</td></tr>`; }
}

function renderRunServers() {
  const inv = State.inventory.filter((s) => s.enabled);
  $("#runServers").innerHTML = inv.length
    ? `<span class="muted sm">Sunucular (seçilmezse tüm etkinler):</span> ` +
      inv.map((s) => `<button type="button" class="chip-rule ${State.runSelect.has(s.name) ? "on" : ""}" data-pick="${esc(s.name)}">${esc(s.name)}</button>`).join("")
    : `<span class="muted sm">Envanterde etkin sunucu yok — <a href="#/servers">Sunucular</a> ekranından ekleyin.</span>`;
}

function renderRuns() {
  const sc = State.health?.scheduler;
  $("#schedInfo").textContent = sc ? (sc.enabled ? `Zamanlayıcı: her gün ${sc.time}${sc.lastRunDate ? ` · son ${sc.lastRunDate}` : ""}` : "Zamanlayıcı kapalı") : "";
  $("#runTable").innerHTML = `<tr><th>Başlangıç</th><th>Tür</th><th>Kullanıcı</th><th>Sunucular</th><th>Analiz</th><th>Durum</th><th class="num">Süre</th></tr>` +
    (State.runs.map((r) => {
      const ok = r.servers.filter((s) => s.collect === "ok" || s.collect === "partial").length;
      const [st, cls] = RUN_STATUS[r.status] || [r.status, ""];
      return `<tr class="clickable" data-run="${esc(r.id)}"><td class="mono">${esc(fmtTime(r.startedAt))}</td>
        <td>${esc(TRIGGER_LABEL[r.trigger] || r.trigger)}</td><td>${esc(r.user)}</td>
        <td>${ok} / ${r.servers.length}${r.servers.length - ok && r.status !== "running" ? ` <span class="status-err sm">${r.servers.length - ok} hata</span>` : ""}</td>
        <td>${r.ai ? "GenAI" : "Kural motoru"}</td><td><span class="label ${cls}">${esc(st)}</span></td>
        <td class="num">${r.finishedAt ? fmtDur(Date.parse(r.finishedAt) - Date.parse(r.startedAt)) : "—"}</td></tr>`;
    }).join("") || `<tr><td colspan="7" class="muted">Henüz çalıştırma yok.</td></tr>`);
}

async function startRun(mode, btn) {
  const body = { mode, ai: $("#runAi").checked };
  if (mode === "collect" && State.runSelect.size) body.servers = [...State.runSelect];
  if (btn) btn.disabled = true;
  try {
    const r = await Api.startRun(body);
    toast(mode === "demo" ? "Demo veri üretiliyor…" : mode === "reanalyze" ? "Yeniden analiz başladı" : "Toplama başladı");
    if (!location.hash.startsWith("#/runs") && !location.hash.startsWith("#/server/")) location.hash = "#/runs";
    pollRun(r.id);
  } catch (e) { toast(e.message, true); }
  finally { if (btn) btn.disabled = false; }
}

const SERVER_STEP = (s) =>
  s.collect === "pending" ? ["Bekliyor", ""] : s.collect === "failed" ? ["Ulaşılamadı", "red"]
  : s.analysis === "running" ? ["Analiz ediliyor", "blue"] : s.analysis === "pending" ? ["Toplandı", "blue"] : ["Tamam", "green"];

function renderActiveRun(r) {
  const el = $("#runActive");
  if (!r || r.status !== "running") { el.innerHTML = ""; return; }
  const done = r.servers.filter((s) => s.collect === "failed" || !["pending", "running"].includes(s.analysis)).length;
  el.innerHTML = `<div class="active-run">
    <div class="card-head"><h3><span class="pulse"></span>Sürüyor: ${esc(TRIGGER_LABEL[r.trigger] || r.trigger)} <span class="muted sm">${esc(r.id)}</span></h3><span class="muted sm">${done} / ${r.servers.length}</span></div>
    <div class="progress"><i style="width:${(done / r.servers.length) * 100}%"></i></div>
    <div class="run-servers">${r.servers.map((s) => { const [l, c] = SERVER_STEP(s); return `<span class="label ${c}" title="${esc(s.error || "")}">${esc(s.name)} · ${l}</span>`; }).join(" ")}</div></div>`;
}

function pollRun(id) {
  clearTimeout(State.runTimer);
  State.activeRun = id;
  const tick = async () => {
    try {
      const r = await Api.run(id);
      renderActiveRun(r);
      setRunChip(r);
      if (r.status === "running") { State.runTimer = setTimeout(tick, 1500); return; }
      State.activeRun = null;
      const failed = r.servers.filter((s) => s.collect === "failed").length;
      toast(`Çalıştırma bitti: ${r.servers.length - failed} / ${r.servers.length} sunucu${failed ? ` · ${failed} ulaşılamadı` : ""}`, failed === r.servers.length);
      if (location.hash.startsWith("#/runs")) loadRuns();
      if (location.hash.startsWith("#/server/") && State.sd.name) loadServer(State.sd.name);
      if (location.hash.startsWith("#/dashboard") || !location.hash) loadOverview();
      renderChatContext();
    } catch { State.runTimer = setTimeout(tick, 4000); }
  };
  tick();
}

function setRunChip(r) {
  const chip = $("#runChip");
  if (r?.status === "running") {
    const done = r.servers.filter((s) => s.collect === "failed" || !["pending", "running"].includes(s.analysis)).length;
    $("span", chip).textContent = `Toplama: ${done}/${r.servers.length}`;
    chip.classList.add("busy");
  } else {
    chip.classList.remove("busy");
    const lr = r || State.overview?.lastRun;
    $("span", chip).textContent = lr ? `Toplama: ${ago(lr.finishedAt || lr.startedAt)}` : "Toplama: —";
  }
}

async function showRun(id) {
  try {
    const r = await Api.run(id);
    const [st, cls] = RUN_STATUS[r.status] || [r.status, ""];
    modal(`Çalıştırma · ${TRIGGER_LABEL[r.trigger] || r.trigger}`, `
      <dl class="kv">
        <dt>Başlangıç / bitiş</dt><dd>${esc(fmtTime(r.startedAt))} → ${r.finishedAt ? esc(fmtTime(r.finishedAt)) : "sürüyor"}</dd>
        <dt>Başlatan</dt><dd>${esc(r.user)}</dd>
        <dt>Analiz</dt><dd>${r.ai ? "GenAI" : "Kural motoru"}</dd>
        <dt>Durum</dt><dd><span class="label ${cls}">${esc(st)}</span>${r.error ? ` <span class="status-err">${esc(r.error)}</span>` : ""}</dd>
      </dl>
      <div class="table-wrap mt"><table class="table compact"><tr><th>Sunucu</th><th>Toplama</th><th class="num">Süre</th><th>Analiz</th><th>Sonuç</th><th>Hata</th></tr>
      ${r.servers.map((s) => `<tr class="clickable" data-href="#/server/${encodeURIComponent(s.name)}">
        <td class="t-name">${esc(s.name)}<div class="t-sub">${esc(s.host || "")}</div></td>
        <td>${s.collect === "failed" ? `<span class="status-err">başarısız</span>` : esc(s.collect)}</td><td class="num">${fmtDur(s.durationMs)}</td>
        <td>${esc(ENGINE_LABEL[s.analysis] || s.analysis)}</td>
        <td>${s.status ? `${statusTag(s.status)} <b>${s.score}</b>` : "—"}</td>
        <td class="t-sub ev-cell">${esc(s.error || s.aiError || "")}</td></tr>`).join("")}</table></div>
      ${r.fleet ? `<h4 class="sub-h">Filo özeti</h4><p>${esc(r.fleet.fleet_summary)}</p>` : ""}`, { wide: true });
  } catch (e) { toast(e.message, true); }
}

/* ================================================================== */
/*  Gelen loglar                                                       */
/* ================================================================== */
async function loadCollected() {
  try {
    const c = await Api.collected();
    State.collected = c;
    $("#colTable").innerHTML = `<tr><th>Sunucu</th><th>Toplama</th>${c.sections.map((k) => `<th class="rot"><span>${esc(SECTION_LABEL[k] || k)}</span></th>`).join("")}</tr>` +
      (c.servers.sort((a, b) => a.name.localeCompare(b.name)).map((s) => `<tr>
        <td><a class="t-name" href="#/server/${encodeURIComponent(s.name)}">${esc(s.name)}</a><div class="t-sub">${esc(s.host || "")}</div></td>
        <td class="t-sub">${esc(fmtTime(s.collectedAt))}${s.collect === "failed" ? `<br><span class="status-err" title="${esc(s.error)}">ulaşılamadı</span>` : ""}</td>
        ${c.sections.map((k) => {
          const n = s.sectionLines[k];
          return n == null ? `<td class="cell miss" title="Gelmedi">✕</td>`
            : `<td class="cell ${n ? "has" : "empty"}" data-raw="${esc(s.name)}" data-run="${esc(s.runId)}" data-sec="${esc(k)}" title="${esc(SECTION_LABEL[k] || k)}: ${n} satır">${n}</td>`;
        }).join("")}</tr>`).join("") ||
      `<tr><td colspan="${c.sections.length + 2}" class="muted">Henüz toplama yapılmadı.</td></tr>`);
  } catch (e) { $("#colTable").innerHTML = `<tr><td>${errorView(e)}</td></tr>`; }
}

async function showRawSection(name, run, sec) {
  try {
    const r = await Api.raw(name, run);
    modal(`${name} · ${SECTION_LABEL[sec] || sec}`, `<p class="muted sm">${esc(fmtTime(r.collectedAt))} · çalıştırma ${esc(run)}</p>
      <pre class="code-block raw">${esc(r.sections[sec] || "(boş — komut çıktı üretmedi)")}</pre>`, { wide: true });
  } catch (e) { toast(e.message, true); }
}

/* ================================================================== */
/*  UnixAI'ye sor                                                      */
/* ================================================================== */
const SUGGESTIONS = [
  "Bugün ilk neye bakmalıyım?",
  "Disk doluluğu riskli sunucular hangileri, ne zaman dolacak?",
  "En kritik sorunun kök nedeni ne olabilir?",
  "Güvenlik açısından acil yapılması gerekenler neler?",
  "En kritik sunucunun P1 bulgusunu canlı tanıyla doğrula ve MorpheusAI ajanından düzeltme playbook'u iste",
];

function renderChatContext(preselect) {
  const sel = $("#chatCtx"), cur = preselect ? `s:${preselect}` : sel.value;
  const names = (State.overview?.servers || []).filter((s) => s.latest && s.latest.collect !== "failed").map((s) => s.name);
  sel.innerHTML = `<option value="">Bağlam yok</option><option value="fleet">Filo (tüm sunucular)</option>` +
    names.map((n) => `<option value="s:${esc(n)}">${esc(n)}</option>`).join("");
  sel.value = [...sel.options].some((o) => o.value === cur) ? cur : names.length ? "fleet" : "";
}

function renderChat() {
  $("#chatLog").innerHTML = State.chat.map((m) => `<div class="msg ${m.role}">
      <div class="avatar">${m.role === "user" ? "Siz" : "UA"}</div>
      <div class="bubble ${m.error ? "error" : ""}">${m.role === "user" ? esc(m.content).replace(/\n/g, "<br>") : md(m.content) + traceHtml(m.trace) + (m.rems?.length ? `<div class="sub-h">MorpheusAI'nin yazdığı playbook${m.rems.length > 1 ? "'lar" : ""}</div>${m.rems.map(remCard).join("")}` : "")}</div></div>`).join("") +
    (State.chatBusy ? `<div class="msg assistant"><div class="avatar">UA</div><div class="bubble typing"><span></span><span></span><span></span></div></div>` : "");
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
  $("#chatSuggest").classList.toggle("hidden", State.chat.length > 1);
  hydrateRem($("#chatLog"));
}

async function sendChat(text) {
  text = String(text || "").trim();
  if (!text || State.chatBusy) return;
  State.chat.push({ role: "user", content: text });
  State.chatBusy = true;
  renderChat();
  const v = $("#chatCtx").value;
  const context = v === "fleet" ? { fleet: true } : v.startsWith("s:") ? { server: v.slice(2) } : null;
  try {
    const r = await Api.chat({ messages: State.chat.filter((m) => !m.error && !m.intro).map(({ role, content }) => ({ role, content })), context });
    const rems = [...new Map((r.remediations || []).map((x) => [x.id, x])).values()];
    State.chat.push({ role: "assistant", content: r.reply, trace: r.trace, mode: r.mode, rems });
    if (rems.length) refreshApprovals();
  } catch (e) {
    State.chat.push({ role: "assistant", content: e.message, error: true });
  } finally {
    State.chatBusy = false;
    renderChat();
    refreshTokenChip();
  }
}

function resetChat() {
  State.chat = [{ role: "assistant", intro: true, content: "Merhaba, ben **UnixAI ajanı**. Sunucu verisine araçlarımla bakar, gerekirse canlı salt-okunur tanı çalıştırır ve bağlı ajanlarla (ör. MorpheusAI) konuşurum. Ne yapalım?" }];
  renderChat();
}

/* ================================================================== */
/*  Sunucular (envanter)                                               */
/* ================================================================== */
async function loadInventory() {
  try {
    const r = await Api.servers();
    State.inventory = r.servers;
    State.sshDefaults = r.defaults;
    renderInventory();
    renderSshKey(r.sshKey);
  } catch (e) { $("#invTable").innerHTML = `<tr><td>${errorView(e)}</td></tr>`; }
}

function renderInventory() {
  const d = State.sshDefaults || {};
  const latest = new Map((State.overview?.servers || []).map((s) => [s.name, s.latest]));
  $("#invTable").innerHTML = `<tr><th>Ad</th><th>Adres</th><th>Kullanıcı</th><th class="num">Port</th><th>Etiketler</th><th>Son durum</th><th></th></tr>` +
    (State.inventory.map((s) => {
      const l = latest.get(s.name);
      return `<tr class="${s.enabled ? "" : "row-off"}"><td class="t-name">${esc(s.name)}${s.enabled ? "" : ` <span class="label">pasif</span>`}</td>
        <td class="mono">${esc(s.host)}</td><td>${esc(s.user || d.user)}${s.user ? "" : ` <span class="muted sm">(varsayılan)</span>`}</td>
        <td class="num">${s.port || d.port}</td><td>${s.tags.map((t) => `<span class="label">${esc(t)}</span>`).join(" ")}</td>
        <td>${!l ? `<span class="muted sm">—</span>` : l.collect === "failed" ? `<span class="status-err sm" title="${esc(l.error)}">ulaşılamadı</span>` : `${statusTag(l.status)} <span class="t-sub">${esc(ago(l.collectedAt))}</span>`}</td>
        <td class="row-actions"><button class="btn link sm" data-test="${esc(s.name)}">SSH testi</button>
          <button class="btn link sm" data-edit="${esc(s.name)}">Düzenle</button><button class="btn link sm danger" data-del="${esc(s.name)}">Sil</button></td></tr>`;
    }).join("") || `<tr><td colspan="7" class="muted">Henüz sunucu yok. <b>Sunucu ekle</b> veya <b>Toplu ekle</b> ile başlayın.</td></tr>`);
}

function serverModal(name) {
  const s = State.inventory.find((x) => x.name === name) || { enabled: true, tags: [] };
  const d = State.sshDefaults || {};
  modal(name ? `Sunucuyu düzenle · ${name}` : "Sunucu ekle", `
    <form id="srvForm" class="form">
      <div class="grid-2">
        <label>Ad<input class="input" name="name" required value="${esc(s.name || "")}" placeholder="rhel-db01" /></label>
        <label>Adres (ad veya IP)<input class="input mono" name="host" required value="${esc(s.host || "")}" placeholder="10.20.30.11" /></label>
        <label>SSH kullanıcısı<input class="input" name="user" value="${esc(s.user || "")}" placeholder="${esc(d.user)} (varsayılan)" /></label>
        <label>Port<input class="input" name="port" type="number" value="${esc(s.port || "")}" placeholder="${esc(d.port)}" /></label>
      </div>
      <label>Anahtar dosyası<input class="input mono" name="keyFile" value="${esc(s.keyFile || "")}" placeholder="${esc(d.keyFile)} (varsayılan)" /></label>
      <label>Etiketler<input class="input" name="tags" value="${esc(s.tags.join(", "))}" placeholder="prod, oracle" /><small>Virgülle ayırın.</small></label>
      <label>Not<input class="input" name="notes" value="${esc(s.notes || "")}" /></label>
      <label class="check"><input type="checkbox" name="enabled" ${s.enabled ? "checked" : ""} /> Günlük toplamaya dahil</label>
      <div id="srvTestOut"></div>
      <div class="toolbar end"><button type="button" class="btn secondary" id="srvTest">Kaydetmeden SSH testi</button><button class="btn primary" type="submit">Kaydet</button></div>
    </form>`);
  const read = () => {
    const f = $("#srvForm").elements;
    return { name: f.name.value, host: f.host.value, user: f.user.value, port: f.port.value, keyFile: f.keyFile.value, tags: f.tags.value, notes: f.notes.value, enabled: f.enabled.checked };
  };
  $("#srvForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await Api.saveServer(read(), name); closeModal(); toast("Sunucu kaydedildi"); loadInventory(); loadOverview(); }
    catch (err) { toast(err.message, true); }
  };
  $("#srvTest").onclick = () => runSshTest(read().name || "_", read(), $("#srvTestOut"));
}

async function runSshTest(name, server, out) {
  out.innerHTML = loadingView(`${server?.host || name} için SSH testi…`);
  try {
    const r = await Api.testServer(name, server);
    out.innerHTML = r.ok
      ? `<div class="alert info"><b class="status-ok">✓ Bağlantı başarılı</b> (${fmtDur(r.durationMs)})<pre class="code-block mt-s">${esc(r.output)}</pre></div>`
      : `<div class="alert danger"><b>✕ Bağlanılamadı:</b> ${esc(r.error)}</div>`;
  } catch (e) { out.innerHTML = errorView(e); }
}

function importModal() {
  modal("Toplu sunucu ekle", `
    <form id="impForm" class="form">
      <label>Her satıra bir sunucu
        <textarea class="input mono" name="text" rows="10" spellcheck="false" placeholder="# ad  adres  [kullanıcı]  [port]
rhel-db01   10.20.30.11
rhel-web01  10.20.30.21  root  22
rhel-app02.corp.local"></textarea>
        <small>Yalnızca adres yazılırsa ad, adresin ilk bölümünden alınır. Var olan adlar güncellenir.</small>
      </label>
      <div class="toolbar end"><button class="btn primary" type="submit">Ekle</button></div>
    </form>`);
  $("#impForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await Api.importServers(e.target.elements.text.value);
      closeModal();
      toast(`${r.added.length} sunucu eklendi${r.errors.length ? ` · ${r.errors.length} hata` : ""}`, Boolean(r.errors.length));
      if (r.errors.length) modal("İçe aktarma hataları", `<pre class="code-block">${esc(r.errors.join("\n"))}</pre>`);
      loadInventory();
    } catch (err) { toast(err.message, true); }
  };
}

function renderSshKey(k) {
  $("#keyInfo").textContent = k.exists ? k.keyFile : "anahtar bulunamadı";
  const setup = k.publicKey
    ? `# 1) Her hedef sunucuda (root) — merkez sunucunun IP'sini yazın:
mkdir -p /root/.ssh && chmod 700 /root/.ssh
echo 'from="<merkez_ip>",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ${k.publicKey}' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# veya projedeki betikle:
${k.targetSetup}

# 2) Merkez sunucuda host key'leri kaydedin:
ssh-keyscan -H <hedef_adres> >> /root/.ssh/known_hosts`
    : "";
  $("#keyBody").innerHTML = k.publicKey
    ? `<p class="muted sm">Toplama <b>${esc(k.user)}</b> kullanıcısıyla bu anahtarla yapılır. Public key'i hedeflerde <code>authorized_keys</code> dosyasına ekleyin. <code>from=</code> kısıtı anahtarın yalnızca merkezden kullanılmasını sağlar.</p>
       <div class="fix"><pre>${esc(k.publicKey)}</pre><button class="btn link sm copy" id="copyPub">Kopyala</button></div>
       <h4 class="sub-h">Hedef sunucu kurulumu</h4><div class="fix"><pre>${esc(setup)}</pre><button class="btn link sm copy" id="copySetup">Kopyala</button></div>`
    : `<div class="alert info">SSH anahtarı yok. Merkez sunucuda root olarak oluşturun (install.sh bunu otomatik yapar):</div>
       <div class="fix"><pre>${esc(k.generate)}</pre></div>`;
  if (k.publicKey) {
    $("#copyPub").onclick = () => copyText(k.publicKey);
    $("#copySetup").onclick = () => copyText(setup);
  }
}

/* ================================================================== */
/*  AI Logları                                                         */
/* ================================================================== */
async function loadLogs() {
  try {
    const rows = await Api.logs();
    $("#logTable").innerHTML =
      `<tr><th>Zaman</th><th>Tür</th><th>Sunucu</th><th>Uç nokta</th><th>Durum</th><th class="num">Süre</th><th class="num">Token</th></tr>` +
      (rows.map((r) => `<tr class="clickable" data-log="${esc(r.id)}">
        <td class="mono">${esc(fmtTime(r.time))}</td>
        <td><span class="label ${r.kind === "tms-token" ? "gold" : "blue"}">${esc(KIND_LABEL[r.kind] || r.kind)}</span></td>
        <td>${esc(r.meta?.server || "")}</td>
        <td class="t-sub mono">${esc(r.method)} ${esc(r.host)}${esc(r.path)}</td>
        <td class="${r.ok ? "status-ok" : "status-err"}">${r.status ?? "HATA"}</td>
        <td class="num mono">${r.durationMs} ms</td>
        <td class="num mono">${r.meta?.usage?.total_tokens ?? ""}</td></tr>`).join("") ||
        `<tr><td colspan="7" class="muted">Henüz kayıt yok. GenAI bağlantısını test edin veya bir analiz başlatın.</td></tr>`);
  } catch (e) { $("#logTable").innerHTML = `<tr><td>${errorView(e)}</td></tr>`; }
}

function setLogAuto(on) {
  clearInterval(State.logTimer);
  State.logTimer = on ? setInterval(loadLogs, 5000) : null;
}

function messagesHtml(body) {
  const msgs = body?.messages;
  if (!Array.isArray(msgs)) return "";
  return msgs.map((m) => `<div class="finding"><div class="finding-top"><span class="label">${esc(m.role)}</span></div>
    <pre class="code-block mt-s">${esc(m.content)}</pre></div>`).join("");
}

async function showLog(id) {
  try {
    const e = await Api.log(id);
    const reply = e.response?.body?.choices?.[0]?.message?.content;
    const pretty = (v) => esc(typeof v === "string" ? v : JSON.stringify(v, null, 2));
    modal(`GenAI · ${KIND_LABEL[e.kind] || e.kind}`, `<div class="log-detail">
      <dl class="kv">
        <dt>Zaman</dt><dd>${esc(fmtTime(e.time))}</dd>
        <dt>İstek</dt><dd class="mono">${esc(e.method)} ${esc(e.host)}${esc(e.path)}</dd>
        <dt>Durum</dt><dd class="${e.ok ? "status-ok" : "status-err"}">${e.status ?? "—"} · ${e.durationMs} ms</dd>
        ${e.meta?.server ? `<dt>Sunucu</dt><dd>${esc(e.meta.server)}</dd>` : ""}
        ${e.meta?.usage ? `<dt>Token kullanımı</dt><dd class="mono">${pretty(e.meta.usage)}</dd>` : ""}
        ${e.meta?.finishReason ? `<dt>finish_reason</dt><dd>${esc(e.meta.finishReason)}</dd>` : ""}
        ${e.error ? `<dt>Hata</dt><dd class="status-err">${esc(e.error)}</dd>` : ""}
      </dl>
      <div class="tabs mt">
        <button class="on" data-ltab="req">Gönderilen</button>
        <button data-ltab="res">Dönen</button>
        <button data-ltab="hdr">Header'lar</button>
      </div>
      <div data-lpane="req">${messagesHtml(e.request.body) || `<pre class="code-block">${pretty(e.request.body)}</pre>`}</div>
      <div data-lpane="res" class="hidden">
        ${reply != null ? `<h4>Model cevabı</h4><pre class="code-block">${esc(reply)}</pre><h4>Ham yanıt</h4>` : ""}
        <pre class="code-block">${pretty(e.response.body) || "(boş)"}</pre>
      </div>
      <div data-lpane="hdr" class="hidden">
        <h4>İstek header'ları</h4><pre class="code-block">${pretty(e.request.headers)}</pre>
        <h4>Yanıt header'ları</h4><pre class="code-block">${pretty(e.response.headers)}</pre>
      </div>
      <div class="toolbar end"><button class="btn link sm" id="logDownload">JSON indir</button></div>
    </div>`, { wide: true });
    $("#modalBody").onclick = (ev) => {
      const t = ev.target.closest("[data-ltab]");
      if (!t) return;
      $$("[data-ltab]").forEach((b) => b.classList.toggle("on", b === t));
      $$("[data-lpane]").forEach((p) => p.classList.toggle("hidden", p.dataset.lpane !== t.dataset.ltab));
    };
    $("#logDownload").onclick = () => download(`unixai-log-${e.id}.json`, JSON.stringify(e, null, 2));
  } catch (err) {
    toast(err.message, true);
  }
}

/* ================================================================== */
/*  Token kullanımı                                                    */
/* ================================================================== */
function setUsageRange(days) {
  const to = new Date(), from = new Date();
  from.setDate(to.getDate() - (days - 1));
  $("#usageFrom").value = isoDay(from);
  $("#usageTo").value = isoDay(to);
  $$("#usageRange button").forEach((b) => b.classList.toggle("on", Number(b.dataset.days) === days));
}

async function refreshTokenChip() {
  try {
    const t = await Api.usageToday();
    const chip = $("#tokenChip");
    const limit = t.dailyLimit;
    $("span", chip).textContent = limit ? `Token: ${fmtK(t.total)} / ${fmtK(limit)}` : `Token: ${fmtK(t.total)} bugün`;
    chip.classList.toggle("over", Boolean(limit && t.total >= limit));
    chip.title = `Bugün ${fmtNum(t.total)} token, ${t.requests} istek` + (limit ? ` · limit ${fmtNum(limit)}` : " · limit yok");
  } catch {}
}

async function loadUsage() {
  if (!$("#usageFrom").value) setUsageRange(7);
  try {
    const r = await Api.usage({ from: $("#usageFrom").value, to: $("#usageTo").value, user: $("#usageUser").value, kind: $("#usageKind").value });
    State.usage = r;
    renderUsage(r);
  } catch (e) { $("#usageKpis").innerHTML = errorView(e); }
}

function renderUsage(r) {
  const us = $("#usageUser"), cur = us.value;
  us.innerHTML = `<option value="">Tüm kullanıcılar</option>` + r.users.map((u) => `<option>${esc(u)}</option>`).join("");
  us.value = r.users.includes(cur) ? cur : "";

  const t = r.today, limit = t.dailyLimit;
  const pct = limit ? Math.min(100, Math.round((t.total / limit) * 100)) : 0;
  const days = r.days.filter((d) => d.totalTokens > 0).length;
  $("#usageKpis").innerHTML = [
    `<div class="kpi"><div class="k-label">Bugün kullanılan</div><div class="k-val">${fmtNum(t.total)}</div>
      <div class="k-sub">${limit ? `limit ${fmtNum(limit)} · kalan ${fmtNum(t.remaining)}` : "günlük limit yok"}</div>
      ${limit ? `<div class="progress"><i class="${pct >= 100 ? "over" : pct >= 80 ? "warn" : ""}" style="width:${pct}%"></i></div>` : ""}</div>`,
    `<div class="kpi"><div class="k-label">Seçili dönem toplamı</div><div class="k-val">${fmtNum(r.total.totalTokens)}</div>
      <div class="k-sub">${fmtNum(r.total.promptTokens)} girdi · ${fmtNum(r.total.completionTokens)} çıktı</div></div>`,
    `<div class="kpi"><div class="k-label">İstek sayısı</div><div class="k-val">${fmtNum(r.total.requests)}</div>
      <div class="k-sub">${r.total.errors ? `${r.total.errors} hatalı` : "hata yok"}</div></div>`,
    `<div class="kpi"><div class="k-label">Günlük ortalama</div><div class="k-val">${fmtNum(days ? Math.round(r.total.totalTokens / days) : 0)}</div>
      <div class="k-sub">kullanım olan ${days} gün</div></div>`,
  ].join("");
  $("#usageTz").textContent = `Saat dilimi: ${r.timezone}`;
  renderUsageChart(r.days, r.limits.dailyLimit);

  const maxU = Math.max(1, ...r.byUser.map((u) => u.totalTokens));
  $("#usageByUser").innerHTML = r.byUser.map((u) => `
    <div class="hbar" title="${esc(u.key)}: ${fmtNum(u.totalTokens)} token, ${u.requests} istek">
      <span class="name">${esc(u.key)}</span>
      <div class="track"><div class="fill" style="width:${(u.totalTokens / maxU) * 100}%"></div></div>
      <span class="val">${fmtNum(u.totalTokens)}</span>
    </div>`).join("") || `<p class="muted">Bu dönemde kullanım yok.</p>`;

  $("#usageByKind").innerHTML = `<tr><th>Tür</th><th class="num">İstek</th><th class="num">Girdi</th><th class="num">Çıktı</th><th class="num">Toplam</th></tr>` +
    (r.byKind.map((k) => `<tr><td>${esc(KIND_LABEL[k.key] || k.key)}</td><td class="num">${fmtNum(k.requests)}</td>
      <td class="num">${fmtNum(k.promptTokens)}</td><td class="num">${fmtNum(k.completionTokens)}</td><td class="num"><b>${fmtNum(k.totalTokens)}</b></td></tr>`).join("") ||
      `<tr><td colspan="5" class="muted">—</td></tr>`);

  $("#usageCount").textContent = `${r.records.length}${r.records.length >= 500 ? "+ (en yeni 500)" : ""} kayıt`;
  $("#usageTable").innerHTML =
    `<tr><th>Zaman</th><th>Kullanıcı</th><th>Tür</th><th>Sorgu</th><th class="num">Girdi</th><th class="num">Çıktı</th><th class="num">Toplam</th><th>Durum</th></tr>` +
    (r.records.map((x, i) => `<tr class="clickable" data-usage="${i}">
      <td class="mono">${esc(fmtTime(x.time))}</td><td>${esc(x.user)}</td><td>${esc(KIND_LABEL[x.kind] || x.kind)}</td>
      <td class="q-cell" title="${esc(x.question)}">${esc(x.question)}</td>
      <td class="num">${fmtNum(x.promptTokens)}</td><td class="num">${fmtNum(x.completionTokens)}</td>
      <td class="num"><b>${fmtNum(x.totalTokens)}</b>${x.estimated ? ` <span class="muted sm" title="Gateway kullanım bilgisi döndürmedi; tahmini">≈</span>` : ""}</td>
      <td class="${x.ok ? "status-ok" : "status-err"}">${x.ok ? "Başarılı" : "Hata"}</td></tr>`).join("") ||
      `<tr><td colspan="8" class="muted">Bu dönemde sorgu yok.</td></tr>`);
}

/** Günlük kullanım: tek seri dikey çubuk + limit referans çizgisi + çubuk başına hover ipucu */
function renderUsageChart(days, limit) {
  const el = $("#usageChart");
  const W = 640, H = 240, L = 48, R = 12, T = 16, B = 28;
  const max = Math.max(1000, limit || 0, ...days.map((d) => d.totalTokens)) * 1.1;
  const step = Math.pow(10, Math.floor(Math.log10(max / 4)));
  const tick = [1, 2, 2.5, 5, 10].map((m) => m * step).find((s) => max / s <= 5) || step * 10;
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const n = days.length, cw = (W - L - R) / n, bw = Math.max(2, Math.min(36, cw - 2));
  const labelEvery = Math.ceil(n / 10);
  const gridLines = [], yLabels = [], xLabels = [], cols = [];
  for (let v = 0; v <= max; v += tick) {
    gridLines.push(`<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`);
    yLabels.push(`<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${fmtK(v)}</text>`);
  }
  days.forEach((d, i) => {
    const x = L + i * cw + (cw - bw) / 2, top = y(d.totalTokens), r = Math.min(4, bw / 2, y(0) - top);
    const bar = d.totalTokens > 0
      ? `<path class="bar" d="M${x},${y(0)} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${y(0)} Z"/>`
      : "";
    cols.push(`<g class="col" data-i="${i}"><rect class="hit" x="${L + i * cw}" y="${T}" width="${cw}" height="${H - T - B}"/>${bar}</g>`);
    if (i % labelEvery === 0)
      xLabels.push(`<text x="${L + i * cw + cw / 2}" y="${H - 8}" text-anchor="middle">${d.date.slice(8, 10)}.${d.date.slice(5, 7)}</text>`);
  });
  const limitLine = limit
    ? `<line class="limit" x1="${L}" x2="${W - R}" y1="${y(limit)}" y2="${y(limit)}"/>
       <text class="limit-label" x="${W - R}" y="${y(limit) - 5}" text-anchor="end">Günlük limit ${fmtK(limit)}</text>`
    : "";
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Günlük token kullanımı">
      <g class="grid">${gridLines.join("")}</g><g class="axis">${yLabels.join("")}${xLabels.join("")}</g>${cols.join("")}${limitLine}
    </svg><div class="tip hidden"></div>`;
  const tip = $(".tip", el);
  el.onmousemove = (e) => {
    const g = e.target.closest(".col");
    if (!g) return tip.classList.add("hidden");
    const d = days[+g.dataset.i];
    const box = el.getBoundingClientRect(), rect = g.querySelector(".hit").getBoundingClientRect();
    tip.innerHTML = `<b>${d.date.split("-").reverse().join(".")}</b><br>${fmtNum(d.totalTokens)} token · ${d.requests} istek`;
    tip.style.left = `${rect.left - box.left + rect.width / 2}px`;
    tip.style.top = `${Math.max(12, (g.querySelector(".bar")?.getBoundingClientRect().top ?? rect.bottom) - box.top)}px`;
    tip.classList.remove("hidden");
  };
  el.onmouseleave = () => tip.classList.add("hidden");
}

function showUsageRecord(i) {
  const x = State.usage?.records[i];
  if (!x) return;
  modal(KIND_LABEL[x.kind] || x.kind, `
    <dl class="kv">
      <dt>Zaman</dt><dd>${esc(fmtTime(x.time))}</dd>
      <dt>Kullanıcı</dt><dd>${esc(x.user)}${x.ip ? ` <span class="muted sm">(${esc(x.ip)})</span>` : ""}</dd>
      <dt>Model</dt><dd>${esc(x.model || "—")}</dd>
      <dt>Token</dt><dd>${fmtNum(x.promptTokens)} girdi · ${fmtNum(x.completionTokens)} çıktı · <b>${fmtNum(x.totalTokens)}</b> toplam${x.estimated ? " (tahmini)" : ""}</dd>
      <dt>Durum</dt><dd class="${x.ok ? "status-ok" : "status-err"}">${x.ok ? "Başarılı" : esc(x.error || "Hata")}</dd>
    </dl>
    <h4>Sorgu</h4><pre class="code-block">${esc(x.question)}</pre>
    ${x.logId ? `<div class="toolbar end"><button class="btn link sm" id="openLog">İstek/cevap logunu aç</button></div>` : ""}`);
  if (x.logId) $("#openLog").onclick = () => showLog(x.logId);
}

function usageCsv() {
  const r = State.usage;
  if (!r) return;
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [["zaman", "kullanici", "ip", "tur", "sorgu", "model", "girdi_token", "cikti_token", "toplam_token", "tahmini", "durum"].join(";"),
    ...r.records.map((x) => [x.time, x.user, x.ip, x.kind, x.question, x.model, x.promptTokens, x.completionTokens, x.totalTokens, x.estimated, x.ok ? "ok" : x.error].map(q).join(";"))];
  download(`unixai-token-${r.from}_${r.to}.csv`, "﻿" + lines.join("\n"), "text/csv;charset=utf-8");
}

/* ================================================================== */
/*  Ayarlar                                                            */
/* ================================================================== */
/** "a.b" adlı alanları iç içe nesneden doldurur / okur */
function fillForm(form, obj) {
  for (const el of form.elements) {
    if (!el.name) continue;
    const v = el.name.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
    if (el.type === "checkbox") el.checked = Boolean(v);
    else el.value = v ?? "";
  }
}
function readForm(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    const keys = el.name.split(".");
    let o = out;
    keys.slice(0, -1).forEach((k) => (o = o[k] ||= {}));
    o[keys.at(-1)] = el.type === "checkbox" ? el.checked : el.type === "number" ? (el.value === "" ? "" : Number(el.value)) : el.value;
  }
  return out;
}

function headerEditor(root, list) {
  const draw = (rows) => {
    root.innerHTML = `<div class="hdr-head"><b>Header'lar</b><button type="button" class="btn link sm" data-hadd>+ Header ekle</button></div>
      ${rows.map((h) => `<div class="hdr-row"><input class="input sm mono" data-hn value="${esc(h.name)}" placeholder="header-adi" />
        <input class="input sm mono" data-hv value="${esc(h.value)}" placeholder="değer veya {{uuid}}" /><button type="button" class="icon-btn sm" data-hdel title="Sil">✕</button></div>`).join("") ||
        `<p class="muted sm">Header yok.</p>`}`;
  };
  draw(list || []);
  root.onclick = (e) => {
    const cur = headerValues(root);
    if (e.target.matches("[data-hadd]")) draw([...cur, { name: "", value: "" }]);
    if (e.target.matches("[data-hdel]")) {
      const idx = [...root.querySelectorAll("[data-hdel]")].indexOf(e.target);
      draw(cur.filter((_, i) => i !== idx));
    }
  };
}
const headerValues = (root) => [...root.querySelectorAll(".hdr-row")].map((r) => ({ name: $("[data-hn]", r).value.trim(), value: $("[data-hv]", r).value }));

async function loadSettingsPage() {
  loadLimits();
  loadMorpheus();
  loadPrompts();
  try {
    const [c, s] = await Promise.all([Api.connection(), Api.settings()]);
    State.connection = c;
    State.settings = s;
    renderConnection(c);
    fillForm($("#sshForm"), s);
    renderThresholds(s.thresholds);
  } catch (e) { $("#connResult").innerHTML = errorView(e); }
}

function renderConnection(c) {
  fillForm($("#connForm"), c);
  headerEditor($('[data-headers="tms"]'), c.tms.headers);
  headerEditor($('[data-headers="chat"]'), c.chat.headers);
  const host = (u) => { try { return new URL(u).host; } catch { return "tanımlı değil"; } };
  $("#flowTmsHost").textContent = host(c.tms.url);
  $("#flowChatHost").textContent = host(c.chat.url);
  $("#flowTms").classList.toggle("ok", Boolean(c.tms.url));
  $("#flowChat").classList.toggle("ok", Boolean(c.chat.url));
  $("#connInfo").textContent = c.updatedAt ? `Son kayıt: ${fmtTime(c.updatedAt)}${c.updatedBy ? ` · ${c.updatedBy}` : ""}` : `Kaynak: ${c.source}`;
}

async function saveConnection() {
  const f = readForm($("#connForm"));
  f.tms.headers = headerValues($('[data-headers="tms"]'));
  f.chat.headers = headerValues($('[data-headers="chat"]'));
  const c = await Api.saveConnection(f);
  State.connection = c;
  renderConnection(c);
  checkHealth();
  return c;
}

async function testConnection(step, btn) {
  const out = $("#connResult");
  btn.disabled = true;
  try {
    await saveConnection();
    out.innerHTML = loadingView(step === "chat" ? "Token alınıyor ve GPT'ye test mesajı gönderiliyor…" : "Token alınıyor…");
    const r = await Api.testConnection(step);
    const line = (ok, label, detail) => `<div class="test-line"><b class="${ok ? "status-ok" : "status-err"}">${ok ? "✓" : "✕"}</b> <b>${label}</b> ${detail}</div>`;
    out.innerHTML = `<div class="alert ${r.token.ok && (!r.chat || r.chat.ok) ? "info" : "danger"}">
      ${line(r.token.ok, "Token servisi", r.token.ok ? `token alındı · ${r.token.expiresIn} sn geçerli` : esc(r.token.error))}
      ${r.chat ? line(r.chat.ok, "chat/completions", r.chat.ok ? `${esc(r.chat.model || "")} cevabı: <code>${esc(r.chat.reply)}</code>` : esc(r.chat.error)) : ""}
      <div class="toolbar mt-s"><a class="btn link sm" href="#/logs">İstek ve cevapları AI Logları'nda görün</a></div></div>`;
    refreshTokenChip();
  } catch (e) { out.innerHTML = errorView(e); }
  finally { btn.disabled = false; }
}

function renderThresholds(t) {
  $("#thrFields").innerHTML = Object.keys(TH_LABEL).map((k) =>
    `<label>${esc(TH_LABEL[k])}<input class="input" name="thresholds.${k}" type="number" step="any" value="${esc(t[k])}" /></label>`).join("");
}

async function loadLimits() {
  try {
    const l = await Api.limits();
    const f = $("#limitForm");
    f.elements.dailyLimit.value = l.dailyLimit || 0;
    f.elements.perUserDailyLimit.value = l.perUserDailyLimit || 0;
    $("#limitInfo").textContent = l.updatedAt ? `Son değişiklik: ${fmtTime(l.updatedAt)}${l.updatedBy ? ` · ${l.updatedBy}` : ""}` : "";
  } catch (e) { $("#limitInfo").textContent = e.message; }
}

async function loadPrompts() {
  try {
    const p = await Api.prompts();
    State.prompts = p;
    $("#promptForm").elements.system.value = p.system;
    $("#promptInfo").textContent = p.updatedAt ? `Son kayıt: ${fmtTime(p.updatedAt)}` : "Varsayılan metin";
  } catch (e) { $("#promptInfo").textContent = `Yüklenemedi: ${e.message}`; }
}

/* ---- Giriş ---- */
let loginWaiters = [];
function showLogin() {
  $("#loginHint").textContent = State.auth?.type === "local"
    ? "Yerel yönetici hesabıyla giriş yapın (sunucudaki .env: ADMIN_USER / ADMIN_PASSWORD)."
    : "Kurumsal (LDAP / Active Directory) kullanıcı adı ve parolanızı kullanın.";
  $("#loginScreen").classList.remove("hidden");
  setTimeout(() => $("#loginForm").elements.username.focus(), 30);
  return new Promise((resolve) => loginWaiters.push(resolve));
}

async function submitLogin(e) {
  e.preventDefault();
  const f = e.target, err = $("#loginError"), btn = $("#loginBtn");
  err.classList.add("hidden");
  btn.disabled = true;
  btn.textContent = "Doğrulanıyor…";
  try {
    const r = await Api.login(f.elements.username.value.trim(), f.elements.password.value);
    State.auth = { ...State.auth, enabled: true, user: r.user };
    f.elements.password.value = "";
    $("#loginScreen").classList.add("hidden");
    loginWaiters.forEach((w) => w(r.user));
    loginWaiters = [];
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove("hidden");
    f.elements.password.select();
  } finally {
    btn.disabled = false;
    btn.textContent = "Giriş yap";
  }
}

function applyAuthUi() {
  const a = State.auth || {};
  $("#uiCard").classList.toggle("hidden", Boolean(a.enabled));
  $("#authBanner").classList.toggle("hidden", Boolean(a.enabled));
  const readOnly = a.enabled && a.user && !a.user.isAdmin;
  ["#limitForm", "#promptForm", "#connForm", "#sshForm", "#thrForm"].forEach((id) => {
    $(id).classList.toggle("form-disabled", Boolean(readOnly));
    $(id).title = readOnly ? "Bu ayarları yalnızca yöneticiler değiştirebilir" : "";
  });
  renderUserChip();
}

function renderUserChip() {
  const a = State.auth;
  $("span", $("#userChip")).textContent = a?.enabled
    ? `${a.user?.displayName || a.user?.username || "Oturum yok"}${a.user?.isAdmin ? " · yönetici" : ""}`
    : Settings.get().userName || "Kullanıcı adı girin";
}

function userMenu() {
  const a = State.auth;
  if (!a?.enabled) return askUserName(true);
  modal("Oturum", `
    <dl class="kv">
      <dt>Ad</dt><dd>${esc(a.user?.displayName || "—")}</dd>
      <dt>Kullanıcı adı</dt><dd class="mono">${esc(a.user?.username || "—")}</dd>
      <dt>Yetki</dt><dd>${a.user?.isAdmin ? "Yönetici" : "Kullanıcı"}</dd>
    </dl>
    <div class="toolbar end"><button class="btn primary" id="logoutBtn">Çıkış yap</button></div>`);
  $("#logoutBtn").onclick = async () => {
    try { await Api.logout(); } catch {}
    location.reload();
  };
}

function askUserName(force = false) {
  if (State.auth?.enabled) return;
  if (Settings.get().userName && !force) return;
  modal("Kullanıcı adınız", `
    <form id="userForm" class="form">
      <label>Ad / sicil
        <input class="input" name="userName" required maxlength="80" value="${esc(Settings.get().userName)}" placeholder="ör. EnisA" />
        <small>Token kullanım raporlarında "kim kullandı" olarak görünür. Bu tarayıcıda saklanır.</small>
      </label>
      <div class="toolbar end"><button class="btn primary" type="submit">Kaydet</button></div>
    </form>`);
  $("#userForm").onsubmit = (e) => {
    e.preventDefault();
    Settings.save({ userName: e.target.elements.userName.value.trim() });
    $("#settingsForm").elements.userName.value = Settings.get().userName;
    renderUserChip();
    closeModal();
    refreshTokenChip();
  };
  setTimeout(() => $("#userForm input").focus(), 30);
}

/* ---- Bağlantı durumu ---- */
function setChip(el, cls, text, title) {
  el.className = `chip link-chip ${cls}`;
  $("span", el).textContent = text;
  el.title = title;
}

async function checkHealth() {
  try {
    const h = await Api.health();
    State.health = h;
    renderNavFoot();
    const g = h.genai;
    setChip($("#aiChip"), h.mode === "demo" ? "warn" : "ok", h.mode === "demo" ? "AI · demo" : `AI · ${g.model || "GPT"}`,
      h.mode === "genai" ? `Token: ${g.tokenHost} · Chat: ${g.chatHost}` : "GenAI bağlantısı tanımlı değil — Ayarlar");
  } catch (e) {
    setChip($("#aiChip"), "err", "AI", e.message);
  }
}

/* ================================================================== */
/*  Olaylar & başlatma                                                 */
/* ================================================================== */
/* ================================================================== */
/*  Tema & komut paleti (Ctrl+K)                                       */
/* ================================================================== */
function toggleTheme() {
  const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("unixai.theme", next); } catch {}
}

const PAGES = [
  ["dashboard", "Genel Bakış", "dashboard"], ["runs", "Toplama & Analiz", "refresh"], ["collected", "Gelen Loglar", "logs"],
  ["ask", "UnixAI Ajanı", "chat"], ["a2a", "MorpheusAI (ajanlar arası)", "bolt"], ["agent", "Ajan & MCP", "sparkles"], ["servers", "Sunucular", "servers"], ["usage", "Token Kullanımı", "chart"],
  ["logs", "AI Logları", "file"], ["audit", "İşlem Kayıtları", "list"], ["settings", "Ayarlar", "sliders"],
];
const Palette = { items: [], sel: 0 };

function paletteItems(q) {
  q = q.trim().toLowerCase();
  const servers = (State.overview?.servers || []).map((s) => ({
    group: "Sunucular", icon: "servers", label: s.name, sub: `${s.host || ""}${s.latest?.score != null ? ` · skor ${s.latest.score}` : ""}`,
    run: () => (location.hash = `#/server/${encodeURIComponent(s.name)}`), key: `${s.name} ${s.host} ${s.tags.join(" ")}`,
  }));
  const pages = PAGES.map(([r, l, i]) => ({ group: "Ekranlar", icon: i, label: l, run: () => (location.hash = `#/${r}`), key: l }));
  const actions = [
    { group: "İşlemler", icon: "bolt", label: "Şimdi topla ve analiz et", run: () => startRun("collect") },
    { group: "İşlemler", icon: "sparkles", label: "Sadece yeniden analiz et", run: () => startRun("reanalyze") },
    { group: "İşlemler", icon: "flask", label: "Demo veri üret", run: () => startRun("demo") },
    { group: "İşlemler", icon: "plus", label: "Sunucu ekle", run: () => { location.hash = "#/servers"; setTimeout(() => serverModal(null), 150); } },
    { group: "İşlemler", icon: "theme", label: "Açık / koyu tema", run: toggleTheme },
  ].map((x) => ({ ...x, key: x.label }));
  const all = [...servers, ...pages, ...actions];
  return q ? all.filter((x) => x.key.toLowerCase().includes(q)) : all;
}

function renderPalette() {
  const list = $("#paletteList");
  let group = "";
  list.innerHTML = Palette.items.map((x, i) => {
    const head = x.group !== group ? `<div class="palette-group">${esc((group = x.group))}</div>` : "";
    return `${head}<div class="palette-item ${i === Palette.sel ? "on" : ""}" data-pi="${i}">${icon(x.icon)}<span>${esc(x.label)}</span>${x.sub ? `<span class="pi-sub">${esc(x.sub)}</span>` : ""}</div>`;
  }).join("") || `<div class="empty sm">Sonuç yok</div>`;
  list.querySelector(".palette-item.on")?.scrollIntoView({ block: "nearest" });
}

function openPalette() {
  $("#palette").classList.remove("hidden");
  const inp = $("#paletteInput");
  inp.value = "";
  Palette.items = paletteItems("");
  Palette.sel = 0;
  renderPalette();
  setTimeout(() => inp.focus(), 10);
}
const closePalette = () => $("#palette").classList.add("hidden");

function runPaletteItem(i) {
  const x = Palette.items[i];
  if (!x) return;
  closePalette();
  x.run();
}

function bindPalette() {
  $("#searchBtn").onclick = openPalette;
  $("#themeBtn").onclick = toggleTheme;
  $("#palette").onclick = (e) => {
    if (e.target.id === "palette") return closePalette();
    const it = e.target.closest("[data-pi]");
    if (it) runPaletteItem(+it.dataset.pi);
  };
  $("#paletteInput").oninput = (e) => { Palette.items = paletteItems(e.target.value); Palette.sel = 0; renderPalette(); };
  $("#paletteInput").onkeydown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); Palette.sel = Math.min(Palette.items.length - 1, Palette.sel + 1); renderPalette(); }
    if (e.key === "ArrowUp") { e.preventDefault(); Palette.sel = Math.max(0, Palette.sel - 1); renderPalette(); }
    if (e.key === "Enter") { e.preventDefault(); runPaletteItem(Palette.sel); }
    if (e.key === "Escape") closePalette();
  };
  addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
    if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) { e.preventDefault(); openPalette(); }
    else if (e.key === "/" && !typing) { e.preventDefault(); openPalette(); }
  });
}

/* ================================================================== */
/*  MorpheusAI köprüsü (düzeltme playbook'ları)                         */
/* ================================================================== */
const REM_STATUS = {
  generated: ["Onay bekliyor", "gold"], approved: ["Onaylı", "blue"], check_running: ["Check çalışıyor", "blue"],
  check_ok: ["Check başarılı", "green"], check_failed: ["Check hatalı", "red"], running: ["Uygulanıyor", "blue"],
  applied: ["Uygulandı", "green"], failed: ["Hatalı", "red"], rejected: ["Reddedildi", ""],
};
const remTag = (s) => { const [l, c] = REM_STATUS[s] || [s || "—", ""]; return `<span class="label ${c}">${esc(l)}</span>`; };
const morConfigured = () => Boolean(State.health?.morpheus?.configured);

/** Aksiyon kartındaki MorpheusAI düğmesi / durum rozeti */
function remButton(i, title) {
  const rec = (State.sd.rems || []).find((r) => r.actionIndex === i && r.title === title && r.runId === State.sd.snap?.run?.id)
    || (State.sd.rems || []).find((r) => r.title === title);
  if (rec) return `<a class="rem-chip" href="${esc(rec.url)}" target="_blank" rel="noopener" title="MorpheusAI'de aç">${icon("bolt", "i i-xs")} MorpheusAI ${remTag(rec.status)}${rec.score != null ? `<span class="mono">uyum ${rec.score}</span>` : ""}</a>`;
  if (!morConfigured()) return "";
  return `<button class="btn sm" data-send-rem="${i}">${icon("bolt")}MorpheusAI'ye gönder</button>`;
}

async function sendRemediation(i, btn) {
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner xs"></span> Playbook üretiliyor…`;
  try {
    const r = await Api.sendRemediation(State.sd.name, i, State.sd.snap.run.id);
    toast(`MorpheusAI playbook'u üretti (uyum ${r.score ?? "—"}) — onay MorpheusAI'de`);
    State.sd.rems = [r, ...(State.sd.rems || [])];
    renderServer();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
    btn.innerHTML = `${icon("bolt")}MorpheusAI'ye gönder`;
  }
}


/* ================================================================== */
/*  Playbook onayı: MorpheusAI yazar, bir kişi UnixAI'den onaylar        */
/*  (onay MorpheusAI'ye X-Approved-By ile aktarılır; ajanlar onay veremez) */
/* ================================================================== */
State.remLive = {};
State.remYaml = {};
State.approvals = [];

function remDecideHtml(r) {
  const id = esc(r.id), by = r.approval?.by ? `<span class="label blue">Onaylayan: ${esc(r.approval.by)}</span>` : "";
  const open = r.url ? `<a class="btn link sm" href="${esc(r.url)}" target="_blank" rel="noopener">MorpheusAI'de aç</a>` : "";
  const refresh = `<button class="btn link sm" data-rem-act="refresh" data-rem-id="${id}">${icon("refresh", "i i-xs")}Yenile</button>`;
  const row = (cls, ic, title, sub, actions) => `<div class="rem-decide ${cls}">${icon(ic)}<div class="rem-decide-t"><b>${title}</b>${sub ? `<div class="muted sm">${sub}</div>` : ""}</div>
      <div class="rem-decide-a">${actions}</div></div>`;
  if (r.rejected || r.status === "rejected")
    return row("off", "alert", "Reddedildi", `${esc(r.rejected?.by || "")}${r.rejected?.reason ? ` — ${esc(r.rejected.reason)}` : ""}`, open);
  if (r.runnable === false) return row("off", "alert", "Bu playbook AAP runner ile çalıştırılamıyor", "MorpheusAI'de inceleyin.", open);
  if (!r.approval)
    return row("ask", "shield", "MorpheusAI bu playbook'u yazdı ve onayınızı bekliyor",
      "Onaylarsanız AAP'de önce <b>check (dry-run)</b> çalışır, sunucuda değişiklik yapılmaz. Sunucuya uygulama ayrıca sizin onayınızla yapılır.",
      `<button class="btn link sm danger" data-rem-act="reject" data-rem-id="${id}">Reddet</button>
       <button class="btn primary sm" data-rem-act="approve" data-rem-id="${id}">${icon("check", "i i-xs")}Onayla</button>`);
  const st = r.status;
  if (st === "approved" || st === "check_running") return row("run", "clock", "Onaylandı — AAP'de check (dry-run) çalışıyor", by, refresh + open);
  if (st === "check_ok")
    return row("ok", "check", "Check başarılı — sunucuya uygulanmaya hazır", `${by} Uygulama hedefi: <code>${esc(r.target || r.server || "")}</code>`,
      `${r.disruptive ? `<label class="check sm"><input type="checkbox" data-rem-disruptive /> Kesinti riskli adımlara izin ver</label>` : ""}
       <button class="btn primary sm" data-rem-act="run" data-rem-id="${id}" data-target="${esc(r.target || r.server || "")}">${icon("bolt", "i i-xs")}Sunucuya uygula</button>`);
  if (st === "check_failed")
    return row("bad", "alert", "Check (dry-run) hatalı", `${by} AAP çıktısını MorpheusAI'de inceleyin.`,
      `<button class="btn sm" data-rem-act="check" data-rem-id="${id}">Check'i tekrar çalıştır</button>` + open);
  if (st === "running") return row("run", "clock", "Sunucuya uygulanıyor (AAP)", by, refresh + open);
  if (st === "applied") return row("ok", "check", "Sunucuya uygulandı", by, open);
  if (st === "failed") return row("bad", "alert", "Uygulama hatalı", `${by} AAP çıktısını MorpheusAI'de inceleyin.`, open);
  return row("", "clock", esc(st || "—"), by, refresh + open);
}

function remLiveBox(id) {
  const c = State.remLive[id];
  return `<div class="rem-live" data-rem-live="${esc(id)}">${c ? remDecideHtml(c) : `<div class="muted sm">Onay durumu MorpheusAI'den alınıyor…</div>`}</div>`;
}

async function hydrateRem(root = document) {
  await Promise.all($$("[data-rem-live]", root).filter((el) => !el.dataset.loaded).map(async (el) => {
    el.dataset.loaded = "1";
    try {
      const r = await Api.remediation(el.dataset.remLive);
      State.remLive[r.id] = r;
      if (r.playbook) State.remYaml[r.id] = r.playbook;
      $$(`[data-rem-live="${CSS.escape(r.id)}"]`).forEach((x) => (x.innerHTML = remDecideHtml(r)));
    } catch (e) { el.innerHTML = `<div class="muted sm">Onay durumu alınamadı: ${esc(e.message)}</div>`; }
  }));
}

async function refreshApprovals() {
  if (!morConfigured()) { State.approvals = []; return renderApprovals(); }
  try { State.approvals = (await Api.approvals()).items; } catch { State.approvals = []; }
  renderApprovals();
}

function renderApprovals() {
  const n = State.approvals.length, b = $("#apprBadge");
  if (b) { b.textContent = n; b.hidden = !n; b.title = `${n} playbook onayınızı bekliyor`; }
  const box = $("#apprBanner");
  if (!box) return;
  box.innerHTML = n ? `<div class="card appr-banner">
      <div class="appr-h">${icon("shield")}<div><b>${n} playbook onayınızı bekliyor</b>
        <div class="muted sm">MorpheusAI bu playbook'ları yazdı. Siz onaylamadan AAP'de hiçbir şey çalıştırılmaz.</div></div></div>
      <div class="appr-list">${State.approvals.map((a) => `<div class="appr-item">
        <code>${esc(a.server || "")}</code><span class="appr-title">${esc(a.playbookTitle || a.title || a.id)}</span>
        ${a.score != null ? `<span class="label ${a.score >= 85 ? "green" : "gold"}">uyum ${a.score}</span>` : ""}${a.disruptive ? `<span class="label gold">kesinti riskli</span>` : ""}
        <span class="muted sm mono">${esc(a.id)}</span>
        <button class="btn sm primary" data-rem-open="${esc(a.id)}">İncele ve onayla</button></div>`).join("")}</div></div>` : "";
}

function bindApprovals() {
  document.addEventListener("click", async (e) => {
    const o = e.target.closest("[data-rem-open]");
    if (o) return showRemYaml(o.dataset.remOpen);
    const c = e.target.closest("[data-copy-yaml]");
    if (c && !c.onclick) return copyText(State.remYaml[c.dataset.copyYaml] || "");
    const b = e.target.closest("[data-rem-act]");
    if (!b) return;
    const id = b.dataset.remId, act = b.dataset.remAct, box = b.closest("[data-rem-live]");
    if (act === "refresh") { $$(`[data-rem-live="${CSS.escape(id)}"]`).forEach((x) => delete x.dataset.loaded); return hydrateRem(); }
    let body = {};
    if (act === "approve" && !confirm(`${id} playbook'unu onaylıyor musunuz?\n\nOnaydan sonra AAP'de check (dry-run) çalıştırılır; sunucuda değişiklik yapılmaz.`)) return;
    if (act === "reject") { const reason = prompt("Ret nedeni (opsiyonel):"); if (reason === null) return; body = { reason }; }
    if (act === "check") body = { mode: "check" };
    if (act === "run") {
      if (!confirm(`Playbook ${b.dataset.target || "sunucuya"} üzerinde GERÇEKTEN UYGULANACAK (AAP). Emin misiniz?`)) return;
      body = { mode: "run", allowDisruptive: Boolean(box?.querySelector("[data-rem-disruptive]")?.checked) };
    }
    b.disabled = true;
    try {
      const r = await Api.remDecide(id, act === "run" || act === "check" ? "launch" : act, body);
      State.remLive[r.id] = r;
      $$(`[data-rem-live="${CSS.escape(r.id)}"]`).forEach((x) => (x.innerHTML = remDecideHtml(r)));
      toast({ approve: "Onaylandı — MorpheusAI AAP'de check (dry-run) başlattı", reject: "Reddedildi", check: "Check başlatıldı", run: "Playbook sunucuya uygulanıyor" }[act]);
      refreshApprovals();
    } catch (err) { toast(err.message, true); b.disabled = false; }
  });
}

function remsPane() {
  const rows = State.sd.rems || [];
  return `<div class="toolbar"><span class="muted sm">Bu sunucu için MorpheusAI'ye gönderilen aksiyonlar. Playbook'u görmek ve onaylamak için <b>YAML / Onay</b>'a basın; onaydan önce AAP'de hiçbir şey çalıştırılmaz.</span>
      <span class="toolbar-spacer"></span><button class="btn sm" id="remReload">${icon("refresh")}Durumları yenile</button></div>
    <div class="table-wrap"><table class="table"><tr><th>Gönderim</th><th>Aksiyon</th><th>Durum</th><th>Uyum</th><th>Son AAP işi</th><th>AAP hedefi</th><th></th></tr>
    ${rows.map((r) => `<tr><td class="t-sub">${esc(fmtTime(r.sentAt))}<br>${esc(r.sentBy || "")}</td>
      <td>${r.priority ? prioTag(r.priority) : ""} ${esc(r.title)}${r.refreshError ? `<div class="t-sub status-err">${esc(r.refreshError)}</div>` : ""}</td>
      <td>${remTag(r.status)}</td><td>${r.score != null ? scoreBar(r.score) : "—"}</td>
      <td class="t-sub">${r.lastJob ? `#${esc(r.lastJob.id)} ${esc(r.lastJob.mode)} · ${esc(r.lastJob.status)}` : "—"}</td>
      <td class="mono">${esc(r.target || "")}</td>
      <td class="row-actions"><button class="btn link sm" data-rem-yaml="${esc(r.id)}">YAML / Onay</button><a class="btn link sm" href="${esc(r.url)}" target="_blank" rel="noopener">MorpheusAI'de aç</a></td></tr>`).join("") ||
      `<tr><td colspan="7" class="muted">Henüz gönderilmedi. Aksiyonlar sekmesinde <b>MorpheusAI'ye gönder</b>'e basın.</td></tr>`}</table></div>`;
}

async function showRemYaml(id) {
  modal("Playbook (MorpheusAI)", loadingView("MorpheusAI'den alınıyor…"), { wide: true });
  try {
    const r = await Api.remediation(id);
    State.remLive[r.id] = r;
    $("#modalBody").innerHTML = remCard({ ...r, title: r.playbookTitle || r.title, lastJob: r.lastJob, approval: r.approval });
    $$("[data-copy-yaml]", $("#modalBody")).forEach((b) => (b.onclick = () => copyText(r.playbook || "")));
  } catch (e) { $("#modalBody").innerHTML = errorView(e); }
}

async function loadMorpheus() {
  try {
    const m = await Api.morpheus();
    fillForm($("#morForm"), m);
    $("#flowMor").classList.toggle("ok", m.configured);
    $("#flowMorHost").textContent = m.url ? new URL(m.url).host : "tanımlı değil";
    $("#morInfo").textContent = m.updatedAt ? `Son kayıt: ${fmtTime(m.updatedAt)}${m.updatedBy ? ` · ${m.updatedBy}` : ""}` : "";
  } catch (e) { $("#morResult").innerHTML = errorView(e); }
}

async function saveMorpheus() {
  const m = await Api.saveMorpheus(readForm($("#morForm")));
  fillForm($("#morForm"), m);
  checkHealth();
  loadMorpheus();
  return m;
}

/* ================================================================== */
/*  Ajan & MCP                                                         */
/* ================================================================== */
const MODE_LABEL = { native: "function calling", json: "JSON protokolü", auto: "otomatik" };

/** Ajan cevabının altındaki araç adımları */
function traceHtml(trace) {
  if (!trace?.length) return "";
  const steps = trace.filter((t) => t.type === "tool" || t.type === "info");
  const remote = [...new Set(steps.filter((t) => t.remoteAgent).map((t) => t.where))];
  return `<details class="trace"><summary>${icon("bolt", "i i-xs")} ${steps.filter((t) => t.type === "tool").length} araç adımı${remote.length ? ` · <b>ajanla konuştu: ${esc(remote.join(", "))}</b>` : ""}</summary>
    ${steps.map((t) => t.type === "info" ? `<div class="tstep info">${esc(t.text)}</div>` : `<div class="tstep ${t.ok ? "" : "err"} ${t.remoteAgent ? "agent" : ""}">
      <div class="tstep-h"><span class="mono">${t.step}.</span> ${t.remoteAgent ? `<span class="label blue">ajan → ${esc(t.where)}</span>` : t.where && t.where !== "local" ? `<span class="label">${esc(t.where)}</span>` : ""}
        <b class="mono">${esc(t.tool)}</b> <span class="${t.ok ? "status-ok" : "status-err"}">${t.ok ? "✓" : "✕"}</span> <span class="muted sm">${fmtDur(t.durationMs)}</span></div>
      <details><summary class="muted sm">girdi / sonuç</summary><pre class="code-block">${esc(JSON.stringify(t.args, null, 1))}</pre><pre class="code-block">${esc(t.ok ? t.result : t.error)}</pre></details>
    </div>`).join("")}</details>`;
}

async function loadAgentPage() {
  const [srv, conns, st, convs] = await Promise.allSettled([Api.mcpServer(), Api.mcpConnections(), Api.agentSettings(), Api.conversations()]);
  if (srv.status === "fulfilled") renderMcpServer(srv.value);
  if (conns.status === "fulfilled") renderConns(conns.value);
  if (st.status === "fulfilled") {
    fillForm($("#agentForm"), st.value);
    $("#agentDetected").textContent = st.value.mode === "auto" ? `Algılanan: ${st.value.detected ? MODE_LABEL[st.value.detected] : "henüz bilinmiyor (ilk istekte denenir)"}` : "";
  }
  if (convs.status === "fulfilled") renderConvs(convs.value);
  renderAgentMap(conns.status === "fulfilled" ? conns.value : [], srv.status === "fulfilled" ? srv.value : null);
}

function renderAgentMap(conns, srv) {
  const tokens = srv?.tokens || [];
  $("#agentMap").innerHTML = `<div class="amap">
    <div class="amap-col"><div class="amap-h">Bağlanan istemciler</div>
      ${tokens.map((t) => `<div class="amap-node in">${icon("key", "i i-xs")} ${esc(t.name)}${t.readonly ? ` <span class="label">salt-okunur</span>` : ""}<small>${t.lastUsedAt ? `son: ${esc(ago(t.lastUsedAt))}` : "henüz bağlanmadı"}</small></div>`).join("") || `<div class="amap-node ghost">Henüz token yok</div>`}
    </div>
    <div class="amap-arrow">→ /mcp →</div>
    <div class="amap-col center"><div class="amap-core">${icon("sparkles")}<b>UnixAI ajanı</b><small>${esc(MODE_LABEL[State.health?.agent?.detected || State.health?.agent?.mode] || "")}</small></div></div>
    <div class="amap-arrow">→ MCP →</div>
    <div class="amap-col"><div class="amap-h">Bağlı sunucular</div>
      ${conns.map((c) => `<div class="amap-node out ${c.enabled ? "" : "off"}">${icon("bolt", "i i-xs")} ${esc(c.name)}${c.lastTest ? ` <span class="${c.lastTest.ok ? "status-ok" : "status-err"}">${c.lastTest.ok ? `✓ ${c.lastTest.tools} araç` : "✕"}</span>` : ""}<small>${esc(c.url)}</small></div>`).join("") || `<div class="amap-node ghost">Henüz bağlantı yok</div>`}
    </div></div>`;
}

function renderMcpServer(s) {
  State.mcpServer = s;
  $("#mcpServerInfo").innerHTML = `
    <dt>Uç nokta</dt><dd><code>${esc(s.endpoint)}</code> <button class="btn link sm" id="copyEndpoint">Kopyala</button></dd>
    <dt>Taşıma</dt><dd>${esc(s.transport)} · ${esc(s.protocolVersions.join(", "))}</dd>
    <dt>Araçlar</dt><dd>${s.tools.map((t) => `<span class="label ${t.readonly ? "" : "gold"}" title="${esc(t.description)}">${esc(t.name)}</span>`).join(" ")}</dd>
    <dt>.env token</dt><dd>${s.envToken ? "UNIXAI_API_TOKEN tanımlı (tam yetki)" : "yok"}</dd>`;
  $("#copyEndpoint").onclick = () => copyText(s.endpoint);
  $("#mcpTokens").innerHTML = `<tr><th>Ad</th><th>Önek</th><th>Yetki</th><th>Son kullanım</th><th></th></tr>` +
    (s.tokens.map((t) => `<tr><td class="t-name">${esc(t.name)}</td><td class="mono">${esc(t.prefix)}…</td><td>${t.readonly ? "salt-okunur" : "tam"}</td>
      <td class="t-sub">${t.lastUsedAt ? esc(ago(t.lastUsedAt)) : "—"}</td><td class="row-actions"><button class="btn link sm danger" data-del-token="${esc(t.id)}">İptal et</button></td></tr>`).join("") ||
      `<tr><td colspan="5" class="muted">Token yok. Başka bir ajanın / MCP istemcisinin bağlanması için token üretin.</td></tr>`);
  $("#mcpOriginForm").elements.origins.value = s.allowedOrigins.join("\n");
}

function showNewToken(t) {
  const ep = State.mcpServer?.endpoint || location.origin + "/mcp";
  const cfg = JSON.stringify({ mcpServers: { "unixai-os": { type: "http", url: ep, headers: { Authorization: `Bearer ${t.token}` } } } }, null, 2);
  modal(`Token: ${t.name}`, `<div class="alert info">Token yalnızca şimdi gösterilir; güvenli bir yere kaydedin.</div>
    <div class="fix"><pre class="term">${esc(t.token)}</pre><button class="btn link sm copy" id="cpTok">Kopyala</button></div>
    <div class="sub-h">MorpheusAI'de (veya başka bir ajanda) bağlantı</div>
    <p class="sm muted">Ajan &amp; MCP → Bağlantı ekle: ad <code>unixai</code>, adres <code>${esc(ep)}</code>, bu token.</p>
    <div class="sub-h">Claude Code</div>
    <div class="fix"><pre class="term"><span class="ln">claude mcp add --transport http unixai-os ${esc(ep)} --header "Authorization: Bearer ${esc(t.token)}"</span></pre></div>
    <div class="sub-h">JSON yapılandırma (Claude Desktop / diğer istemciler)</div>
    <div class="fix"><pre>${esc(cfg)}</pre><button class="btn link sm copy" id="cpCfg">Kopyala</button></div>`, { wide: true });
  $("#cpTok").onclick = () => copyText(t.token);
  $("#cpCfg").onclick = () => copyText(cfg);
}

function renderConns(list) {
  State.mcpConns = list;
  $("#mcpConns").innerHTML = `<tr><th>Ad</th><th>Adres</th><th>Durum</th><th></th></tr>` +
    (list.map((c) => `<tr class="${c.enabled ? "" : "row-off"}"><td class="t-name">${esc(c.name)}${c.description ? `<div class="t-sub">${esc(c.description)}</div>` : ""}</td>
      <td class="mono t-sub">${esc(c.url)}</td>
      <td>${c.lastTest ? `<span class="${c.lastTest.ok ? "status-ok" : "status-err"}" title="${esc(c.lastTest.error || "")}">${c.lastTest.ok ? `✓ ${c.lastTest.tools} araç` : "✕ hata"}</span><div class="t-sub">${esc(c.lastTest.server?.name || "")} ${esc(ago(c.lastTest.at))}</div>` : `<span class="muted sm">test edilmedi</span>`}</td>
      <td class="row-actions"><button class="btn link sm" data-conn-test="${esc(c.id)}">Test</button><button class="btn link sm" data-conn-edit="${esc(c.id)}">Düzenle</button><button class="btn link sm danger" data-conn-del="${esc(c.id)}">Sil</button></td></tr>`).join("") ||
      `<tr><td colspan="4" class="muted">Bağlantı yok. Örn. MorpheusAI: <code>http://morpheus:3000/mcp</code> + MorpheusAI'de üretilen token.</td></tr>`);
}

function connModal(id) {
  const c = (State.mcpConns || []).find((x) => x.id === id) || { enabled: true };
  modal(id ? `Bağlantı · ${c.name}` : "MCP bağlantısı ekle", `<form id="connF" class="form">
    <div class="grid-2">
      <label>Ad<input class="input" name="name" value="${esc(c.name || "")}" placeholder="morpheusai" required /><small>Küçük harf; araçlar <code>ad__araç</code> olur.</small></label>
      <label>MCP adresi<input class="input mono" name="url" value="${esc(c.url || "")}" placeholder="http://morpheus.kurum.local:3000/mcp" required /></label>
    </div>
    <label>Token (Bearer)<input class="input mono" name="token" type="password" value="${esc(c.token || "")}" autocomplete="new-password" /><small>Karşı uygulamada üretilen MCP erişim token'ı.</small></label>
    <label>Açıklama<input class="input" name="description" value="${esc(c.description || "")}" placeholder="Ansible/AAP otomasyon ajanı" /></label>
    <label class="check"><input type="checkbox" name="enabled" ${c.enabled !== false ? "checked" : ""} /> Etkin (araçları ajana açık)</label>
    <label class="check"><input type="checkbox" name="insecureTls" ${c.insecureTls ? "checked" : ""} /> Sertifika doğrulamasını kapat</label>
    <div id="connOut"></div>
    <div class="toolbar end"><button class="btn primary" type="submit">Kaydet ve test et</button></div></form>`);
  $("#connF").onsubmit = async (e) => {
    e.preventDefault();
    const out = $("#connOut");
    try {
      const saved = await Api.saveMcpConnection({ ...readForm(e.target), id: c.id });
      out.innerHTML = loadingView("Bağlanılıyor…");
      const t = await Api.testMcpConnection(saved.id);
      out.innerHTML = t.ok ? `<div class="alert info"><b class="status-ok">✓ ${esc(t.server?.serverInfo?.title || t.server?.serverInfo?.name || "")}</b> — ${t.tools.length} araç: ${t.tools.map((x) => `<code>${esc(x.name)}</code>`).join(" ")}</div>`
        : `<div class="alert danger"><b>✕</b> ${esc(t.error)}</div>`;
      loadAgentPage();
      checkHealth();
    } catch (err) { out.innerHTML = errorView(err); }
  };
}

function renderConvs(rows) {
  $("#convTable").innerHTML = `<tr><th>Zaman</th><th>Kaynak</th><th>Soru</th><th>Araçlar</th><th>Konuştuğu ajanlar</th><th>Durum</th></tr>` +
    (rows.map((c) => `<tr class="clickable" data-conv="${esc(c.id)}"><td class="mono t-sub">${esc(fmtTime(c.startedAt))}</td>
      <td>${c.hop ? `<span class="label blue">ajan: ${esc(c.origin)}</span>` : esc(c.origin || "")}</td>
      <td class="q-cell" title="${esc(c.question)}">${esc(c.question)}</td>
      <td class="t-sub">${c.tools.length}</td><td>${c.remoteAgents.map((a) => `<span class="label blue">${esc(a)}</span>`).join(" ") || "—"}</td>
      <td class="${c.status === "done" ? "status-ok" : c.status === "failed" ? "status-err" : ""}">${esc(c.status)}</td></tr>`).join("") ||
      `<tr><td colspan="6" class="muted">Henüz konuşma yok.</td></tr>`);
}

async function showConversation(id) {
  try {
    const c = await Api.conversation(id);
    modal(`Ajan konuşması · ${fmtTime(c.startedAt)}`, `<dl class="kv">
      <dt>Kaynak</dt><dd>${esc(c.origin)}${c.hop ? ` · hop ${c.hop}` : ""}${c.parent ? ` · üst konuşma <code>${esc(c.parent)}</code>` : ""}</dd>
      <dt>Mod</dt><dd>${esc(MODE_LABEL[c.mode] || c.mode || "—")}</dd><dt>Durum</dt><dd>${esc(c.status)}${c.error ? ` — <span class="status-err">${esc(c.error)}</span>` : ""}</dd></dl>
      <div class="sub-h">Soru</div><div class="bubble">${esc(c.question)}</div>
      <div class="sub-h">Adımlar</div>${traceHtml(c.trace).replace("<details class=\"trace\">", "<details class=\"trace\" open>")}
      <div class="sub-h">Cevap</div><div class="bubble">${md(c.reply || "")}</div>`, { wide: true });
  } catch (e) { toast(e.message, true); }
}

function bindAgentPage() {
  $("#mcpTokenForm").onsubmit = async (e) => {
    e.preventDefault();
    try { const t = await Api.createMcpToken(e.target.elements.name.value, e.target.elements.readonly.checked); e.target.reset(); showNewToken(t); loadAgentPage(); }
    catch (err) { toast(err.message, true); }
  };
  $("#mcpOriginForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await Api.saveMcpOrigins(e.target.elements.origins.value.split(/\s+/).filter(Boolean)); toast("İzinli alanlar kaydedildi"); }
    catch (err) { toast(err.message, true); }
  };
  $("#mcpTokens").onclick = async (e) => {
    const b = e.target.closest("[data-del-token]");
    if (b && confirm("Bu token iptal edilsin mi? Onu kullanan istemciler bağlanamaz.")) {
      try { await Api.deleteMcpToken(b.dataset.delToken); loadAgentPage(); } catch (err) { toast(err.message, true); }
    }
  };
  $("#mcpConnAdd").onclick = () => connModal(null);
  $("#mcpConns").onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.connEdit) connModal(b.dataset.connEdit);
    if (b.dataset.connTest) {
      b.disabled = true;
      try { const t = await Api.testMcpConnection(b.dataset.connTest); toast(t.ok ? `Bağlandı · ${t.tools.length} araç` : t.error, !t.ok); loadAgentPage(); }
      catch (err) { toast(err.message, true); } finally { b.disabled = false; }
    }
    if (b.dataset.connDel && confirm("Bağlantı silinsin mi?")) {
      try { await Api.deleteMcpConnection(b.dataset.connDel); loadAgentPage(); } catch (err) { toast(err.message, true); }
    }
  };
  $("#agentForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await Api.saveAgentSettings(readForm(e.target)); toast("Ajan ayarları kaydedildi"); loadAgentPage(); checkHealth(); }
    catch (err) { toast(err.message, true); }
  };
  $("#convRefresh").onclick = loadAgentPage;
  $("#convTable").onclick = (e) => { const tr = e.target.closest("[data-conv]"); if (tr) showConversation(tr.dataset.conv); };
}

/* ================================================================== */
/*  İşlem kayıtları                                                    */
/* ================================================================== */
State.auditDays = 7;
const SOURCE_LABEL = { "arayüz": ["Arayüz", ""], api: ["API", "gold"], mcp: ["MCP", "blue"] };

async function loadAudit() {
  const to = new Date(), from = new Date();
  from.setDate(to.getDate() - (State.auditDays - 1));
  try {
    const r = await Api.audit({ from: isoDay(from), to: isoDay(to), user: $("#auditUser").value, q: $("#auditQ").value.trim(), failed: $("#auditFailed").checked ? "1" : "" });
    State.audit = r;
    const us = $("#auditUser"), cur = us.value;
    us.innerHTML = `<option value="">Tüm kullanıcılar</option>` + r.users.map((u) => `<option>${esc(u)}</option>`).join("");
    us.value = r.users.includes(cur) ? cur : "";
    $("#auditCount").textContent = `${r.total} kayıt${r.failed ? ` · ${r.failed} hatalı` : ""}`;
    $("#auditTable").innerHTML = `<tr><th>Zaman</th><th>Kullanıcı</th><th>Kaynak</th><th>İşlem</th><th>Ayrıntı</th><th>Sonuç</th><th class="num">Süre</th></tr>` +
      (r.records.map((x, i) => {
        const [sl, sc] = SOURCE_LABEL[x.source] || [x.source, ""];
        const det = Object.entries(x.detail || {}).map(([k, v]) => `<span class="kvp"><span class="muted">${esc(k)}</span> ${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span>`).join(" ");
        return `<tr class="clickable" data-audit="${i}"><td class="mono t-sub">${esc(fmtTime(x.time))}</td>
          <td><div class="t-name">${esc(x.user)}</div><div class="t-sub mono">${esc(x.ip || "")}</div></td>
          <td><span class="label ${sc}">${esc(sl)}</span></td><td>${esc(x.action)}</td><td class="ev-cell sm">${det || "—"}</td>
          <td>${x.ok ? `<span class="status-ok">✓ ${x.status}</span>` : `<span class="status-err" title="${esc(x.error || "")}">✕ ${x.status ?? ""}</span>${x.error ? `<div class="t-sub status-err">${esc(x.error.slice(0, 120))}</div>` : ""}`}</td>
          <td class="num t-sub">${fmtDur(x.durationMs)}</td></tr>`;
      }).join("") || `<tr><td colspan="7" class="muted">Bu aralıkta işlem kaydı yok.</td></tr>`);
  } catch (e) { $("#auditTable").innerHTML = `<tr><td>${errorView(e)}</td></tr>`; }
}

function showAudit(i) {
  const x = State.audit?.records[i];
  if (!x) return;
  modal(x.action, `<dl class="kv">
    <dt>Zaman</dt><dd>${esc(fmtTime(x.time))}</dd><dt>Kullanıcı</dt><dd>${esc(x.user)} · ${esc(x.ip || "")}</dd>
    <dt>Kaynak</dt><dd>${esc(x.source)}</dd><dt>İstek</dt><dd class="mono">${esc(x.method)} ${esc(x.path)}</dd>
    <dt>Sonuç</dt><dd class="${x.ok ? "status-ok" : "status-err"}">${x.status ?? "—"} · ${fmtDur(x.durationMs)}</dd>
    ${x.error ? `<dt>Hata</dt><dd class="status-err">${esc(x.error)}</dd>` : ""}</dl>
    <div class="sub-h">Ayrıntı</div><pre class="code-block">${esc(JSON.stringify(x.detail || {}, null, 2))}</pre>`);
}

function auditCsv() {
  const r = State.audit;
  if (!r) return;
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [["zaman", "kullanici", "ip", "kaynak", "islem", "istek", "ayrinti", "durum", "hata", "sure_ms"].join(";"),
    ...r.records.map((x) => [x.time, x.user, x.ip, x.source, x.action, `${x.method} ${x.path}`, JSON.stringify(x.detail || {}), x.status, x.error, x.durationMs].map(q).join(";"))];
  download(`unixai-islem-kayitlari-${r.from}_${r.to}.csv`, "\ufeff" + lines.join("\n"), "text/csv;charset=utf-8");
}

function bindAudit() {
  $("#auditRange").onclick = (e) => {
    const b = e.target.closest("[data-days]");
    if (!b) return;
    State.auditDays = Number(b.dataset.days);
    $$("#auditRange button").forEach((x) => x.classList.toggle("on", x === b));
    loadAudit();
  };
  let t;
  $("#auditQ").oninput = () => { clearTimeout(t); t = setTimeout(loadAudit, 300); };
  $("#auditUser").onchange = loadAudit;
  $("#auditFailed").onchange = loadAudit;
  $("#auditRefresh").onclick = loadAudit;
  $("#auditCsv").onclick = auditCsv;
  $("#auditTable").onclick = (e) => { const tr = e.target.closest("[data-audit]"); if (tr) showAudit(+tr.dataset.audit); };
}

const SELF_AGENT = "UnixAI ajanı", SELF_SHORT = "UnixAI", PEER_HINT = "MorpheusAI";
const A2A_EMPTY = "Henüz MorpheusAI ajanıyla konuşma yok. UnixAI Ajanı ekranında örneğin: <i>'rhel-db01 P1 bulgusu için MorpheusAI'den playbook iste'</i>";
/* ================================================================== */
/*  Ajanlar arası konuşmalar (yalnızca iki ajanın kendi aralarındaki mesajlar) */
/* ================================================================== */
State.a2a = { peer: "", server: "", sel: null };
const peerTitle = (p) => (/morpheus/i.test(p) ? "MorpheusAI ajanı" : /unixai/i.test(p) ? "UnixAI ajanı" : `${p} ajanı`);

function remCard(x) {
  const st = typeof remTag === "function" ? remTag(x.status) : `<span class="label">${esc(x.status || "")}</span>`;
  const yaml = x.playbook || "";
  if (yaml) State.remYaml[x.id] = yaml;
  return `<div class="a2a-rem">
    <div class="a2a-rem-h"><b>${esc(x.title || x.id)}</b> ${st} ${x.score != null ? `<span class="label ${x.score >= 85 ? "green" : x.score >= 60 ? "gold" : "red"}">uyum ${x.score}</span>` : ""}
      ${x.risk ? `<span class="label">risk: ${esc(x.risk)}</span>` : ""}${x.disruptive ? `<span class="label gold">kesinti riskli adım</span>` : ""}
      <span class="a2a-rem-meta mono">${esc(x.id)}${x.target ? ` · hedef ${esc(x.target)}` : ""}</span></div>
    ${x.summary ? `<p class="muted sm">${esc(x.summary)}</p>` : ""}
    ${x.violations?.length ? `<div class="sm status-err">Standart ihlalleri: ${x.violations.map(esc).join(" · ")}</div>` : ""}
    ${yaml ? `<div class="fix"><pre class="yaml">${esc(yaml)}</pre><button class="btn link sm copy" data-copy-yaml="${esc(x.id)}">YAML kopyala</button></div>` : `<p class="muted sm">YAML bu kayıtta yok.</p>`}
    <div class="toolbar end">${x.lastJob ? `<span class="muted sm">Son AAP işi #${esc(x.lastJob.id)} ${esc(x.lastJob.mode)} · ${esc(x.lastJob.status)}</span>` : ""}
      ${x.approval ? `<span class="label blue">Onaylayan: ${esc(x.approval.by)}</span>` : ""}
    </div>
    ${x.id ? remLiveBox(x.id) : ""}
  </div>`;
}

function a2aTranscript(r) {
  const out = r.direction === "giden";
  const asker = out ? SELF_AGENT : peerTitle(r.peer), answerer = out ? peerTitle(r.peer) : SELF_AGENT;
  const rems = r.remediations || [];
  State.a2aYaml = Object.fromEntries(rems.map((x) => [x.id, x.playbook || ""]));
  return `<div class="a2a-head"><span class="label ${out ? "blue" : "green"}">${out ? "giden" : "gelen"}</span>
      <b>${esc(asker)}</b> → <b>${esc(answerer)}</b>${r.server ? ` · sunucu <code>${esc(r.server)}</code>` : ""}
      <span class="muted sm">${esc(fmtTime(r.time))}${r.durationMs != null ? ` · ${fmtDur(r.durationMs)}` : ""}${r.user ? ` · başlatan ${esc(r.user)}` : ""}</span></div>
    <div class="a2a-chat">
      <div class="a2a-msg ${out ? "self" : "peer"}"><div class="a2a-who">${esc(asker)}</div><div class="bubble">${esc(r.question).replace(/\n/g, "<br>")}</div></div>
      <div class="a2a-msg ${out ? "peer" : "self"}"><div class="a2a-who">${esc(answerer)}</div>
        <div class="bubble ${r.ok ? "" : "error"}">${r.ok ? md(r.answer || "") : `<b>Hata:</b> ${esc(r.error || "")}`}
        ${r.tools?.length ? `<div class="muted sm a2a-tools">Kullandığı araçlar: ${r.tools.map((t) => `<code>${esc(t)}</code>`).join(" ")}</div>` : ""}</div></div>
    </div>
    ${rems.length ? `<div class="sub-h">Hazırlanan playbook'lar (${rems.length})</div>${rems.map(remCard).join("")}` : ""}`;
}

function a2aList(items) {
  return items.map((x) => `<div class="a2a-item ${State.a2a.sel === x.id ? "on" : ""} ${x.ok ? "" : "err"}" data-a2a="${esc(x.id)}">
      <div class="a2a-item-h"><span class="a2a-dir">${x.direction === "giden" ? `${esc(SELF_SHORT)} → ${esc(x.peer)}` : `${esc(x.peer)} → ${esc(SELF_SHORT)}`}</span>
        ${x.server ? `<code>${esc(x.server)}</code>` : ""}<span class="muted sm">${esc(fmtTime(x.time))}</span></div>
      <div class="a2a-q">${esc(x.question)}</div>
      ${x.remediations?.length ? `<div class="a2a-badges">${x.remediations.map((m) => `<span class="label ${m.score >= 85 ? "green" : "gold"}">playbook${m.score != null ? ` · ${m.score}` : ""}</span>`).join(" ")}</div>` : ""}
    </div>`).join("") || `<div class="empty sm">Henüz ajanlar arası konuşma yok.<br>Ajan ekranında karşı ajana iş verin (ör. "…için ${esc(PEER_HINT)}'den playbook iste").</div>`;
}

async function loadA2A(targetList = "#a2aList", targetDetail = "#a2aDetail", server = State.a2a.server) {
  try {
    const r = await Api.a2a({ server, peer: State.a2a.peer });
    if ($("#a2aPeer")) {
      const ps = $("#a2aPeer"), cur = ps.value;
      ps.innerHTML = `<option value="">Tüm ajanlar</option>` + r.peers.map((p) => `<option>${esc(p)}</option>`).join("");
      ps.value = r.peers.includes(cur) ? cur : "";
    }
    $(targetList).innerHTML = a2aList(r.items);
    $(targetList).dataset.detail = targetDetail;
    const first = r.items.find((x) => x.id === State.a2a.sel) || r.items[0];
    if (first) showA2A(first.id, targetList, targetDetail);
    else $(targetDetail).innerHTML = `<div class="empty">${A2A_EMPTY}</div>`;
  } catch (e) { $(targetList).innerHTML = errorView(e); }
}

async function showA2A(id, targetList = "#a2aList", targetDetail = "#a2aDetail") {
  State.a2a.sel = id;
  $$(`${targetList} .a2a-item`).forEach((el) => el.classList.toggle("on", el.dataset.a2a === id));
  $(targetDetail).innerHTML = loadingView("Yükleniyor…");
  try {
    const r = await Api.a2aItem(id);
    $(targetDetail).innerHTML = a2aTranscript(r);
    hydrateRem($(targetDetail));
    $$("[data-copy-yaml]", $(targetDetail)).forEach((b) => (b.onclick = () => copyText(State.a2aYaml[b.dataset.copyYaml] || "")));
  } catch (e) { $(targetDetail).innerHTML = errorView(e); }
}

function bindA2A() {
  document.addEventListener("click", (e) => {
    const it = e.target.closest("[data-a2a]");
    if (!it) return;
    const list = it.parentElement;
    showA2A(it.dataset.a2a, `#${list.id}`, list.dataset.detail || "#a2aDetail");
  });
  if ($("#a2aPeer")) $("#a2aPeer").onchange = (e) => { State.a2a.peer = e.target.value; loadA2A(); };
  if ($("#a2aServer")) {
    let t;
    $("#a2aServer").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { State.a2a.server = e.target.value.trim(); loadA2A(); }, 300); };
  }
  if ($("#a2aRefresh")) $("#a2aRefresh").onclick = () => loadA2A();
}

function bindEvents() {
  addEventListener("hashchange", route);
  $("#menuBtn").onclick = () => $("#sidebar").classList.toggle("open");
  $("#modalClose").onclick = closeModal;
  // Not: onclick işleyicisi false döndürürse tarayıcı tıklamayı iptal eder (modal içindeki submit düğmeleri çalışmaz)
  $("#modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };
  addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModal(); closePalette(); } });

  document.addEventListener("click", (e) => {
    const tr = e.target.closest("[data-href]");
    if (tr && !e.target.closest("a, button")) { closeModal(); location.hash = tr.dataset.href; }
    const lg = e.target.closest("tr[data-log]");
    if (lg) showLog(lg.dataset.log);
    const rn = e.target.closest("tr[data-run]");
    if (rn) showRun(rn.dataset.run);
  });

  // genel bakış
  $("#srvFilter").oninput = renderServers;
  $("#srvStatus").onclick = (e) => {
    const b = e.target.closest("[data-st]");
    if (!b) return;
    State.srvStatus = b.dataset.st;
    renderServers();
  };
  $("#srvView").onclick = (e) => {
    const b = e.target.closest("[data-view]");
    if (!b) return;
    State.srvView = b.dataset.view;
    try { localStorage.setItem("unixai.srvView", State.srvView); } catch {}
    renderServers();
  };
  $("#srvTiles").addEventListener("keydown", (e) => {
    const t = e.target.closest(".tile[data-href]");
    if (t && e.key === "Enter") location.hash = t.dataset.href;
  });
  $$(".settings-nav a").forEach((a) => (a.onclick = (e) => {
    e.preventDefault();
    document.getElementById(a.dataset.anchor)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  bindPalette();
  bindAgentPage();
  bindAudit();
  bindA2A();
  bindApprovals();
  $("#dashCollect").onclick = (e) => startRun("collect", e.target);
  $("#dashReanalyze").onclick = (e) => startRun("reanalyze", e.target);

  // sunucu detayı
  $("#sdBody").addEventListener("click", (e) => {
    const t = e.target.closest("[data-tab]");
    if (!t) return;
    State.sd.tab = t.dataset.tab;
    $$("#sdTabs button").forEach((b) => b.classList.toggle("on", b === t));
    renderServerTab();
  });
  $("#sdRun").onchange = (e) => loadServer(State.sd.name, e.target.value === State.sd.hist[0]?.runId ? null : e.target.value);
  $("#sdCollect").onclick = async (e) => {
    const name = State.sd.name;
    if (!State.inventory.length) try { State.inventory = (await Api.servers()).servers; } catch {}
    if (!State.inventory.some((s) => s.name === name)) return toast("Bu sunucu envanterde değil (demo). Sunucular ekranından ekleyin.", true);
    e.target.disabled = true;
    try { const r = await Api.startRun({ mode: "collect", servers: [name], ai: true }); toast(`${name} toplanıyor…`); pollRun(r.id); }
    catch (err) { toast(err.message, true); }
    finally { e.target.disabled = false; }
  };

  // çalıştırmalar
  $("#runServers").onclick = (e) => {
    const b = e.target.closest("[data-pick]");
    if (!b) return;
    const n = b.dataset.pick;
    State.runSelect.has(n) ? State.runSelect.delete(n) : State.runSelect.add(n);
    renderRunServers();
  };
  $("#runCollect").onclick = (e) => startRun("collect", e.target);
  $("#runReanalyze").onclick = (e) => startRun("reanalyze", e.target);
  $("#runDemo").onclick = (e) => startRun("demo", e.target);

  // gelen loglar
  $("#colRefresh").onclick = loadCollected;
  $("#colTable").onclick = (e) => {
    const c = e.target.closest("[data-raw]");
    if (c) showRawSection(c.dataset.raw, c.dataset.run, c.dataset.sec);
  };

  // sohbet
  const ct = $("#chatText");
  $("#chatForm").onsubmit = (e) => { e.preventDefault(); const v = ct.value; ct.value = ""; ct.style.height = ""; sendChat(v); };
  ct.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chatForm").requestSubmit(); }
  });
  ct.addEventListener("input", () => { ct.style.height = "auto"; ct.style.height = ct.scrollHeight + "px"; });
  $("#chatSuggest").innerHTML = SUGGESTIONS.map((s) => `<button type="button">${esc(s)}</button>`).join("");
  $("#chatSuggest").onclick = (e) => { if (e.target.matches("button")) sendChat(e.target.textContent); };
  $("#chatClear").onclick = resetChat;

  // envanter
  $("#invAdd").onclick = () => serverModal(null);
  $("#invImport").onclick = importModal;
  $("#invTable").onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.edit) serverModal(b.dataset.edit);
    if (b.dataset.test) {
      modal(`SSH testi · ${b.dataset.test}`, `<div id="sshTestOut"></div>`);
      runSshTest(b.dataset.test, null, $("#sshTestOut"));
    }
    if (b.dataset.del && confirm(`${b.dataset.del} envanterden silinsin mi? (Toplanmış geçmiş veriler silinmez)`)) {
      try { await Api.deleteServer(b.dataset.del); toast("Silindi"); loadInventory(); } catch (err) { toast(err.message, true); }
    }
  };

  // loglar
  $("#logRefresh").onclick = loadLogs;
  $("#logAuto").onchange = (e) => setLogAuto(e.target.checked);

  // token kullanımı
  $("#usageRange").onclick = (e) => {
    const b = e.target.closest("[data-days]");
    if (!b) return;
    setUsageRange(Number(b.dataset.days));
    loadUsage();
  };
  ["#usageFrom", "#usageTo"].forEach((id) => ($(id).onchange = () => {
    $$("#usageRange button").forEach((b) => b.classList.remove("on"));
    loadUsage();
  }));
  $("#usageUser").onchange = loadUsage;
  $("#usageKind").onchange = loadUsage;
  $("#usageCsv").onclick = usageCsv;
  $("#usageTable").onclick = (e) => {
    const tr = e.target.closest("[data-usage]");
    if (tr) showUsageRecord(+tr.dataset.usage);
  };

  // oturum
  $("#userChip").onclick = userMenu;
  $("#loginForm").onsubmit = submitLogin;
  addEventListener("unixai:auth-required", async () => {
    if (!$("#loginScreen").classList.contains("hidden")) return;
    await showLogin();
    location.reload();
  });

  // ayarlar
  $("#connForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await saveConnection(); toast("GenAI bağlantısı kaydedildi"); $("#connResult").innerHTML = ""; }
    catch (err) { $("#connResult").innerHTML = errorView(err); }
  };
  $("#connTestToken").onclick = (e) => testConnection("token", e.target);
  $("#morForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await saveMorpheus(); toast("MorpheusAI entegrasyonu kaydedildi"); $("#morResult").innerHTML = ""; }
    catch (err) { $("#morResult").innerHTML = errorView(err); }
  };
  $("#morTest").onclick = async (e) => {
    const b = e.currentTarget, out = $("#morResult");
    b.disabled = true;
    try {
      await saveMorpheus();
      out.innerHTML = loadingView("MorpheusAI'ye bağlanılıyor…");
      const r = await Api.testMorpheus();
      out.innerHTML = r.ok
        ? `<div class="alert info"><b class="status-ok">✓ MorpheusAI bağlantısı başarılı</b> · mod: ${esc(r.mode)} · AAP: ${r.aap ? `bağlı (runner #${esc(r.runnerTemplate || "tanımsız")})` : "bağlı değil"} · ${r.requireCheck ? "önce check zorunlu" : "check opsiyonel"} · ${r.autoApprove ? "otomatik onay AÇIK" : "onay gerekli"} · ${r.remediations} kayıt</div>`
        : `<div class="alert danger"><b>✕ Bağlanılamadı:</b> ${esc(r.error)}</div>`;
    } catch (err) { out.innerHTML = errorView(err); }
    finally { b.disabled = false; }
  };
  $("#connTestChat").onclick = (e) => testConnection("chat", e.target);
  $("#sshForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const f = readForm(e.target);
      State.settings = await Api.saveSettings(f);
      toast("Ayarlar kaydedildi");
      checkHealth();
    } catch (err) { toast(err.message, true); }
  };
  $("#thrForm").onsubmit = async (e) => {
    e.preventDefault();
    try { State.settings = await Api.saveSettings(readForm(e.target)); toast("Eşikler kaydedildi — sonraki analizde geçerli"); }
    catch (err) { toast(err.message, true); }
  };
  $("#thrReset").onclick = async () => {
    const s = await Api.settings();
    renderThresholds(s.defaults.thresholds);
    toast("Varsayılan eşikler yüklendi — kalıcı olması için Kaydet'e basın");
  };
  $("#limitForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await Api.saveLimits({ dailyLimit: Number(e.target.elements.dailyLimit.value || 0), perUserDailyLimit: Number(e.target.elements.perUserDailyLimit.value || 0) });
      toast("Limitler kaydedildi");
      loadLimits();
      refreshTokenChip();
    } catch (err) { toast(err.message, true); }
  };
  $("#promptForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const p = await Api.savePrompts(e.target.elements.system.value);
      State.prompts = { ...State.prompts, ...p };
      $("#promptInfo").textContent = `Son kayıt: ${fmtTime(p.updatedAt)}`;
      toast("Sistem mesajı kaydedildi");
    } catch (err) { toast(err.message, true); }
  };
  $("#promptReset").onclick = () => {
    if (!State.prompts?.defaults) return;
    $("#promptForm").elements.system.value = State.prompts.defaults.system;
    toast("Varsayılan metin yüklendi — kalıcı olması için Kaydet'e basın");
  };
  $("#settingsForm").onsubmit = (e) => {
    e.preventDefault();
    Settings.save({ userName: e.target.elements.userName.value.trim() });
    renderUserChip();
    refreshTokenChip();
    $("#settingsMsg").textContent = "Kaydedildi";
    setTimeout(() => ($("#settingsMsg").textContent = ""), 2000);
  };
}

(async function init() {
  hydrateIcons();
  $("#contract").textContent = API_CONTRACT;
  $("#settingsForm").elements.userName.value = Settings.get().userName;
  bindEvents();
  resetChat();

  // Giriş açıksa (LDAP veya yerel yönetici) önce giriş
  try { State.auth = await Api.me(); }
  catch (e) { State.auth = e.data || { enabled: false }; }
  if (State.auth.enabled && !State.auth.user) State.auth.user = await showLogin();
  applyAuthUi();

  await checkHealth();
  try { State.settings = await Api.settings(); } catch {}
  refreshTokenChip();
  setInterval(refreshTokenChip, 60000);
  refreshApprovals();
  setInterval(refreshApprovals, 60000);
  try {
    State.overview = await Api.overview();
    setRunChip(State.overview.lastRun);
    if (State.overview.lastRun?.status === "running") pollRun(State.overview.lastRun.id);
  } catch {}
  route();

})();
