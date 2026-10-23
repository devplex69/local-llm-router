# Credits

This router is a thin layer in front of other people's work, and several files here are theirs, not mine. Listed in
the order they matter to this project.

## [Strata](https://github.com/Niko1221/Strata) — Niko1221 and the Strata contributors

**License: MIT.** The inference engine this router was written to manage, and the source of its entire front end.
Copied from tag `v0.1.13`; each file's header names its origin, and
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) records the upstream blob hashes:

- `telemetry.py` — verbatim (NVML-over-ctypes hardware sampler; the Monitor tab is this project's, the readings are theirs)
- `ui/web/tokens.css`, `ui/web/components.css`, `ui/web/app.css` — the design system: tokens, components, layout
- `ui/web/sprite.svg` — the icon sprite, byte-for-byte
- `ui/index.html` — the chat page's markup skeleton and control layout
- `ui/web/page.js` — toasts, copy buttons, theme pre-paint, status pill, hardware tiles and sparklines

Strata's own README points at [llama.cpp / ggml](https://github.com/ggml-org/llama.cpp) (MIT) inside its engine; that
code stays in Strata and is not redistributed here.

## [@code-martin](https://github.com/code-martin) — the Monitor tab idea

**[Strata PR #22](https://github.com/Niko1221/Strata/pull/22)**, "tools: add real-time web dashboard for hardware and
engine monitoring". Strata's `docs/DETAILS.md` credits the Monitor tab to it, and `telemetry.py`'s header carries that
credit upstream — this project inherited the tab and its tile layout from that line of work. (Checked against the API:
the PR is closed, unmerged.)

## [Outfit](https://github.com/Outfitio/Outfit-Fonts) — the font

**License: SIL Open Font License 1.1.** `ui/fonts/outfit-latin-wght.woff2` and
`ui/fonts/outfit-latin-ext-wght.woff2`, byte-for-byte upstream, with the license text at `ui/fonts/OFL.txt`, whose
first line reads "Copyright 2021 The Outfit Project Authors (https://github.com/Outfitio/Outfit-Fonts)". The OFL
requires that text to travel with the fonts, so keep it in any fork.

## [llama.cpp](https://github.com/ggml-org/llama.cpp) — ggml org

**License: MIT.** No llama.cpp code is in this repository. `llama-server` is one of the two engines the router drives
(the `kind: "llama"` backends, including its multi-model router mode with `--models-preset`), so this project's llama
support exists only because that server exposes a stable HTTP API. Credit where credit is due, and go install it
yourself.

## The models

Model weights (Qwen and the quantizations/finetunes built on it) are **not** distributed here and are each under their
own terms. `registry.example.json` ships placeholder ids; the real stack this was developed against — an uncensored
27B on llama.cpp and Flash-Next MoE packs on Strata — is a private detail of one 24 GB card.

## What is original here

`proxy.py` (engine lifecycle, the relative-VRAM gate, the busy guard, routing, SSE relay, `/about`), the registry
schema, `run-router.bat`, `stop-all.ps1` / `stop-all.bat`, `busytest.py`, `ui/web/router.css`, `ui/web/about.js`,
`ui/about.html` and the chat/monitor logic in `ui/web/router.js`. `proxy.py` shares no meaningful code with Strata's
`serve/server.py`: of its 317 lines longer than 45 characters, 4 also appear upstream, and all four are Python stdlib
boilerplate.

## If you fork or reuse this

The router's own code is MIT (`LICENSE`), which means the copyright line and permission notice travel with it. Same
obligation applies one layer down: keep Strata's and Outfit's notices on the files they cover, and keep this file
pointing at where each piece came from. Attribution is the entire price of the license, and it is a cheap one.
