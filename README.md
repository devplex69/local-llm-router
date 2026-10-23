# local-llm-router

One HTTP port in front of several local LLM engines, on a machine with one GPU.

The problem it solves: you have llama.cpp on one port, a second engine on another, and maybe a third build for
experimenting. Every chat app wants `http://127.0.0.1:<port>/v1`, and every one of those engines wants the whole
graphics card. Starting a second engine without stopping the first does not fail cleanly — on a 24 GB card a
30 GB load just stalls, both processes report a healthy port, and the driver hands back 0 MiB free.

This router takes the front door (`:8090`), keeps every engine's port behind it, and owns the handover: pick a
model, it stops whatever is resident, waits for the VRAM to actually come back, starts the engine you asked for,
**proves it can generate tokens**, and only then forwards your message. Your other server is never left in a half
-loaded state, and nothing you did not register is ever killed.

```
   your apps ── :8090 ──► proxy.py ──► llama.cpp router  :8082   (many presets, one loaded)
        one port          one GPU       a second engine   :8085    (back-end ports are yours
         at a time                    a third             :8087     to pick in registry.json)
```

Everything is stdlib Python — no pip install, no database, no Node.

**Never done this before?** Read [GETTING-STARTED.md](GETTING-STARTED.md) instead of the Quick start below. It covers
installing an engine, how to check each of your prerequisites, what every field in `registry.json` is for, a table that
matches the exact error message you can see to the thing to do about it, and a plain-language glossary. The Quick start
assumes you already have llama.cpp or Strata answering on its own port.


## Quick start

