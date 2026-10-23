// ui/web/page.js - shared by both router pages: the chat page (/) and the backends page (/about).
// Theme bootstrap, copy buttons, toasts, the status pill and the eight hardware tiles, built on the kit in
// tokens.css + components.css + app.css. Loaded before the page's own script; they are one global scope, so a name
// declared here must not be declared again there.
// Most of this file is adapted from Strata v0.1.13 serve/web/app.js - MIT, (c) 2026 Niko1221 and the Strata
// contributors; see THIRD_PARTY_LICENSES.md. The toast map, clipboard handler, sparkline builder and Monitor tile
// definitions are verbatim from it; the engine/router parts are not.
"use strict";

const $ = (id) => document.getElementById(id);
const SPRITE = "/web/sprite.svg";
const icon = (name, cls = "st-icon") => `<svg class="${cls}" aria-hidden="true"><use href="${SPRITE}#i-${name}"/></svg>`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "\u2013" : Number(n).toLocaleString(undefined, {maximumFractionDigits: d, minimumFractionDigits: d}));
const kfmt = (n) => (n == null ? "\u2013" : n >= 1000 ? `${fmt(n / 1000, n >= 10000 ? 0 : 1)}k` : fmt(n));
const gb = (b, d = 1) => (b == null ? "\u2013" : fmt(b / 1073741824, d));
const clockOf = (epoch) => new Date(epoch * 1000).toLocaleTimeString();

const store = {
  get(k, d) { try { const v = localStorage.getItem("router." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("router." + k, JSON.stringify(v)); } catch (e) { /* private mode: in memory only */ } },
};

function toast(kind, title, text = "", ms = 3500) {
  const names = {info: "info", success: "check", warn: "warning", error: "error"};
  const el = document.createElement("div");
  el.className = `st-toast st-toast--${kind}`;
  el.innerHTML = `${icon(names[kind] || "info")}<div><div class="st-toast__title"></div><div class="t-text"></div></div>`;
  el.querySelector(".st-toast__title").textContent = title;
  el.querySelector(".t-text").textContent = text;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {                                   // plain http, and webviews that refuse the async API
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  if (btn) {
    const use = btn.querySelector("use");
    use.setAttribute("href", `${SPRITE}#i-check`);
    setTimeout(() => use.setAttribute("href", `${SPRITE}#i-copy`), 1500);
  }
  toast("success", "Copied", "", 1500);
}
// any <button data-copy="#id"> copies that element's text
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-copy]");
  if (b) copyText($(b.dataset.copy).textContent.trim(), b);
});

// ---------------------------------------------------------------------- theme
function setTheme(t, save) {
  document.documentElement.dataset.theme = t;
  if (save) try { localStorage.setItem("router.theme", t); } catch (e) { /* ignore */ }
  $("theme-icon").setAttribute("href", `${SPRITE}#i-${t === "dark" ? "sun" : "moon"}`);
}
$("theme-btn").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
setTheme(document.documentElement.dataset.theme || "light", false);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
  let saved = null;
  try { saved = localStorage.getItem("router.theme"); } catch (err) { /* ignore */ }
  if (!saved) setTheme(e.matches ? "dark" : "light", false);
});

// ---------------------------------------------------------------------- status pill
function setPill(state, text) {
  $("pill").dataset.state = state;
  $("pill-text").textContent = text;
}

// ---------------------------------------------------------------------- the hardware tiles
const METRICS = [
  {key: "speed", label: "Speed", icon: "gauge", unit: "tok/s", series: "tok_s"},
  {key: "gpu", label: "GPU load", icon: "gpu", unit: "%", series: "gpu_util", max: 100},
  {key: "vram", label: "VRAM", icon: "layers", unit: "GB", series: "gpu_mem_used"},
  {key: "temp", label: "GPU temp", icon: "thermometer", unit: "\u00b0C", series: "gpu_temp", tone: "warn"},
  {key: "power", label: "Power", icon: "bolt", unit: "W", series: "gpu_power"},
  {key: "pcie", label: "PCIe", icon: "link", unit: "", series: "gpu_pcie_rx_mb", tone: "info"},
  {key: "cpu", label: "CPU", icon: "cpu", unit: "%", series: "cpu", max: 100},
  {key: "disk", label: "Disk read", icon: "disk", unit: "MB/s", series: "disk_read_mb", tone: "info"},
];

