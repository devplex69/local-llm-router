// ui/web/router.js - the chat page and the Monitor tab of the local router.
// Helpers, theme, pill and the hardware tiles come from page.js; the endpoints are the router's:
// /v1/models, /router/metrics, /router/load, /router/stop, /v1/chat/completions.
// Written here; three statements are reused from Strata v0.1.13 serve/web/app.js (MIT - THIRD_PARTY_LICENSES.md).
"use strict";

// ---------------------------------------------------------------------- tabs
let tab = location.hash === "#monitor" ? "monitor" : "chat";
function showTab(name) {
  tab = name === "monitor" ? "monitor" : "chat";
  for (const b of document.querySelectorAll("button.st-tab")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  $("view-chat").hidden = tab !== "chat";
  $("view-monitor").hidden = tab !== "monitor";
  if (location.hash.slice(1) !== tab) window.history.replaceState(null, "", tab === "chat" ? location.pathname : `#${tab}`);
  if (tab === "chat") $("input").focus();
  if (M) render(M);
}
for (const b of document.querySelectorAll("button.st-tab")) b.onclick = () => showTab(b.dataset.tab);

// ---------------------------------------------------------------------- drawer
const sampling = () => store.get("sampling", {thinking: "none", max: 8192});
function openDrawer(open) {
  $("drawer").dataset.open = String(open);
  $("drawer").setAttribute("aria-hidden", String(!open));
  $("scrim").hidden = !open;
}
function paintDrawer() {
  const s = sampling();
  for (const b of $("s-thinking").children) b.setAttribute("aria-checked", String(b.dataset.v === s.thinking));
  $("o-thinking").textContent = s.thinking === "none" ? "off" : s.thinking;
  $("s-max").value = s.max || "";
  $("o-max").textContent = s.max ? fmt(s.max) : "until done";
}
$("sampling-btn").onclick = () => { paintDrawer(); openDrawer(true); };
$("drawer-close").onclick = () => openDrawer(false);
$("scrim").onclick = () => openDrawer(false);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("drawer").dataset.open === "true") openDrawer(false); });
for (const b of $("s-thinking").children) b.onclick = () => {
  const s = sampling(); s.thinking = b.dataset.v; store.set("sampling", s); paintDrawer();
};
$("s-max").oninput = () => {
  const s = sampling(); s.max = Math.max(1, Number($("s-max").value) || 8192);
  store.set("sampling", s); $("o-max").textContent = fmt(s.max);
};
$("s-reset").onclick = () => { store.set("sampling", {thinking: "none", max: 8192}); paintDrawer(); toast("info", "Back to the defaults"); };
$("s-apply").onclick = () => { openDrawer(false); toast("success", "Saved", "Used for the next message."); };
paintDrawer();

mountTiles($("metrics"));

// ---------------------------------------------------------------------- models
let MODELS = [], SIGN = "";

function current() { return MODELS.find((m) => m.id === $("models").value) || MODELS[0] || {}; }

function hint() {
  const m = current();
  if (m.status === "hot") return `<b>${esc(m.id)}</b> is resident on ${esc(m.engine || "its engine")} - sending a chat answers at once.`;
  return `One engine holds the GPU at a time. <b>Wake this engine</b> makes the picked model resident now, no message `
       + `needed. A cold engine takes from a few seconds to a few minutes depending on how much it has to load - `
       + `the countdown above is that wait. Sending a chat does the same switch by itself.`;
}

function paint() {
  const m = current();
  $("mid").textContent = m.id || "\u2013";
  $("attach-btn").hidden = m.engine_kind !== "strata";   // image input only where the engine was built with a vision projector
  const hot = MODELS.find((x) => x.status === "hot");
  $("chat-empty-sub").textContent = !m.id ? "The router is not answering yet."
    : !hot ? `${m.id} is cold - press Wake this engine. Nothing leaves this PC.`
    : hot.id === m.id ? `${m.id} is awake. Nothing leaves this PC.`
    : `${hot.id} is awake; waking ${m.id} means a swap.`;
  $("bar-hint").innerHTML = hint();
  store.set("model", m.id);
}

