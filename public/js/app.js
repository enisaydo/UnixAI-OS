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
};

const STATUS_META = {
  OK: { label: "Sağlıklı", cls: "green", icon: "✓" },
  WARNING: { label: "Uyarı", cls: "gold", icon: "!" },
  CRITICAL: { label: "Kritik", cls: "red", icon: "✕" },
};
const statusTag = (s) => {
  const m = STATUS_META[s];
  return m ? `<span class="label ${m.cls}"><b aria-hidden="true">${m.icon}</b> ${m.label}</span>` : `<span class="label">—</span>`;
};
const prioTag = (p) => `<span class="label prio-${esc(p)}">${esc(p)}</span>`;
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
const ROUTES = ["dashboard", "server", "runs", "collected", "ask", "servers", "usage", "logs", "settings"];

function route() {
  const [name, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  const arg = rest.length ? decodeURIComponent(rest.join("/")) : null;
  const r = ROUTES.includes(name) ? name : "dashboard";
  $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === r));
  $$(".sidebar a").forEach((a) => a.classList.toggle("active", a.dataset.route === (r === "server" ? "dashboard" : r)));
  $("#sidebar").classList.remove("open");
  if (r === "dashboard") loadOverview();
  if (r === "server" && rest[0]) {
    if (rest[1]) State.sd.tab = rest[1];
    loadServer(decodeURIComponent(rest[0]));
  }
  if (r === "runs") loadRuns();
  if (r === "collected") loadCollected();
  if (r === "ask") { renderChatContext(arg); setTimeout(() => $("#chatText").focus(), 50); }
  if (r === "servers") loadInventory();
  if (r === "logs") loadLogs();
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
    $("#kpis").innerHTML = errorView(e);
  }
}

function renderOverview() {
  const o = State.overview;
  const withData = o.servers.filter((s) => s.latest && s.latest.collect !== "failed");
  const crit = withData.filter((s) => s.latest.status === "CRITICAL").length;
  const warn = withData.filter((s) => s.latest.status === "WARNING").length;
  const failed = o.servers.filter((s) => s.latest?.collect === "failed").length;
  const p1 = withData.reduce((n, s) => n + (s.latest.p1 || 0), 0);
  const lr = o.lastRun;
  $("#modeLabel").className = `label ${o.mode === "genai" ? "blue" : "gold"}`;
  $("#modeLabel").textContent = o.mode === "genai" ? "GenAI analizi açık" : "Demo modu (GenAI bağlı değil)";
  $("#kpis").innerHTML = [
    `<div class="kpi"><div class="k-label">Sunucular</div><div class="k-val">${o.servers.length}</div>
      <div class="k-sub">${o.servers.filter((s) => s.inInventory).length} envanterde · ${withData.length} analiz edildi</div></div>`,
    `<div class="kpi kpi-crit"><div class="k-label">Kritik / Uyarı</div><div class="k-val">${crit} <span class="k-slash">/ ${warn}</span></div>
      <div class="k-sub">${failed ? `${failed} sunucuya ulaşılamadı` : "toplama hatası yok"}</div></div>`,
    `<div class="kpi kpi-warn"><div class="k-label">P1 aksiyonlar</div><div class="k-val">${p1}</div><div class="k-sub">hemen yapılması gereken</div></div>`,
    `<div class="kpi"><div class="k-label">Son toplama</div><div class="k-val sm">${lr ? esc(ago(lr.startedAt)) : "—"}</div>
      <div class="k-sub">${lr ? `${esc(TRIGGER_LABEL[lr.trigger] || lr.trigger)} · ${esc(RUN_STATUS[lr.status]?.[0] || lr.status)}` : "henüz yok"}${o.scheduler.enabled ? ` · her gün ${esc(o.scheduler.time)}` : ""}</div></div>`,
  ].join("");

  const f = o.fleet;
  $("#fleetCard").innerHTML = !o.servers.length
    ? `<div class="empty"><h4>Henüz sunucu yok</h4><p><a href="#/servers">Sunucular</a> ekranından RHEL sunucularınızı ekleyin veya
        <a href="#/runs">Toplama &amp; Analiz</a> ekranında <b>Demo veri üret</b> ile deneyin.</p></div>`
    : !f ? `<div class="card-head"><h3>Filo özeti</h3></div><p class="muted">Filo özeti en az iki sunucu analiz edildiğinde üretilir.</p>`
    : `<div class="card-head"><h3>Filo özeti</h3><span class="muted sm">${esc(ENGINE_LABEL[f.engine] || f.engine)} · ${esc(fmtTime(f.time))}</span></div>
       ${f.aiError ? `<div class="alert danger">GenAI filo özeti alınamadı, kural özeti gösteriliyor: ${esc(f.aiError)}</div>` : ""}
       <p>${esc(f.fleet_summary)}</p>
       <div class="grid-2">
         <div><h4 class="sub-h">Bugünün öncelikli işleri</h4>
           <ol class="top-actions">${f.todays_top_actions.map((a) => `<li><a href="#/server/${encodeURIComponent(a.server)}"><b>${esc(a.server)}</b></a> — ${esc(a.title)}
             ${a.why_now ? `<div class="muted sm">${esc(a.why_now)}</div>` : ""}</li>`).join("") || "<li class='muted'>—</li>"}</ol></div>
         <div><h4 class="sub-h">Sunucular arası örüntüler</h4>
           <ul class="patterns">${f.cross_server_patterns.map((p) => `<li>${esc(p)}</li>`).join("") || "<li class='muted'>—</li>"}</ul></div>
       </div>`;
  renderServerTable();
}

