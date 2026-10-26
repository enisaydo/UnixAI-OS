#!/usr/bin/env node
/*
 * Pixel Office köprüsü — UnixAI OS ve MorpheusAI ajanlarını açık kaynak Pixel Agents ofisinde (MIT) karakter yapar
 *
 *   UnixAI / MorpheusAI canlı akışı (SSE /api/live)  ──►  bu köprü  ──►  Pixel Agents yerel sunucusu (POST /api/hooks/claude)
 *
 * Pixel Agents'ın koduna dokunulmaz: köprü, onun belgelenmiş hook olaylarını gönderir
 * (SessionStart, PreToolUse, PostToolUse, PermissionRequest, Stop). Her ajan ofiste bir karakterdir:
 *   - araç adımı → karakter masasında çalışır (okuma araçları "Reading …", diğerleri "Running: …")
 *   - ajana soru / playbook yazma / toplama → "Subtask: …" (uzun işlerde yanlış izin balonu çıkmasın diye arka plan alt görevi)
 *   - playbook onay bekliyor → izin balonu (PermissionRequest)
 *   - iş bitti → "Done"
 *
 * Kullanım (Node 20+, bağımlılık yok):
 *   UNIXAI_URL=http://unixai.kurum.local:8080 MORPHEUS_URL=http://morpheus.kurum.local:3000 node pixel-bridge.mjs
 *
 * Ortam değişkenleri:
 *   UNIXAI_URL, UNIXAI_TOKEN        UnixAI adresi; giriş açıksa UNIXAI_API_TOKEN değeri (Bearer)
 *   MORPHEUS_URL, MORPHEUS_TOKEN    MorpheusAI adresi (isteğe bağlı); giriş açıksa MORPHEUS_API_TOKEN değeri
 *                                   Yalnızca UnixAI verilirse MorpheusAI'nin düzeltme akışı UnixAI üzerinden gelir.
 *   PIXEL_URL, PIXEL_TOKEN          Pixel Agents sunucusu; boşsa ~/.pixel-agents/servers/*.json'dan bulunur
 *   PIXEL_CWD                       karakterlerin "proje klasörü" öneki (varsayılan /pixel-office)
 *   TLS: kurum içi sertifika için NODE_EXTRA_CA_CERTS=/etc/pki/tls/certs/ca-bundle.crt
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const env = (k, d = "") => process.env[k] || d;
const CWD = env("PIXEL_CWD", "/pixel-office").replace(/\/+$/, "");
const AGENTS = {
  unixai: { session: "unixai-os-ajani", cwd: `${CWD}/UnixAI` },
  morpheus: { session: "morpheusai-ajani", cwd: `${CWD}/MorpheusAI` },
};
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

/* ---------------- Pixel Agents sunucusu ---------------- */
function pixelTargets() {
  if (env("PIXEL_URL")) return [{ url: env("PIXEL_URL").replace(/\/+$/, ""), token: env("PIXEL_TOKEN") }];
  const dir = path.join(os.homedir(), ".pixel-agents");
  const files = [];
  try { for (const f of fs.readdirSync(path.join(dir, "servers"))) if (f.endsWith(".json")) files.push(path.join(dir, "servers", f)); } catch {}
  if (!files.length) files.push(path.join(dir, "server.json"));
  const out = [];
  for (const f of files) {
    try {
      const c = JSON.parse(fs.readFileSync(f, "utf8"));
      if (c.port && c.token) out.push({ url: `http://127.0.0.1:${c.port}`, token: c.token });
    } catch {}
  }
  return out;
}