async function loadModels() {
  let d;
  try { d = await (await fetch("/v1/models")).json(); } catch (e) { return; }
  MODELS = d.data || [];
  const sel = $("models"), want = sel.value || store.get("model", "");
  const sig = MODELS.map((m) => `${m.status}:${m.id}`).join("|");
  if (sig !== SIGN) {                                    // rebuilding on every poll would close an open dropdown
    SIGN = sig;
    sel.innerHTML = "";
    for (const m of MODELS) {
      const o = document.createElement("option");
      o.value = m.id;
      o.textContent = `${m.status === "hot" ? "\u25cf" : m.status === "idle" ? "\u25d0" : "\u25cb"} ${m.id}  [${m.engine}]`;
      sel.appendChild(o);
    }
  }
  if (want && MODELS.some((m) => m.id === want)) sel.value = want;
  paint();
}
$("models").onchange = () => paint();

$("oai").textContent = location.origin + "/v1";
$("ant").textContent = location.origin;

// ---------------------------------------------------------------------- the switch
let FLASH = 0, WAS_SWAP = false;
function setSwap(msg, tone) {
  $("swap").textContent = msg || "";
  $("swap").className = "swap-line" + (tone ? ` ${tone}` : "");
}

$("load").onclick = async () => {
  const m = current();
  if (!m.id) return;
  $("load").disabled = true;
  setSwap(`preparing ${m.id} ...`);
  let msg = `${m.id} is resident.`, tone = "ok";
  try {
    const d = await (await fetch("/router/load", {method: "POST", headers: {"Content-Type": "application/json"},
                                                  body: JSON.stringify({model: m.id})})).json();
    if (d.error) { msg = `could not load: ${d.error.message || JSON.stringify(d.error)}`; tone = "err"; }
  } catch (e) { msg = `switch request failed: ${e}`; tone = "err"; }
  FLASH = Date.now() + 9000;
  setSwap(msg, tone);
  loadModels();
};