0. If no engine starts on this machine yet, stop here and do [GETTING-STARTED.md](GETTING-STARTED.md) sections 2-3.
1. Copy `registry.example.json` to `registry.json`.
2. Put your own engine paths and model ids in it (see [registry.json](#registryjson) below).
3. `run-router.bat` (or `python proxy.py`).
4. Open <http://127.0.0.1:8090>.

The first request to a cold model waits for its engine to load — seconds for a small GGUF, a minute or more for a
big MoE pack. The UI shows a live countdown while that happens, and streaming requests get SSE keepalive comments
so the client's own timeout never fires.


## Why it refuses to run two engines

Two rules do most of the work, and both come from being burned:

**Liveness is proven by tokens, not by a port.** Several engine wrappers answer `/health` with `200 ok` from a thin
Python layer even when the model server behind them died or is still loading. So the router never trusts a status
endpoint: after starting a backend it sends a four-token completion and only marks the engine `hot` once real
tokens come back. A backend that answers `/v1/models` but cannot decode is restarted rather than routed to.

**A swap waits for free VRAM, and refuses while another request is mid-answer.** The gate is *relative* — "does the
card have `needs_mib` free right now" — not absolute, because a desktop with browsers and overlays open was
measured holding 6.8 GB on its own and that would block every load forever. If a generation is in flight on the
resident engine, a conflicting request gets a clean `409 engine_busy` instead of a kill that truncates someone
else's answer.


## registry.json

```jsonc
{
  "port": 8090,                  // the one front door
  "backends": [
    {
      "name": "llama-router",    // shown in the UI, used by /router/state
      "kind": "llama",           // "llama" or anything else — see below, it changes status reporting
      "port": 8082,              // this backend's own port, behind the front door
      "cwd": "X:\\llama-cpp",
      "needs_mib": 16500,        // free VRAM this engine needs before it is worth starting
      "ready_timeout": 900,      // seconds to wait for the engine to prove it can decode
      "log": "X:\\router\\logs\\llama-router.log",
      "cmd": ["X:\\llama-cpp\\llama-server.exe", "--port", "8082", ...],
      "models": [
        { "id": "example-27b", "needs_mib": 16500, "label": "what the picker shows" },
        { "id": "example-9b",  "needs_mib": 7000,  "label": "same engine, smaller model" }
      ]
    }
  ]
}
```

- **`kind`** is the only behavioural switch. `"llama"` means "one server process that can host many models but only
  ever has one loaded", so the router asks `/v1/models` for per-model load status and reports unloaded presets as
  `idle` instead of `hot`. Any other value means "a single-model server": up means hot. Add more engines by copying
  a block and changing `name`, `port` and `cmd`.
- **`models[].id` must be the id the backend itself serves**, because it is passed through to the engine's
  `/v1/chat/completions` unchanged. With a llama.cpp `--models-preset` ini, the section names are the ids.
- **`needs_mib`** is a measured number, not a guess — a 27B Q3 needs roughly 16.5 GB free on a 24 GB card with a
  desktop running. Too low and the engine starts and then thrashes or OOMs; too high and the router waits for
  memory that will never free up.
- Model `label`s are pure UI text. Put the honest one-line tradeoff there ("slower, but better at prose") — the
  picker is where you remember why you have four arms of the same model.
- A preset added to the engine after the proxy started is discovered on first use, so you can edit the llama.cpp
  preset file without restarting the router.
- **`registry.json` is git-ignored** — it is your machine's paths and model stack. Keep the real one out of a
  public repo; `registry.example.json` is the template that ships.


## Endpoints

| | |
|---|---|
| `GET  /` | chat UI with the model picker |
| `GET  /about` | every backend's real startup flags, config file and live status |
| `GET  /monitor` | an **alias for `/about`** - same page, both routes serve it. The hardware tiles live on it; the per-second numbers come from `/router/metrics` below |
| `GET  /v1/models` | all registered models, tagged `hot` / `idle` / `cold` |
| `GET  /router/state` | backend detail the UI polls (pid, status, busy count, recent events) |
| `GET  /router/metrics` | the Monitor feed; served from caches so it can be polled every second |
| `GET  /about.json` | raw data behind /about, including each engine's live facts |
| `POST /router/load` | `{"model": "<id>"}` — warm an engine without sending a chat |
| `POST /router/stop` | stop every engine and free the GPU |
| `POST /v1/chat/completions` | routed by `model`; streams SSE, keepalive comments during a cold swap |
| `POST /v1/messages` | Anthropic-shaped, forwarded to a `kind: strata`-style backend that speaks it |
| `GET  /health` | `{status, hot[], swapping}` — note this is the *router's* health, not an engine's |

`/router/load` and `/router/stop` are what you script. To warm a model before a work session:

```sh
curl -s -X POST http://127.0.0.1:8090/router/load -H "Content-Type: application/json" \
     -d '{"model":"example-27b"}'
```


## Pointing clients at it

One base URL for all of them — `http://127.0.0.1:8090/v1` — and the model id picks the engine. The router binds
both `127.0.0.1` and `::1` on the loopback, because some clients resolve `localhost` to `::1` first and never fall
back.

```python
# OpenAI SDK / anything built on it
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8090/v1", api_key="not-needed")
client.chat.completions.create(model="example-27b", messages=[{"role": "user", "content": "hi"}])
```

```jsonc
// Claude Code / Anthropic-style: base URL without /v1
{ "ANTHROPIC_BASE_URL": "http://127.0.0.1:8090", "ANTHROPIC_API_KEY": "not-needed" }
```

For tools that let you name a model per profile (Open WebUI, Continue, silo, aider), make one profile per model id
and the router handles which engine actually has to be awake. `busytest.py` is a small script that proves the
mid-answer refusal works: it starts a long generation on the hot engine, polls `/router/state` until that backend
reports `busy`, then fires a request for a cold model and shows the clean `409` instead of a truncated answer.


## The three safety behaviours worth knowing

1. **A listener it did not register is never killed.** Before stopping anything the router resolves the PIDs on
   the port and compares their executable path and command line against the backend's own. Docker Desktop was
   found holding a port this router would otherwise have used; the old port-based kill would have force-removed it
   and its whole tree during an ordinary model swap. Foreign listeners produce a `NOT killing :<port>` event and a
   500 that tells you what to close or re-map.
2. **Engines you started by hand are adopted, and shown as adopted.** If a backend's port is already answering
   when the router comes up, it is used and reported with its real PID and an `adopted` flag rather than
   double-started.
3. **Killing is by bare PID, never a process tree, for the proxy itself.** `taskkill /T` on the proxy takes the
   loaded model child down with it in a way that can leave the GPU allocation half-released. `stop-all.ps1` asks
   the proxy to release engines first, then terminates leftovers by PID, and re-checks every port afterwards.

`stop-all.ps1` reads the same `registry.json`, so it only ever touches processes your registry describes. Run
`stop-all.bat -Report` first: it prints exactly what it would kill and what holds GPU memory, and changes nothing.


## Requirements and what is actually tested

- **Windows.** The lifecycle code calls `netstat -ano`, `taskkill`, `Get-CimInstance Win32_Process` and
  `subprocess`'s Windows-only `creationflags`. It will not run on Linux as written — that is a port, not a bug you
  can config around.
- **NVIDIA**, for the VRAM gate and the hardware tiles (`nvidia-smi`, and NVML through `ctypes` for the Monitor
  tab). Without `nvidia-smi` the free-VRAM gate degrades to "start anyway", so a swap can OOM instead of waiting.
- **Python 3.9+**, standard library only. `psutil` is used for CPU/disk readings if already installed and
  ignored if not; nothing needs `pip install`.
- **One GPU.** This is a single-card tool. With two cards you want a scheduler, not this.
- **Developed and tested on one machine:** RTX 4090 24 GB, i9-13900K, 128 GB RAM, Windows 11, against a llama.cpp
  CUDA router build and two versions of one MoE engine. The two engines it was built around are llama.cpp and
  Strata; anything else is registered the same way but I have not run it, so expect to check `needs_mib` and
  `ready_timeout` for your own engine rather than trusting mine.
- It binds loopback only and has **no authentication**. Anyone who can reach that port can load your models and
  run them. That is fine on your own desktop and not fine on a shared network — keep it on `127.0.0.1`, or front it
  with something that does auth.


## Which engine versions this works with

The router does not link against an engine, so it is not version-locked — it only has to be able to *start* one, and
then talk OpenAI-shaped HTTP to it. What that means in practice, per engine:

### Strata

Strata's version numbers moved from the `0.1.x` line to `0.2.x`, and the engine packages are versioned separately from
the app, so it is worth being specific. I read `serve/server.py` at three tags — `v0.1.13` (70,835 bytes / 1,330
lines), `v0.1.45` (430,671 / 7,159) and `v0.2.3.1` (520,530 / 8,607) — and compared the argument parser:

- **The three flags a registry `cmd` needs — `--engine`, `--config`, `--port` — exist at all three tags.** A backend
  block written against an older Strata still starts on a newer one.
- **`--config` means the same thing at all three**: its help text is byte-identical — *"strata engine config (JSON: exe,
  args, cwd, tokenizer, model_name), written by setup.py"*. So let Strata's own setup write that file; don't hand-roll it.
  The loader is also still a plain `json.loads` with no required-key schema (`read_config` / `parse_config_text` at
  `v0.2.3.1`), so a config file written for an older tag parses on a newer one.
- **The engine command line survives too.** `serve/server.py` only builds the `--engine/--config/--port` part; the
  model-loading flags live inside the config's `args` list and go to the engine binary. So I checked the engine's own
  argument parser (`src/program/generate.cpp`) at `v0.2.3.1`: it grew from 3,939 lines to 17,028, and every flag a real
  working launch used on `0.1.x` is still a live parse branch there — `--pack`, `--native`, `--native-dense-gguf`,
  `--ple-gguf`, `--pool-workers`, `--expert-profile`, `--expert-cache`, `--expert-cache-cpu-order`, `--prefill`,
  `--spec`, `--spec-min-p`, `--mtp`, `--max-context`, `--kv`. That is source evidence, not a booted engine: a *newer*
  engine binary can still change what those values do.
- **`--engine` defaults to `mock`, at every version.** That is the one way to get a confidently-wrong setup: drop the
  flag and the engine starts, answers `/health`, and produces tokens — from a fake engine. The router's liveness probe
  cannot catch that, because it only checks that real tokens come back. Always pass `--engine strata` explicitly.
- **Newer flags you can leave alone, and two you should not.** Checked defaults at `v0.2.3.1`:
  `--min-free-vram-mib` defaults to *none* ("always load"); `--idle-unload` defaults to *never*; `--api-key` defaults to
  `$STRATA_API_KEY` or empty, i.e. no key required. Leave them at their defaults. Set `--min-free-vram-mib` only if you
  also *lower* the router's `needs_mib` below it, or the router will consider the card ready while the engine answers
  503 to the load. And if you export `STRATA_API_KEY` globally, name the key in that backend's `cmd` too — the router
  forwards whatever your client sent, and clients sending `api_key="not-needed"` will be refused by the engine.
- `--replicas`, `--parallel`, `--max-queue` change the engine from one-at-a-time to many-at-a-time. The router still
  works — but its `409 engine_busy` refusal is the router's policy, not the engine's, so a swap guard you set up for
  one slot no longer describes what the engine will accept.
- The UI in this repo was copied from `v0.1.13` and is labelled as such in `THIRD_PARTY_LICENSES.md`; the newer tags
  still ship `serve/web/tokens.css` / `components.css` / `app.css`, but these files were not re-synced to them.

### llama.cpp

`registry.example.json` uses router mode (`--models-preset` + `--models-max 1`), which is what makes one llama.cpp
engine serve several models. Verified present, from `llama-server.exe --help` on build **b11201**, the build this was
developed against: `--models-preset`, `--models-max`, and `-a, --alias` (*"set model name aliases, comma-separated (to
be used by API)"*). Router mode is the newer part, so **check before you copy that block**: run
`llama-server.exe --help | findstr models-preset`. If your build does not list it, don't upgrade llama.cpp for this —
give each model its own backend block with a plain `-m <file> --alias <id> --port <n>` command. That works on any build
and the router handles the swapping exactly the same; the only loss is sharing one process across models.

### What is honest here

**Run** on: Windows 11, RTX 4090, one llama.cpp CUDA build (b11201) and Strata 0.1.x. Everything in the Strata list
above comes from reading Strata's source at those tags — `serve/server.py`, and for the flags `src/program/generate.cpp`
and `src/kernels/cuda/iq_kernels.cu`. `v0.2.3.1` itself has not been started behind this router on my machine. If you do
run it there and something has moved, that is worth an issue.


## Notable limits

- **Only the endpoints listed above are routed.** `/v1/completions`, `/v1/embeddings`, `/v1/models/<x>/...` and
  anything else a client might try return a 404 that names the path — the router does not pass unknown paths
  through to the hot engine. If a tool needs a text-completion endpoint, that is a small addition to
  `Handler.do_POST`, not a config.
- The cold-start wait is inside the request. A client with a 60 s timeout that does not read streaming comments
  will give up on a 3-minute MoE load. Prefer `/router/load` before you send, or use a streaming request.
- `needs_mib` is per model, so if one llama.cpp router hosts both a 16 GB and a 6 GB model you must set both
  numbers correctly or the cheap one will be blocked behind the expensive one's gate.
- The `409 engine_busy` refusal is per-swap, and it is deliberately not a queue — no request is silently held
  while another finishes. Queuing is a policy decision your client is better placed to make.
- Model ids are the join between everything. Rename one in the engine without renaming it in `registry.json` and
  the router will report the model as unknown rather than guess.


## Files

| | |
|---|---|
| `GETTING-STARTED.md` | the no-coding walkthrough: prerequisites, installing an engine, every `registry.json` field, error-message table, glossary |
| `proxy.py` | the router: engine lifecycle, VRAM gate, routing, SSE relay, both API shapes |
| `telemetry.py` | NVML + OS hardware sampler for the Monitor tab; optional, the page degrades without it. Verbatim Strata code (MIT) - see `THIRD_PARTY_LICENSES.md` |
| `registry.example.json` | template — copy to `registry.json` |
| `run-router.bat` | starts the proxy relative to its own folder |
| `stop-all.ps1` / `.bat` | surgical shutdown that reads the same registry |
| `busytest.py` | proves a swap request is refused while an engine is mid-answer |
| `ui/` | the chat page, the backends page, the monitor feed, and their CSS/JS — partly Strata's, see `THIRD_PARTY_LICENSES.md` |
| `THIRD_PARTY_LICENSES.md` | exactly which files come from Strata v0.1.13, with blob hashes, plus the MIT and OFL texts |
| `CREDITS.md` | who wrote what here, including the font authors and the dashboard idea this UI grew from |
| `LICENSE` | MIT, this repo's own code |


## License

**The router's own code is MIT** — see [LICENSE](LICENSE), `Copyright (c) 2026 devplex69`. Use it, fork it, ship it in
your stack; the only condition is that the copyright line and permission notice travel with it.

That notice has to travel two more layers, because parts of this repo are **not** original:

- `telemetry.py`, `ui/web/tokens.css`, `ui/web/components.css`, `ui/web/app.css`, `ui/web/sprite.svg`, `ui/index.html`
  and most of `ui/web/page.js` are taken from [Strata](https://github.com/Niko1221/Strata) tag `v0.1.13`
  (**MIT, Copyright (c) 2026 Niko1221 and the Strata contributors**). Their license text and per-file provenance
  headers are in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md), with upstream blob hashes so you can verify the
  copies yourself. An earlier draft of this README called the styling "adapted, attribution unclear", which was
  wrong — the copies are verifiable against upstream's git tree.
- `ui/fonts/` ships two `.woff2` files of *Outfit*, **SIL Open Font License 1.1**; the text is at `ui/fonts/OFL.txt`
  and must travel with the fonts.
- The Monitor tab's concept traces to **[@code-martin's Strata PR #22](https://github.com/Niko1221/Strata/pull/22)**,
  per Strata's own credits, and is preserved in `telemetry.py`'s header.

Original here: `proxy.py` and the registry schema, `run-router.bat`, `stop-all.ps1` / `.bat`, `busytest.py`,
`ui/web/router.css`, `ui/web/about.js`, `ui/about.html`, and the chat/monitor logic in `ui/web/router.js`. `proxy.py`
shares no meaningful code with Strata's `serve/server.py` (4 of its 317 long lines match, all stdlib boilerplate).
Full accounting in [CREDITS.md](CREDITS.md).

If you need a repo with none of Strata's code in it, `THIRD_PARTY_LICENSES.md` says which files to drop or replace and
what breaks when you do (cosmetic, plus the Monitor tiles).

The llama.cpp and Strata engines themselves are separate projects you install independently — this repo contains
no engine binary and no model weights.