function renderServerTable() {
  const q = $("#srvFilter").value.toLowerCase();
  const order = { CRITICAL: 0, WARNING: 1, OK: 2 };
  const rows = State.overview.servers
    .filter((s) => !q || `${s.name} ${s.host} ${s.tags.join(" ")}`.toLowerCase().includes(q))
    .sort((a, b) => (order[a.latest?.status] ?? (a.latest?.collect === "failed" ? -1 : 3)) - (order[b.latest?.status] ?? (b.latest?.collect === "failed" ? -1 : 3)) || a.name.localeCompare(b.name));
  const th = State.settings?.thresholds || { fs_warn_pct: 85, fs_crit_pct: 95 };
  $("#srvTable").innerHTML = `<tr><th>Sunucu</th><th>Durum</th><th>Sağlık</th><th>En dolu disk</th><th class="num">Load/core</th><th class="num">Boş bellek</th><th class="num">P1</th><th>Son toplama</th></tr>` +
    (rows.map((s) => {
      const l = s.latest, h = l?.headline || {};
      return `<tr class="clickable" data-href="#/server/${encodeURIComponent(s.name)}">
        <td><div class="t-name">${esc(s.name)}</div><div class="t-sub">${esc(s.host || "")}${h.os ? ` · ${esc(h.os)}` : ""}
          ${s.demo ? ` <span class="label">demo</span>` : ""}${s.tags.map((t) => ` <span class="label">${esc(t)}</span>`).join("")}${s.inInventory && !s.enabled ? ` <span class="label">pasif</span>` : ""}</div></td>
        <td>${!l ? `<span class="muted sm">Toplanmadı</span>` : l.collect === "failed" ? `<span class="label red" title="${esc(l.error)}"><b>✕</b> Ulaşılamadı</span>` : statusTag(l.status)}</td>
        <td>${l?.score != null ? scoreBar(l.score) : `<span class="muted sm">—</span>`}</td>
        <td>${h.fs_max_pct != null ? `${pctCell(h.fs_max_pct, th.fs_warn_pct, th.fs_crit_pct)} <span class="t-sub mono">${esc(h.fs_max_mount)}</span>` : "—"}</td>
        <td class="num">${h.load_per_core ?? "—"}</td>
        <td class="num">${h.mem_available_pct != null ? `%${h.mem_available_pct}` : "—"}</td>
        <td class="num">${l?.p1 ? `<b class="status-err">${l.p1}</b>` : l?.p1 === 0 ? "0" : "—"}</td>
        <td class="t-sub">${l ? `${esc(ago(l.collectedAt))}<br>${esc(ENGINE_LABEL[l.engine] || "")}` : "—"}</td></tr>`;
    }).join("") || `<tr><td colspan="8" class="muted">Eşleşen sunucu yok.</td></tr>`);
}