$("stopall").onclick = async () => {
  if (M && M.swapping && !confirm(`An engine is loading right now ("${M.message}").\n\n` +
      "Free the GPU would kill it and the load starts from zero next time. Free it anyway?")) return;
  setSwap("stopping every engine ..."); FLASH = Date.now() + 8000;
  try {
    await fetch("/router/stop", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  } catch (e) { FLASH = 0; setSwap(`could not reach the router: ${e}`, "err"); return; }
  setSwap("GPU free", "ok"); FLASH = Date.now() + 4000; loadModels();
};

// ---------------------------------------------------------------------- the 1 s feed
let M = null, failures = 0;

async function poll() {
  try {
    const r = await fetch("/router/metrics");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    M = await r.json();
    failures = 0;
    render(M);
  } catch (e) {
    if (++failures === 2) setPill("error", "Router not reachable");
    $("chips").innerHTML = "";
  }
  setTimeout(poll, 1000);
}

function render(m) {
  const live = m.live || {state: "idle"};
  $("load").disabled = !!m.swapping;
  $("load").textContent = m.swapping ? "Switching ..." : "Wake this engine";
  $("swap-progress").hidden = !m.swapping;
  if (m.swapping && m.message) {
    setSwap(`(${m.swap_seconds || 0}s) ${m.message}`);
    WAS_SWAP = true;
  } else {
    if (WAS_SWAP) {                              // the countdown ended: rest a line the user can read, then clear it
      WAS_SWAP = false;
      if (Date.now() > FLASH) setSwap("engine ready", "ok");
      FLASH = Date.now() + 5000;
    } else if (Date.now() > FLASH) setSwap("");
  }

  $("chips").innerHTML = (m.backends || []).map((b) =>
    `<span class="st-badge st-badge--${b.status}" title="${esc(`${b.name} - ${b.kind} on 127.0.0.1:${b.port}`)}`
    + `${b.pid ? ` (pid ${b.pid})` : ""}${b.busy ? `, ${b.busy} generating` : ""}">`
    + `${esc(b.name)} <span class="chip__port">:${b.port}</span></span>`).join("");

  if (m.swapping) setPill("loading", `Waking \u00b7 ${m.swap_seconds || 0}s`);
  else if (live.state === "generating") setPill("generating", live.tok_s == null ? "Generating" : `Generating \u00b7 ${fmt(live.tok_s, 1)} tok/s`);
  else if (live.state === "prefill") setPill("reading", `Reading prompt \u00b7 ${live.seconds}s`);
  else if (failures) setPill("error", "Router not reachable");
  else {
    const hot = (m.backends || []).filter((b) => b.status === "hot").map((b) => b.name);
    setPill("idle", hot.length ? `${hot.join(", ")} resident` : "Idle - GPU free");
  }
  if (tab === "monitor") renderMonitor(m, live);
}

function renderMonitor(m, live) {
  for (const b of document.querySelectorAll("#state-badges .st-badge")) b.classList.toggle("on", b.dataset.s === live.state);
  const prog = $("state-progress");
  let label = "Waiting for a request", detail = "", pct = 0, busy = false;
  delete prog.dataset.tone;
  if (live.state === "loading") {
    busy = true;
    label = "Waking an engine";
    detail = `${live.seconds}s so far - the engine is reading its model off disk. Big MoE packs can take 1-3 min.`;
  } else if (live.state === "prefill") {
    prog.dataset.tone = "info";
    label = "Reading prompt";
    detail = `${live.engine} / ${live.model} - ${live.seconds}s, ${fmt(live.bytes)} bytes back`;
  } else if (live.state === "generating") {
    const cap = Number($("s-max").value) || 8192;
    label = "Generating";
    pct = Math.min(100, (100 * live.tokens) / cap);
    detail = `${kfmt(live.tokens)} tokens - ${fmt(live.tok_s, 1)} tok/s on ${live.engine}`;
  } else {
    const last = (m.requests || [])[0];
    label = "Idle";
    detail = last ? `last: ${last.output_tokens} tokens at ${fmt(last.decode_tok_s, 1)} tok/s on ${last.engine}`
                  : "no request has passed through this proxy yet";
  }
  prog.classList.toggle("st-progress--busy", busy);
  if (busy) prog.dataset.tone = "busy";
  $("state-label").textContent = label;
  $("state-detail").textContent = detail;
  $("state-bar").style.width = busy ? "" : `${pct}%`;

  const pc = m.pc || {}, h = m.history || {}, last = (m.requests || [])[0];
  const speed = live.state === "generating" ? live.tok_s : last ? last.decode_tok_s : null;
  setMetric("speed", speed == null ? null : fmt(speed, 1), "tok/s",
            live.state === "generating" ? "now" : last ? "last request" : "");
  spark("sp-speed", h.tok_s);
  paintTiles(pc, h);

  const frac = pc.gpu_mem_total && pc.gpu_mem_used != null ? Math.min(1, pc.gpu_mem_used / pc.gpu_mem_total) : 0;
  $("mem-fill").setAttribute("stroke-dasharray", `${(235.6 * frac).toFixed(1)} 314.2`);
  $("mem-fill").style.opacity = 235.6 * frac >= 3 ? "1" : "0";   // a near-zero arc would draw just its round cap
  $("mem-pct").textContent = `${Math.round(frac * 100)}%`;
  $("mem-sub").textContent = pc.gpu_mem_total ? `${gb(pc.gpu_mem_used)} / ${gb(pc.gpu_mem_total, 0)} GB` : "\u2013";
  $("ram-text").textContent = pc.ram_total ? `${gb(pc.ram_used)} / ${gb(pc.ram_total, 0)} GB` : "\u2013";
  $("ram-bar").style.width = `${pc.ram_total ? Math.min(100, (100 * pc.ram_used) / pc.ram_total) : 0}%`;
  $("temp-text").textContent = pc.gpu_temp == null ? "\u2013" : `${fmt(pc.gpu_temp)} \u00b0C`;
  $("temp-bar").style.width = `${Math.min(100, (100 * (pc.gpu_temp || 0)) / 90)}%`;
  const up = (m.backends || []).filter((b) => b.status !== "cold");
  $("who").textContent = up.length ? up.map((b) => `${b.name} :${b.port} (${b.status})`).join(", ") : "nothing resident";

  $("engine-body").innerHTML = (m.backends || []).map((b) => `<tr>
      <td><b>${esc(b.name)}</b></td><td>${esc(b.kind)}</td><td class="num">${b.port}</td>
      <td><span class="st-badge st-badge--${b.status}">${b.status}</span>${b.busy ? ` <span class="muted">${b.busy} generating</span>` : ""}</td>
      <td class="num">${b.pid ?? "\u2013"}</td><td class="num">${(b.models || []).length}</td></tr>`).join("");

  $("req-body").innerHTML = !(m.requests || []).length ? `<tr><td colspan="6" class="muted">No requests yet</td></tr>`
    : m.requests.map((r) => `<tr><td>${clockOf(r.time)}</td><td>${esc(r.engine)}</td>
       <td><code>${esc(r.model)}</code></td><td class="num">${r.output_tokens}</td>
       <td class="num">${r.decode_tok_s == null ? "\u2013" : fmt(r.decode_tok_s, 1)}</td>
       <td class="num">${fmt(r.duration_s, 1)}</td></tr>`).join("");

  $("log-list").innerHTML = !(m.events || []).length ? `<li class="muted">nothing yet</li>`
    : m.events.map((e) => `<li><b>${clockOf(e.t)}</b> ${esc(e.msg)}</li>`).join("");
}

// ---------------------------------------------------------------------- chat
let CHAT = [], image = null, ctrl = null;
const EMPTY = $("chat").innerHTML;
const scrollChat = () => { const s = $("chat-scroll"); s.scrollTop = s.scrollHeight; };

function addMsg(role) {
  if ($("chat-empty")) $("chat").innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = `st-msg st-msg--${role}`;
  const bub = document.createElement("div");
  bub.className = "st-bubble";
  wrap.appendChild(bub);
  $("chat").appendChild(wrap);
  scrollChat();
  return {wrap, bub};
}

function showErr(into, text) {
  const box = document.createElement("div");
  box.className = "msg-error";
  box.textContent = `error: ${text}`;
  into.appendChild(box);
  scrollChat();
}

$("file").onchange = () => {
  const f = $("file").files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => { image = r.result; showChip(f.name); };
  r.readAsDataURL(f);
};
function showChip(name) {
  const box = $("attachments");
  box.hidden = false;
  box.innerHTML = `<span class="chip">${icon("image", "st-icon st-icon--sm")}${esc(name)}`
    + `<button type="button" class="st-btn st-btn--icon" id="chip-x" aria-label="Remove the picture">${icon("trash", "st-icon st-icon--sm")}</button></span>`;
  $("chip-x").onclick = clearImage;
}
function clearImage() { image = null; $("file").value = ""; $("attachments").hidden = true; $("attachments").innerHTML = ""; }

$("attach-btn").onclick = () => $("file").click();
$("new-btn").onclick = () => { CHAT = []; clearImage(); $("chat").innerHTML = EMPTY; $("input").focus(); };
$("stop-btn").onclick = () => ctrl && ctrl.abort();

const ta = $("input");
ta.addEventListener("input", () => {
  ta.style.height = "auto";
  ta.style.height = `${Math.min(ta.scrollHeight, Math.round(window.innerHeight * 0.4))}px`;
});
ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); } });

