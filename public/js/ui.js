/* UnixAI OS - ortak UI yardımcıları */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const SEV_ORDER = ["critical", "high", "medium", "low", "info"];
const SEV_LABEL = { critical: "Kritik", high: "Yüksek", medium: "Orta", low: "Düşük", info: "Bilgi" };
const STATUS_LABEL = { compliant: "Uyumlu", warning: "İyileştirme gerekli", non_compliant: "Uyumsuz" };

const scoreColor = (s) => (s >= 85 ? "var(--ok)" : s >= 60 ? "var(--warn)" : "var(--crit)");

/* Çizgi ikon seti (CSP uyumlu satır içi SVG) */
const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  logs: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  servers: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M2 20h20"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h6"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  theme: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  terminal: '<path d="m4 17 6-5-6-5M12 19h8"/>',
  bolt: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
  sparkles: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  flask: '<path d="M9 3h6M10 3v6L4 19a1 1 0 0 0 .9 1.5h14.2A1 1 0 0 0 20 19l-6-10V3"/><path d="M7 14h10"/>',
  shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/>',
  check: '<path d="m5 12 5 5L20 7"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  disk: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
};
const icon = (name, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
function hydrateIcons(root = document) {
  $$("svg[data-icon]", root).forEach((el) => {
    el.setAttribute("viewBox", "0 0 24 24");
    el.setAttribute("aria-hidden", "true");
    el.innerHTML = ICONS[el.dataset.icon] || "";
  });
}

function gauge(score, { size = 112, label = "sağlık skoru", stroke = 9 } = {}) {
  const r = size / 2 - stroke, c = 2 * Math.PI * r, off = c * (1 - score / 100);
  return `<div class="gauge" style="width:${size}px;height:${size}px">
    <svg width="${size}" height="${size}">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="${stroke}"/>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${scoreColor(score)}" stroke-width="${stroke}"
        stroke-dasharray="${c}" stroke-dashoffset="${c}" style="transition:stroke-dashoffset .8s ease" data-off="${off}"/>
    </svg>
    <div class="g-val"><div class="g-num">${score}</div><div class="g-lbl">${label}</div></div>
  </div>`;
}
function animateGauges(root = document) {
  requestAnimationFrame(() => requestAnimationFrame(() => $$("circle[data-off]", root).forEach((el) => (el.style.strokeDashoffset = el.dataset.off))));
}

const scoreBar = (s) =>
  s == null
    ? `<span class="muted sm">Analiz edilmedi</span>`
    : `<div class="scorebar"><div class="bar"><i style="width:${s}%;background:${scoreColor(s)}"></i></div><b>${s}</b></div>`;

const sevTag = (s) => `<span class="label sev-${s}">${SEV_LABEL[s] || esc(s)}</span>`;

/* Minimal & güvenli markdown (önce escape, sonra biçimlendirme) */
function md(src) {
  const blocks = [];
  let s = String(src ?? "").replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push(`<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  s = esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])_([^_]+)_(?=[\s).,!?:]|$)/g, "$1<i>$2</i>")
    .replace(/(^|[\s(])\*([^*]+)\*(?=[\s).,!?:]|$)/g, "$1<i>$2</i>");

  const out = [];
  let list = null;
  for (const line of s.split("\n")) {
    const ul = line.match(/^\s*[-*]\s+(.*)/), ol = line.match(/^\s*\d+\.\s+(.*)/);
    const item = ul || ol;
    if (item) {
      const tag = ul ? "ul" : "ol";
      if (list !== tag) { if (list) out.push(`</${list}>`); out.push(`<${tag}>`); list = tag; }
      out.push(`<li>${item[1]}</li>`);
      continue;
    }
    if (list) { out.push(`</${list}>`); list = null; }
    if (/^&gt;\s?/.test(line)) out.push(`<blockquote>${line.replace(/^&gt;\s?/, "")}</blockquote>`);
    else if (/^#{1,4}\s/.test(line)) out.push(`<p class="md-h">${line.replace(/^#+\s/, "")}</p>`);
    else if (line.trim()) out.push(`<p>${line}</p>`);
  }
  if (list) out.push(`</${list}>`);
  return out.join("").replace(/<p>\u0000(\d+)\u0000<\/p>|\u0000(\d+)\u0000/g, (_, a, b) => blocks[a ?? b]);
}

let toastTimer;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast show${isErr ? " err" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), 3000);
}

function modal(title, html, { wide = false } = {}) {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = html;
  $("#modal .modal-box").classList.toggle("wide", wide);
  $("#modal").classList.remove("hidden");
}
function closeModal() { $("#modal").classList.add("hidden"); }

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Panoya kopyalandı"); }
  catch { toast("Kopyalanamadı", true); }
}

function download(name, content, type = "application/json") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([content], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

const fmtTime = (iso) => {
  try { return new Date(iso).toLocaleString("tr-TR", { hour12: false }); } catch { return iso; }
};