const started = new Set(); // SessionStart gönderilmiş oturumlar (Pixel yeniden başlarsa sıfırlanır)
async function hook(kind, body) {
  const a = AGENTS[kind];
  const targets = pixelTargets();
  if (!targets.length) { log("! Pixel Agents sunucusu bulunamadı (çalışıyor mu? PIXEL_URL/PIXEL_TOKEN verin)"); return; }
  for (const t of targets) {
    const key = `${t.url}|${a.session}`;
    const send = (b) => fetch(`${t.url}/api/hooks/claude`, {
      method: "POST", headers: { "content-type": "application/json", authorization: `Bearer ${t.token}` },
      body: JSON.stringify({ session_id: a.session, ...b }),
    });
    try {
      if (!started.has(key)) {
        await send({ hook_event_name: "SessionStart", source: "startup", cwd: a.cwd });
        started.add(key);
      }
      const r = await send(body);
      if (r.status === 401) log("! Pixel Agents token reddedildi (PIXEL_TOKEN veya ~/.pixel-agents/servers/*.json)");
    } catch (e) {
      started.delete(key);
      log(`! Pixel Agents'a ulaşılamadı (${t.url}): ${e.message}`);
    }
  }
}

/* ---------------- olay → ofis hareketi ---------------- */
const idleTimers = {};
const busy = { unixai: 0, morpheus: 0 };
function done(kind, ms = 4000) { // adım bitti; yeni adım gelmezse "Done"
  clearTimeout(idleTimers[kind]);
  idleTimers[kind] = setTimeout(() => { if (!busy[kind]) hook(kind, { hook_event_name: "Stop" }); }, ms);
}
const start = (kind, tool_name, tool_input) => { clearTimeout(idleTimers[kind]); busy[kind]++; return hook(kind, { hook_event_name: "PreToolUse", tool_name, tool_input }); };
const end = (kind, failed) => { busy[kind] = Math.max(0, busy[kind] - 1); hook(kind, { hook_event_name: failed ? "PostToolUseFailure" : "PostToolUse" }); done(kind); };
const subtask = (kind, description) => start(kind, "Agent", { description: description.slice(0, 120), run_in_background: true });
const READ_RE = /get_|list_|status|findings|diagnostic|search|query|standards|records|tasks/;

let morpheusDirect = false; // MorpheusAI'ye doğrudan bağlıysak UnixAI üzerinden gelen kopyalar atlanır

function onEvent(src, ev) {
  if (ev.replay) return; // sayfa/köprü açılırken gelen geçmiş olaylar canlandırılmaz
  const peer = src === "unixai" ? "morpheus" : "unixai";
  const name = (k) => (k === "unixai" ? "UnixAI" : "MorpheusAI");
  switch (ev.type) {
    case "step": {
      const tool = String(ev.tool || "").replace(/^.*__/, "");
      if (ev.phase === "start") {
        if (/agent_ask$/.test(tool)) return subtask(src, `${name(peer)}'ye soruyor`);
        if (/create_remediation/.test(tool)) return subtask(src, "Playbook yazıyor");
        if (READ_RE.test(tool)) return start(src, "Read", { file_path: `/${tool}` });
        return start(src, "Bash", { command: tool });
      }
      return end(src, ev.ok === false);
    }
    case "a2a": {
      if (src === "unixai" && morpheusDirect && ev.dir === "giden") return; // MorpheusAI kendi tarafını zaten gönderiyor
      const callee = ev.dir === "gelen" ? src : peer; // soruyu cevaplayan
      if (ev.phase === "start") return subtask(callee, `${name(ev.dir === "gelen" ? peer : src)}'den soru: ${ev.question || ""}`);
      busy[callee] = Math.max(0, busy[callee] - 1);
      hook(callee, { hook_event_name: ev.ok === false ? "PostToolUseFailure" : "PostToolUse" });
      if (ev.remediations) return hook("morpheus", { hook_event_name: "PermissionRequest" }); // playbook onay bekliyor
      return done(callee, 1500);
    }
    case "rem": {
      if (src === "unixai" && morpheusDirect) return;
      const s = ev.server || "sunucu";
      switch (ev.stage) {
        case "received": return start("morpheus", "Read", { file_path: `/bulgu-${s}` }).then(() => end("morpheus"));
        case "generated": return start("morpheus", "Write", { file_path: `/playbook-${s}.yml` })
          .then(() => { end("morpheus"); return hook("morpheus", { hook_event_name: "PermissionRequest" }); });
        case "approved": return start("morpheus", "Bash", { command: `onaylandı · ${ev.by || ""}` }).then(() => end("morpheus"));
        case "commit": return start("morpheus", "Bash", { command: `git commit · Bitbucket · ${s}` }).then(() => end("morpheus"));
        case "sync": return start("morpheus", "Bash", { command: "AAP proje sync" }).then(() => end("morpheus"));
        case "template": return start("morpheus", "Bash", { command: `AAP job template · ${s}` }).then(() => end("morpheus"));
        case "launch": return start("morpheus", "Bash", { command: `AAP ${ev.mode === "run" ? "uygula" : "check"} · ${s}` }).then(() => end("morpheus"));
        case "job": return start("morpheus", "Bash", { command: String(ev.text || "AAP iş sonucu").slice(0, 80) }).then(() => end("morpheus", !/successful/.test(ev.text || "")));
        case "rejected": case "failed": return start("morpheus", "Bash", { command: String(ev.text || ev.stage).slice(0, 80) }).then(() => end("morpheus", true));
        default: return undefined;
      }
    }
    case "run": {
      if (src !== "unixai") return undefined;
      if (ev.phase === "start") return subtask("unixai", `Toplama · ${(ev.servers || []).length} sunucu`);
      if (ev.phase === "server") return start("unixai", "Read", { file_path: `/${ev.server}` }).then(() => end("unixai", ev.ok === false));
      busy.unixai = Math.max(0, busy.unixai - 1);
      hook("unixai", { hook_event_name: "PostToolUse" });
      return done("unixai", 1500);
    }
    default:
      return undefined; // mcp/http ham trafik: ofiste ayrıca gösterilmez
  }
}

