// ui/web/about.js - the Backends page: every engine's live facts, read from /about.json.
// page.js provides $, esc, fmt, gb, icon, setPill, paintTiles, mountTiles, spark, setMetric, facts, copyChip.
"use strict";

mountTiles($("metrics"));

const ENGINE_KEYS = ["version", "model", "max_context", "kv", "kv_resident", "expert_slots",
                     "expert_cache_mib", "spec", "mtp_max", "lookup", "vram_free_mib", "cvec", "arena_mib",
                     "images", "api_key", "role", "build_info"];

const chipList = (obj, only) => {
  const keys = Object.keys(obj).filter((k) => obj[k] != null && obj[k] !== "" && obj[k] !== true);
  const use = only ? keys.filter((k) => only.includes(k)) : keys;
  return use.length ? `<div class="params">` + use.map((k) =>
    `<code title="${esc(obj[k])}">${esc(k)}=${esc(String(obj[k]).slice(0, 46))}</code>`).join("") + `</div>` : "";
};

function badge(status) {
  return `<span class="st-badge st-badge--${status}">${esc(status)}</span>`;
}

function backendCard(b) {
  const eng = b.engine || {};
  const ver = eng.version ? `v${eng.version}` : eng.build_info;
  return `<div class="st-card be-card">
    <div class="be-card__head"><span class="card-title">${esc(b.name)}</span>${badge(b.status)}
      <span class="muted small">${esc(b.kind)} - 127.0.0.1:${b.port}${b.pid ? ` - pid ${b.pid}${b.adopted ? " (started by hand; the router can still stop it)" : ""}` : ""}${b.busy ? ` - ${b.busy} generating` : ""}</span>
    </div>
    <dl class="facts">${facts([
      ["OpenAI", copyChip(`u-${b.name}`, b.openai_url)],
      ["Anthropic", b.kind === "strata" ? copyChip(`a-${b.name}`, b.anthropic_url) : `<span class="muted">not served (use /v1/chat/completions)</span>`],
      ["Engine", ver ? `<code>${esc(ver)}</code>`
        : b.status === "cold" ? `<span class="muted">cold - what is below is read from disk</span>`
        : `<span class="muted">running, but it volunteers no version</span>`],
      ["Needs", `${b.needs_mib} MiB free VRAM - ready timeout ${b.ready_timeout}s`],
      ["Config", b.config ? `<code>${esc(b.config)}</code>` : null],
      ["Weights", b.model_file ? `<code>${esc(b.model_file)}</code>`
        : eng.model_path && eng.model_path !== "none" ? `<code>${esc(eng.model_path)}</code>`
        : (eng.model ? `<code>${esc(eng.model)}</code>` : null)],
      ["Log", b.log ? `<code>${esc(b.log)}</code>` : null],
    ])}</dl>
    ${chipList(eng, ENGINE_KEYS)}
    ${b.server_flags ? `<div class="be-card__lbl">Server command</div>${chipList(b.server_flags)}` : ""}
    ${b.args ? `<div class="be-card__lbl">Engine arguments</div>${chipList(b.args)}` : ""}
    <div class="be-card__lbl">Models on this backend</div>
    <div class="table-wrap"><table class="st-table">
      <thead><tr><th style="width:280px">id</th><th style="width:90px">state</th><th>settings</th><th>notes</th></tr></thead>
      <tbody>${b.models.map((m) => `<tr><td>${copyChip(`m-${b.name}-${m.id}`, m.id)}</td>
        <td>${badge(m.status)}</td>
        <td>${m.params && Object.keys(m.params).length ? chipList(m.params)
             : `<span class="muted">${b.kind === "strata" ? "shared config above" : ""}</span>`}</td>
        <td class="muted">${esc(m.label)}</td></tr>`).join("")}</tbody>
    </table></div>
  </div>`;
}

function requestRows(rs) {
  return !rs.length ? `<tr><td colspan="7" class="muted">No requests yet - rates here are measured by the proxy
    itself, so llama.cpp and Strata are timed the same way.</td></tr>`
    : rs.map((r) => `<tr><td>${clockOf(r.time)}</td><td>${esc(r.engine)}</td><td><code>${esc(r.model)}</code></td>
       <td class="num">${r.stream ? "yes" : "no"}</td><td class="num">${r.output_tokens}</td>
       <td class="num">${r.decode_tok_s == null ? "\u2013" : fmt(r.decode_tok_s, 1)}</td>
       <td class="num">${fmt(r.duration_s, 1)}</td></tr>`).join("");
}

let about = null, tick = 0, failures = 0;

// ------------------------------------------------ the "custom provider" form, filled in from live data
const ORDER = {hot: 0, idle: 1, cold: 2};

/** The registry labels are sentences (they read as notes in the engine tables); an app's Name field wants the
    clause before the first " - ", with the full text kept in the tooltip. */