$("composer").onsubmit = async (e) => {
  e.preventDefault();
  const text = ta.value.trim();
  if (!text) return;
  ta.value = ""; ta.style.height = "auto";
  const m = current();
  const content = image ? [{type: "image_url", image_url: {url: image}}, {type: "text", text}] : text;

  const {wrap: u, bub: ub} = addMsg("user");
  if (image) {
    const box = document.createElement("div");
    box.className = "msg-images";
    const im = document.createElement("img");
    im.src = image;
    box.appendChild(im);
    u.insertBefore(box, ub);
  }
  const ut = document.createElement("div");
  ut.textContent = text;
  ub.appendChild(ut);
  CHAT.push({role: "user", content});
  clearImage();

  const {bub} = addMsg("assistant");
  const ans = document.createElement("div");
  ans.className = "cursor";
  bub.appendChild(ans);
  let det = null, think = null;
  $("send-btn").disabled = true;
  $("stop-btn").hidden = false;
  ctrl = new AbortController();
  let answer = "", stopped = false;
  const s = sampling(), effort = s.thinking;
  try {
    const body = {model: m.id, messages: CHAT, stream: true, max_tokens: s.max || 8192,
                  chat_template_kwargs: {enable_thinking: effort !== "none"}};
    if (effort !== "none") body.reasoning_effort = effort;
    const r = await fetch("/v1/chat/completions", {method: "POST", headers: {"Content-Type": "application/json"},
                                                   body: JSON.stringify(body), signal: ctrl.signal});
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error((j.error && (j.error.message || JSON.stringify(j.error))) || `HTTP ${r.status}`);
    }
    const rd = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const {value, done} = await rd.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 2);
        if (line.startsWith(":")) { setSwap(line.replace(/^:+\s*/, "")); FLASH = Date.now() + 6000; continue; }  // swap keepalive
        if (!line.startsWith("data: ") || line === "data: [DONE]") continue;
        const d = JSON.parse(line.slice(6));
        if (d.error) throw new Error(d.error.message || JSON.stringify(d.error));
        const delta = (d.choices || [{}])[0].delta || {};
        if (delta.reasoning_content) {
          if (!det) {
            det = document.createElement("details");
            det.className = "st-collapse";
            det.open = true;
            det.innerHTML = `<summary>${icon("thinking", "st-icon st-icon--sm")}Thinking`
              + `${icon("chevron", "st-icon st-icon--sm st-chev")}</summary><div class="st-collapse__body thinking"></div>`;
            bub.insertBefore(det, ans);
            think = det.querySelector(".thinking");
          }
          think.textContent += delta.reasoning_content;
        }
        if (delta.content) { answer += delta.content; ans.textContent = answer; }
        scrollChat();
      }
    }
  } catch (err) {
    if (err && err.name === "AbortError") { stopped = true; ans.textContent = `${answer} [stopped]`; }
    else { showErr(bub, err.message || String(err)); toast("error", "That request failed", err.message || String(err), 6000); }
  }
  ans.classList.remove("cursor");
  if (det) {
    det.open = false;
    if (!answer && !stopped) {                     // thinking ate the whole budget: say so instead of showing nothing
      const n = document.createElement("div");
      n.className = "muted small";
      n.textContent = "Thinking reached the token cap before an answer - raise Max tokens or set thinking to Off.";
      bub.appendChild(n);
    }
  }
  if (!answer && !det) ans.remove();
  CHAT.push({role: "assistant", content: answer});
  $("send-btn").disabled = false;
  $("stop-btn").hidden = true;
  ctrl = null;
  loadModels();
};

// ---------------------------------------------------------------------- go
showTab(tab);
loadModels();
setInterval(loadModels, 4000);
poll();
