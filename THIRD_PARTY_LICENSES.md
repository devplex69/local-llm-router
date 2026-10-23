# Third-party code in this repository

Most of this repo is original - `proxy.py`, `registry.example.json`, `run-router.bat`, `stop-all.ps1` / `.bat`,
`busytest.py`, `ui/web/router.css` and `ui/about.html` - and `proxy.py` shares no meaningful code with Strata's
`serve/server.py` either (of its 317 long lines, exactly 4 match upstream, and all four are stdlib boilerplate like
`from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer`).

But the UI, and the hardware sampler behind the Monitor tab, are **not** written here. They come from
[Strata](https://github.com/Niko1221/Strata), the MoE inference engine this router was built to sit in front of.
Strata is under the MIT license, reproduced in full at the bottom, and MIT requires this notice and its copyright
line to travel with any copy. So: here they are.

The source snapshot is **tag `v0.1.13`**, and the copy it came from is provably that tag: every upstream path in the
table hash-compares to the blob id shown when fetched from the tag's git tree. Four of them ship here **byte for
byte** - `ui/web/sprite.svg`, both `.woff2` files and `ui/fonts/OFL.txt` - so running `git hash-object` on the copy in
this repo returns exactly the id in the table. The rest are adapted; the "what changed" column says how much.

| file here | upstream path @ v0.1.13 | blob sha | what changed |
|---|---|---|---|
| `telemetry.py` | `serve/telemetry.py` | `1bb9b2925c0dc1ff124902872d0d64df3f3273e8` | the module docstring only - every line of code is upstream's |
| `ui/web/tokens.css` | `serve/web/tokens.css` | `a96ce6c5909e98cdf9d4666cd68b7cdcb85e2329` | header comments only |
| `ui/web/app.css` | `serve/web/app.css` | `9c8e190b8071977ca823ee2b92b85bf1cc3ffc63` | header comments only |
| `ui/web/components.css` | `serve/web/components.css` | `899f525729a8bada806289250285f6957a75131e` | header comments only |
| `ui/web/sprite.svg` | `serve/web/sprite.svg` | `598f6234a6c2bbb4cd4e201feaca55f1a9e66585` | **nothing** — verbatim copy, left untouched so its embedded C2PA/Content-Credentials manifest keeps hashing |
| `ui/index.html` | `serve/web/index.html` | `60901f4a94d211235245aa46ae4d0c301424e01c` | 126 of upstream's 211 lines are kept verbatim; 85 replaced and 80 added. The DOM skeleton, class names, theme bootstrap and control layout are still Strata's |
| `ui/web/page.js` | `serve/web/app.js` | `9ec3eaa688278a502c5613fe4277a0478836456e` | 78 of its 138 non-empty lines appear verbatim in upstream's `app.js`, including 21 substantial statements - the toast map, the clipboard handler, the sparkline path builder and the Monitor tile definitions (`{key: "speed", ... series: "tok_s"}`) |
| `ui/web/router.js` | `serve/web/app.js` | `9ec3eaa688278a502c5613fe4277a0478836456e` | 3 substantial statements reused (drawer/Escape handling and the SSE decode loop); everything else - model picker, engine status, swap countdown, routing - is this project's |
| `ui/web/about.js` | `serve/web/app.js` | `9ec3eaa688278a502c5613fe4277a0478836456e` | 1 line reused; the copy on this page is written here |
| `ui/fonts/outfit-latin-wght.woff2` | `serve/web/fonts/outfit-latin-wght.woff2` | `85e3332e6d9430f3c6b158843ceb6d3f95743f8a` | **nothing** — Outfit is SIL OFL 1.1, text at `ui/fonts/OFL.txt` |
| `ui/fonts/outfit-latin-ext-wght.woff2` | `serve/web/fonts/outfit-latin-ext-wght.woff2` | `c54ceec800d486b63e4812d54f0d62d83eeacd4f` | **nothing** — same |
| `ui/fonts/OFL.txt` | `serve/web/fonts/OFL.txt` | `723cd447edee83ed3bb07c3343f478d352aa7440` | **nothing** — the font's own license text |

Outfit itself is the font, **SIL Open Font License 1.1** - `ui/fonts/OFL.txt` line 1 reads "Copyright 2021 The Outfit
Project Authors (https://github.com/Outfitio/Outfit-Fonts)" - and that text is included and must stay next to the
fonts. Upstream Strata additionally credits the Monitor tab's concept to `@code-martin` (Strata PR #22) and describes
`tokens.css` as "derived from the Gainsty design system"; both statements come from Strata's own
`docs/DETAILS.md`, and I have not followed that chain further.

## Why the notice is worded this way

The copy of Strata these files came from was installed before upstream added a `LICENSE` file: at tag `v0.1.13` there
is genuinely no `LICENSE` in that tree (it exists on `main` only), which is why this repo's first draft described the
styling as "adapted, attribution unclear". That draft was wrong in a way worth correcting: the snapshot really is
v0.1.13 (every path above hashes to that tag), and as of 2026-10-23 the project carries MIT with
`Copyright (c) 2026 Niko1221 and the Strata contributors`. So these are MIT files, and the honest thing is to say whose
they are rather than keep the credit vague.

If you would rather ship none of it: `proxy.py` works with any CSS you like - `ui/index.html` links four sheets, of
which `tokens.css`, `components.css` and `app.css` are upstream's and `router.css` is this repo's, so replacing the
first three (or dropping all four links) leaves the router fully functional - and `telemetry.py` is optional: the
monitor degrades and the API keeps working without it. `sprite.svg` is upstream's too; delete it and the icon sprite
disappears, which is cosmetic.

## MIT License (upstream Strata)

```
MIT License

Copyright (c) 2026 Niko1221 and the Strata contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