function displayName(m) {
  const full = (m.label || "").trim() || m.id.replace(/[-_]/g, " ");
  const cut = full.split(/ - |\. /)[0].trim();
  const name = cut.length > 58 ? `${cut.slice(0, 57).trimEnd()}\u2026` : cut;
  return `<span title="${esc(full)}">${esc(name)}</span>`;
}

function providerCard(d) {
  const gpu = d.pc && d.pc.gpu_name ? d.pc.gpu_name.replace(/^NVIDIA /, "") : "this PC";
  $("facts-provider").innerHTML = facts([
    ["Provider ID", copyChip("p-id", "local_router")],
    ["Display name", copyChip("p-name", `Local Router (${gpu})`)],
    ["Provider API", `<code>OpenAI Compatible</code>`],
    ["Base URL", copyChip("p-url", d.proxy.openai_url)],
    ["API key", `${copyChip("p-key", "local")} <span class="muted">any non-empty string works - the router checks no
      key, and sends none on to the engine</span>`],
    ["Headers", `<span class="muted">none - leave the section empty</span>`],
  ]);
  const rows = d.backends.flatMap((b) => b.models.map((m) => ({...m, engine: b.name, kind: b.kind})))
    .sort((a, z) => ORDER[a.status] - ORDER[z.status] || a.engine.localeCompare(z.engine) || a.id.localeCompare(z.id));
  $("provider-models").innerHTML = rows.map((m) => `<tr>
      <td>${copyChip(`pm-${m.id}`, m.id)}</td>
      <td>${displayName(m)}</td>
      <td><span class="st-badge st-badge--hot">tick</span></td>
      <td>${m.kind === "strata" ? `<span class="st-badge st-badge--hot">tick</span>`
                                 : `<span class="st-badge st-badge--cold">leave off</span>`}</td>
      <td class="muted">${esc(m.engine)} - ${esc(m.kind)} <span class="st-badge st-badge--${m.status}">${m.status}</span></td>
    </tr>`).join("");
  $("provider-notes").innerHTML = "Reasoning: a model has a thinking mode only if its engine exposes one, and it "
    + "stays off unless the app asks for it - tick the box so the app may use it, and give it a generous token cap, "
    + "because thinking counts against it. Image: only models registered with a vision projector can take one. The "
    + "first request to a <code>cold</code> model waits for its engine, and one engine holds the GPU at a time. "
    + "Each engine's real context window and startup flags are on the <a href='/about'>Backends</a> tab, read from "
    + "the engine itself, so check there before setting a context in your app.";
}

async function deep() {                     // /about.json touches the live engines, so it runs every 4 s, not 1 s
  try { about = await (await fetch("/about.json")).json(); } catch (e) { return; }
  const p = about.proxy;
  providerCard(about);
  $("facts-proxy").innerHTML = facts([
    ["OpenAI", copyChip("u-oai", p.openai_url)],
    ["Anthropic", copyChip("u-ant", p.anthropic_url)],
    ["Engines", `${about.backends.length} - ${about.backends.map((b) => esc(b.name)).join(", ")}`],
    ["Models", about.backends.reduce((a, b) => a + b.models.length, 0)],
    ["Updated", `${esc(p.now)}`],
  ]);
  $("bes").innerHTML = about.backends.map(backendCard).join("");
}

async function fast() {
  let m;
  try {
    const r = await fetch("/router/metrics");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    m = await r.json();
    failures = 0;
  } catch (e) {
    if (++failures === 2) setPill("error", "Router not reachable");
    return;
  }
  const live = m.live || {state: "idle"}, last = (m.requests || [])[0], pc = m.pc || {}, h = m.history || {};
  if (m.swapping) setPill("loading", `Waking \u00b7 ${m.swap_seconds || 0}s`);
  else if (live.state === "generating") setPill("generating", live.tok_s == null ? "Generating" : `Generating \u00b7 ${fmt(live.tok_s, 1)} tok/s`);
  else {
    const hot = (m.backends || []).filter((b) => b.status === "hot").map((b) => b.name);
    setPill(failures ? "error" : "idle", hot.length ? `${hot.join(", ")} resident` : "Idle - GPU free");
  }
  const speed = live.state === "generating" ? live.tok_s : last ? last.decode_tok_s : null;
  setMetric("speed", speed == null ? null : fmt(speed, 1), "tok/s",
            live.state === "generating" ? "now" : last ? "last request" : "");
  spark("sp-speed", h.tok_s);
  paintTiles(pc, h);
  $("req-body").innerHTML = requestRows(m.requests || []);
  $("log-list").innerHTML = !(m.events || []).length ? `<li class="muted">nothing yet</li>`
    : m.events.map((e) => `<li><b>${clockOf(e.t)}</b> ${esc(e.msg)}</li>`).join("");

  if (++tick % 4 === 0) deep();
  setTimeout(fast, 1000);
}

fast();
deep();