/* ---------------- canlı akış (SSE) istemcisi ---------------- */
async function follow(src, base, token) {
  let last = 0, wait = 2000;
  for (;;) {
    try {
      const res = await fetch(`${base.replace(/\/+$/, "")}/api/live`, {
        headers: { accept: "text/event-stream", ...(token ? { authorization: `Bearer ${token}` } : {}), ...(last ? { "last-event-id": String(last) } : {}) },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}${res.status === 401 ? " (token gerekli: UNIXAI_TOKEN / MORPHEUS_TOKEN)" : ""}`);
      log(`✓ ${src} canlı akışına bağlandı: ${base}`);
      if (src === "morpheus") morpheusDirect = true;
      wait = 2000;
      const dec = new TextDecoder();
      let buf = "";
      for await (const chunk of res.body) {
        buf += dec.decode(chunk, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, i);
          buf = buf.slice(i + 2);
          const id = frame.match(/^id: (\d+)$/m);
          if (id) last = Number(id[1]);
          const data = frame.match(/^data: (.*)$/m);
          if (!data) continue;
          try { await onEvent(src, JSON.parse(data[1])); } catch (e) { log(`! olay işlenemedi: ${e.message}`); }
        }
      }
      throw new Error("bağlantı kapandı");
    } catch (e) {
      if (src === "morpheus") morpheusDirect = false;
      log(`… ${src} akışı: ${e.message}; ${wait / 1000} sn sonra yeniden`);
      await new Promise((r) => setTimeout(r, wait));
      wait = Math.min(wait * 2, 60000);
    }
  }
}

if (!env("UNIXAI_URL") && !env("MORPHEUS_URL")) {
  console.error("UNIXAI_URL (ve isteğe bağlı MORPHEUS_URL) verin. Ayrıntı: integrations/pixel-office/README.md");
  process.exit(1);
}
log(`Pixel Office köprüsü · hedef: ${pixelTargets().map((t) => t.url).join(", ") || "(henüz bulunamadı)"}`);
// karakterleri ofise al: SessionStart + ilk olay ("Done")
for (const k of Object.keys(AGENTS)) hook(k, { hook_event_name: "Stop" });
if (env("UNIXAI_URL")) follow("unixai", env("UNIXAI_URL"), env("UNIXAI_TOKEN"));
if (env("MORPHEUS_URL")) follow("morpheus", env("MORPHEUS_URL"), env("MORPHEUS_TOKEN"));