/* ================================================================== */
/*  Sunucu detayı                                                      */
/* ================================================================== */
async function loadServer(name, run) {
  State.sd = { ...State.sd, name, run: run || null };
  $("#sdName").textContent = name;
  $("#sdSub").textContent = "";
  $("#sdAsk").href = `#/ask/${encodeURIComponent(name)}`;
  $("#sdBody").innerHTML = loadingView("Yükleniyor…");
  const [snap, hist] = await Promise.allSettled([Api.snapshot(name, run), Api.serverHistory(name)]);
  State.sd.hist = hist.status === "fulfilled" ? hist.value : [];
  $("#sdRun").innerHTML = State.sd.hist.map((h) => `<option value="${esc(h.runId)}">${esc(fmtTime(h.time))} · ${h.collect === "failed" ? "ulaşılamadı" : `skor ${h.score ?? "—"}`}</option>`).join("");
  if (snap.status === "rejected") {
    const last = State.sd.hist[0];
    $("#sdBody").innerHTML = `<div class="card">${last?.error ? `<div class="alert danger"><b>Son toplama başarısız:</b> ${esc(last.error)}</div>` : ""}
      <div class="empty"><h4>Bu sunucu için analiz yok</h4><p>${esc(snap.reason.message)}</p></div></div>`;
    return;
  }
  const s = State.sd.snap = snap.value;
  $("#sdRun").value = s.run.id;
  const m = s.metrics;
  $("#sdSub").innerHTML = `${esc(s.host || "")} · ${esc(m.os_release || "")} · kernel ${esc(m.kernel)} · uptime ${m.uptime_days} gün · ${m.cpu_count} CPU · ${fmtMb(m.memory.mem_total_mb)} RAM`;
  renderServer();
}