function mountTiles(container) {
  container.innerHTML = METRICS.map((mt) => `
    <div class="st-card metric-card"><div class="st-metric">
      <span class="st-metric__label">${icon(mt.icon, "st-icon st-icon--sm")}${esc(mt.label)}</span>
      <span class="st-metric__value" id="mv-${mt.key}">\u2013</span>
      <span class="st-metric__sub" id="ms-${mt.key}"></span>
      <svg class="st-metric__spark" id="sp-${mt.key}" viewBox="0 0 100 32" preserveAspectRatio="none"${mt.tone ? ` data-tone="${mt.tone}"` : ""}>
        <path class="area" fill="currentColor" opacity=".12"/><path class="line" fill="none" stroke="currentColor"
        stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>
    </div></div>`).join("");
}

function spark(id, values, max) {
  const svg = $(id);
  const v = (values || []).map((x) => (x == null ? 0 : x));
  if (v.length < 2) { svg.querySelector(".line").setAttribute("d", ""); svg.querySelector(".area").setAttribute("d", ""); return; }
  const top = Math.max(max || 0, ...v, 1e-9);
  const pts = v.map((x, i) => [(i / (v.length - 1)) * 100, 30 - (x / top) * 26]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join("");
  svg.querySelector(".line").setAttribute("d", line);
  svg.querySelector(".area").setAttribute("d", `${line}L100,32L0,32Z`);
}

function setMetric(key, value, unit, sub) {
  $(`mv-${key}`).innerHTML = value == null ? "\u2013" : `${esc(value)}${unit ? `<small>${esc(unit)}</small>` : ""}`;
  $(`ms-${key}`).textContent = sub || "";
}

/** Everything except Speed, which only the page knows the context for. `pc` and `h` come from /router/metrics
    or /about.json - both are built from the same telemetry snapshot, so the keys match. */
function paintTiles(pc, h) {
  pc = pc || {}; h = h || {};
  setMetric("gpu", pc.gpu_util == null ? null : fmt(pc.gpu_util), "%", pc.gpu_name || "");
  spark("sp-gpu", h.gpu_util, 100);
  setMetric("vram", pc.gpu_mem_used == null ? null : gb(pc.gpu_mem_used),
            pc.gpu_mem_total ? `/ ${gb(pc.gpu_mem_total, 0)} GB` : "GB",
            pc.gpu_mem_total ? `${gb(pc.gpu_mem_total - pc.gpu_mem_used)} GB free` : "");
  spark("sp-vram", h.gpu_mem_used, pc.gpu_mem_total);
  setMetric("temp", pc.gpu_temp == null ? null : fmt(pc.gpu_temp), "\u00b0C", "");
  spark("sp-temp", h.gpu_temp, 90);
  setMetric("power", pc.gpu_power == null ? null : fmt(pc.gpu_power), "W",
            pc.gpu_power_limit ? `of ${fmt(pc.gpu_power_limit)} W limit` : "");
  spark("sp-power", h.gpu_power, pc.gpu_power_limit);
  const gen = pc.gpu_pcie_gen_max || pc.gpu_pcie_gen;
  setMetric("pcie", gen ? `Gen${gen}` : null, pc.gpu_pcie_width ? `x${pc.gpu_pcie_width}` : "",
            pc.gpu_pcie_rx_mb == null ? "" : `to GPU ${fmt(pc.gpu_pcie_rx_mb, pc.gpu_pcie_rx_mb < 10 ? 1 : 0)} MB/s`);
  spark("sp-pcie", h.gpu_pcie_rx_mb);
  setMetric("cpu", pc.cpu == null ? null : fmt(pc.cpu), "%",
            pc.threads ? `${pc.cores ? `${pc.cores} cores - ` : ""}${pc.threads} threads` : "");
  spark("sp-cpu", h.cpu, 100);
  if (pc.disk_read_mb == null) {
    setMetric("disk", null, "", pc.psutil ? "" : "needs psutil (setup installs it)");
  } else {
    const big = pc.disk_read_mb >= 1000;
    setMetric("disk", big ? fmt(pc.disk_read_mb / 1024, 2) : fmt(pc.disk_read_mb, pc.disk_read_mb < 10 ? 1 : 0),
              big ? "GB/s" : "MB/s", "");
  }
  spark("sp-disk", h.disk_read_mb);
}

/** A `.facts` list from [label, html] pairs; empty values are dropped so the page never shows a blank row. */
function facts(rows) {
  return rows.filter((r) => r[1] != null && r[1] !== "").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("");
}
const copyChip = (id, text) => `<code id="${id}">${esc(text)}</code>`
  + `<button type="button" class="st-btn st-btn--icon" data-copy="${id}" aria-label="Copy">`
  + `${icon("copy", "st-icon st-icon--sm")}</button>`;