function renderServer() {
  const s = State.sd.snap, a = s.analysis, last = State.sd.hist[0];
  const counts = ["critical", "high", "medium", "low"].map((k) => [k, a.anomalies.filter((x) => x.severity === k).length]).filter(([, n]) => n);
  const tabs = [
    ["actions", `Aksiyonlar (${a.action_items.length})`], ["anomalies", `Anomaliler (${a.anomalies.length})`],
    ["rules", `Kural bulguları (${s.findings.length})`], ["metrics", "Metrikler"], ["trend", "Trend"], ["raw", "Ham loglar"], ["ai", "AI çağrısı"],
  ];
  $("#sdBody").innerHTML = `
    ${last && last.runId !== s.run.id && !State.sd.run && last.collect === "failed" ? `<div class="alert danger"><b>Son toplama başarısız (${esc(fmtTime(last.time))}):</b> ${esc(last.error)} — aşağıda son başarılı veri gösteriliyor.</div>` : ""}
    <div class="card">
      <div class="result-head">
        ${gauge(a.health_score)}
        <div>
          <div>${statusTag(a.status)} <span class="engine">${esc(ENGINE_LABEL[a.engine] || a.engine)}${a.model ? ` · ${esc(a.model)}` : ""} · ${esc(fmtTime(s.collectedAt))} · ${esc(TRIGGER_LABEL[s.run.trigger] || "")}</span></div>
          <h2>${esc(s.server)}</h2>
          <p class="summary">${esc(a.summary)}</p>
          <div class="sev-summary">${counts.map(([k, n]) => `${sevTag(k)} <b>${n}</b>`).join(" ")}</div>
        </div>
      </div>
      ${a.aiError ? `<div class="alert danger"><b>GenAI analizi yapılamadı</b>, kural motoru sonucu gösteriliyor: ${esc(a.aiError)}</div>` : ""}
      ${s.partial ? `<div class="alert info">Toplama eksik tamamlandı; gelmeyen bölümler: ${esc((s.missing || []).join(", ") || "?")}</div>` : ""}
      <div class="tabs" id="sdTabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${State.sd.tab === k ? "on" : ""}">${esc(l)}</button>`).join("")}</div>
      <div id="sdPane"></div>
    </div>`;
  renderServerTab();
}

function renderServerTab() {
  const s = State.sd.snap, a = s.analysis, pane = $("#sdPane");
  const t = State.sd.tab;
  if (t === "actions") {
    pane.innerHTML = a.action_items.map((x, i) => `
      <div class="action prio-${esc(x.priority)}">
        <div class="finding-top">${prioTag(x.priority)} <span class="finding-title">${esc(x.title)}</span>
          <span class="finding-meta"><span>${esc(x.owner)}</span>${x.timeframe && x.timeframe !== "-" ? `<span>${esc(x.timeframe)}</span>` : ""}${x.related_anomalies.length ? `<span>${esc(x.related_anomalies.join(", "))}</span>` : ""}</span></div>
        ${x.rationale && x.rationale !== "-" ? `<p>${esc(x.rationale)}</p>` : ""}
        ${x.commands.length ? `<div class="fix"><pre>${esc(x.commands.join("\n"))}</pre><button class="btn link sm copy" data-copy-cmd="${i}">Kopyala</button></div>` : ""}
        <dl class="kv sm-kv">
          ${x.verification && x.verification !== "-" ? `<dt>Doğrulama</dt><dd>${esc(x.verification)}</dd>` : ""}
          ${x.risk && x.risk !== "-" ? `<dt>Risk</dt><dd>${esc(x.risk)}</dd>` : ""}
        </dl>
      </div>`).join("") || `<div class="empty"><h4>Aksiyon yok</h4><p>Bu sunucuda anomali bulunmadı.</p></div>`;
    pane.querySelectorAll("[data-copy-cmd]").forEach((b) => (b.onclick = () => copyText(a.action_items[+b.dataset.copyCmd].commands.join("\n"))));
  } else if (t === "anomalies") {
    pane.innerHTML = a.anomalies.map((x) => `
      <div class="finding ${esc(x.severity)}">
        <div class="finding-top">${sevTag(x.severity)} <span class="mono">${esc(x.id)}</span> <span class="finding-title">${esc(x.title)}</span>
          <span class="finding-meta"><span>${esc(x.category)}</span></span></div>
        ${x.evidence ? `<pre class="code-block mt-s">${esc(x.evidence)}</pre>` : ""}
        ${x.probable_cause && x.probable_cause !== "-" ? `<p><b>Olası neden:</b> ${esc(x.probable_cause)}</p>` : ""}
      </div>`).join("") || `<div class="empty"><h4>Anomali yok</h4></div>`;
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
      ${a.logId ? `<div class="toolbar mt"><button class="btn secondary" id="sdOpenLog">GPT'ye gönderilen istek ve cevabı göster</button></div>` :
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
    <div class="card-head"><h3>Sürüyor: ${esc(TRIGGER_LABEL[r.trigger] || r.trigger)} <span class="muted sm">${esc(r.id)}</span></h3><span class="muted sm">${done} / ${r.servers.length}</span></div>
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
      <div class="bubble ${m.error ? "error" : ""}">${m.role === "user" ? esc(m.content).replace(/\n/g, "<br>") : md(m.content)}</div></div>`).join("") +
    (State.chatBusy ? `<div class="msg assistant"><div class="avatar">UA</div><div class="bubble typing"><span></span><span></span><span></span></div></div>` : "");
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
  $("#chatSuggest").classList.toggle("hidden", State.chat.length > 1);
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
    State.chat.push({ role: "assistant", content: r.reply });
  } catch (e) {
    State.chat.push({ role: "assistant", content: e.message, error: true });
  } finally {
    State.chatBusy = false;
    renderChat();
    refreshTokenChip();
  }
}

function resetChat() {
  State.chat = [{ role: "assistant", intro: true, content: "Merhaba, ben **UnixAI**. Bir sunucu veya filo bağlamı seçip sorun; son toplanan metrik, log ve analizlere göre cevap veririm." }];
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
function bindEvents() {
  addEventListener("hashchange", route);
  $("#menuBtn").onclick = () => $("#sidebar").classList.toggle("open");
  $("#modalClose").onclick = closeModal;
  $("#modal").onclick = (e) => e.target.id === "modal" && closeModal();
  addEventListener("keydown", (e) => e.key === "Escape" && closeModal());

  document.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-href]");
    if (tr) { closeModal(); location.hash = tr.dataset.href; }
    const lg = e.target.closest("tr[data-log]");
    if (lg) showLog(lg.dataset.log);
    const rn = e.target.closest("tr[data-run]");
    if (rn) showRun(rn.dataset.run);
  });

  // genel bakış
  $("#srvFilter").oninput = renderServerTable;
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
  $("#chatSuggest").onclick = (e) => e.target.matches("button") && sendChat(e.target.textContent);
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
  try {
    State.overview = await Api.overview();
    setRunChip(State.overview.lastRun);
    if (State.overview.lastRun?.status === "running") pollRun(State.overview.lastRun.id);
  } catch {}
  route();
  askUserName();
})();
