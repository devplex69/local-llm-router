# Start here if you have never set up a local model server before

The README's Quick start is four lines written for someone who already does this. This file is for the other case: you
have a gaming PC, you want your own models to answer from one address, and words like *port* and *VRAM gate* currently
mean nothing to you. Nothing here needs you to write code. You will edit one text file and double-click one program.

About 30 minutes if an engine is already installed on this machine; a few hours if it is not, most of it spent downloading.


## 1. The shape of it

```
  your chat app  ──►  the router  ──►  an engine  ──►  a model file on disk
  (Open WebUI,      (this repo,    (llama.cpp,     (a big .gguf or a "pack",
   an IDE, a         one address      Strata,        usually 5-70 GB)
   script)           :8090)           or both)
```

Two things to hold onto for the rest of this file:

- **An engine** is a program that can actually run a model: llama.cpp, or Strata. It is *not* this router. You install
  it yourself, separately, and this repo contains no engine and no model files.
- **Your graphics card only fits one engine at a time.** A big model fills the card's memory (VRAM) completely. The
  router exists because working out which engine is allowed to be awake, and shutting the other one down cleanly, is
  the annoying part. That is the whole product.


## 2. Check you have the four pieces

Do these checks first. Each one tells you exactly which key is missing, and nothing further works until it is present.

| what you need | how to check, as a person who does not code |
|---|---|
| **Windows 10 or 11** | Start menu → Settings → System → About → "Windows specifications". This router's start/stop code is Windows-only; on Linux or a Mac it will not run, and that is not something you can config around. |
| **An NVIDIA graphics card** | Ctrl+Shift+Esc (Task Manager) → Performance tab → the entry that says GPU. Look for **Dedicated GPU memory**. Write down that number in GB. AMD cards are not what this was run on. |
| **Python 3.9 or newer** | Start menu → type `cmd` → Enter → type `python --version`. You want `Python 3.x.y` with x at least 9. If it says *"is not recognized"*, download Python from python.org and **tick "Add python.exe to your PATH"** in the installer, then close and reopen the cmd window. Python runs the router only; your engine does not need it unless the engine's own instructions say so. |
| **One engine, already proven to start on its own** | Section 3 below. This is the one gate you cannot skip. |

**The model-file rule, so you buy the right file.** A model file is bigger than the number in your card. As a working
rule: the file size must be roughly **2 GB smaller than your dedicated GPU memory**. A 16 GB file wants a 24 GB card,
not a 16 GB one. Some models spill part of themselves into ordinary system RAM and start anyway at a tenth of the
speed — that is a real option, but expect it to feel slow, not broken.


## 3. Get an engine working on its own first

Do not wire the router in until the engine answers *by itself*. If it does not work alone, it will not work behind a
router, and the error you get there will be a lot harder to read.

### If the engine is llama.cpp

1. Download a **CUDA** build of `llama-server.exe` (the llama.cpp releases page publishes Windows CUDA zips; pick the
   one matching your card generation), and unzip it to a folder you will remember, for example `X:\llama-cpp`.
2. Download one model file (`.gguf`) into that folder or anywhere with room.
3. In the folder, click into the address bar, type `cmd`, Enter, and start it plainly:

   ```
   llama-server.exe -m my-model.gguf --alias my-model --host 127.0.0.1 --port 8082
   ```

   `--alias` is the name your apps will ask for. llama.cpp's own help text defines it as *"set model name aliases,
   comma-separated (to be used by API)"*, so this is the flag that decides the id, not a guess.
4. Open **http://127.0.0.1:8082** in a browser and chat. It should answer.
5. Close it with Ctrl+C in that window. You now know the engine works, and you know two things you need later: the
   path to `llama-server.exe`, and the alias.

llama.cpp can also host several models from one INI file (`--models-preset`), each section name becoming a model id.
That is the shape the example `registry.json` in this repo uses. One model is fine to start with.

### If the engine is Strata

Run Strata's own installer and its own setup, and let **it** write its config file — do not hand-write one. Strata's
`serve/server.py` documents the config it wants as *"strata engine config (JSON: exe, args, cwd, tokenizer,
model_name), written by setup.py"*. Write down three things when setup finishes: the folder it installed into, the
Python inside that folder (usually `.venv\Scripts\python.exe`), and the path of the `.json` it wrote. Then confirm
Strata's own page answers in a browser before going further.

One trap when you later copy those into `registry.json`: the command must say `--engine strata`. Strata's server
defaults that flag to `mock` — a fake engine that starts, answers, and produces text without any model at all. It will
feel like it works and be wrong every time. Read out of `serve/server.py` at Strata `v0.1.13`, `v0.1.45` and
`v0.2.3.1`: the default is `mock` at all three, so this is not an old note.


## 4. Set the router up

1. **Copy `registry.example.json`** in this folder, and rename the copy to **`registry.json`**. Right-click → Copy,
   right-click → Paste, then rename. This file is your machine's list of engines, so it stays out of git on purpose.
2. **Open `registry.json` in Notepad.** Right-click → Open with → Notepad.
3. **Change only these five things** (they are the ones that cannot have sensible defaults):

   | field | what to put |
   |---|---|
   | `cmd` (the list inside a backend) | the real paths: your engine's `.exe` or Python as the **first** item, then the arguments it needs to start |
   | `cwd` | the folder the engine lives in |
   | `port` (inside the backend) | a free port for that engine, e.g. `8082`, `8085`. Leave the **top-level** `"port"` at `8090` — that is your one front door |
   | `log` | `logs\<backend-name>.log` — a file you will read when something goes wrong |
   | `models` | one entry per model: `id` (exactly the id the engine itself advertises — for llama.cpp that is the `--alias` or the INI section name), plus a plain-English `label` and `needs_mib` |

   `needs_mib` is the number to get right: **roughly the model file size in GB × 1000, plus 2000**. A 13 GB file on a
   24 GB card: about `16500`. Too low and the engine loads and then crawls; too high and the router waits forever for
   memory that will never come free.
4. **Save it.** If Notepad offers ".txt" at the end of the name, remove it — `registry.json.txt` will not be found.
5. **Double-click `run-router.bat`.** A black window opens and stays open. Leave it: that window *is* the router.

JSON is fussy in exactly two ways, and both show up as the window opening and vanishing:

- **Windows paths need double backslashes** inside this file: `"X:\\llama-cpp\\llama-server.exe"`. One backslash each
  way breaks it. (Forward slashes also work here: `"X:/llama-cpp/llama-server.exe"`.)
- **No comma after the last item** in a list or `{ }` block, and quotes are straight `"` not curly ones.


## 5. You'll know it worked when

Open **http://127.0.0.1:8090** — that is an address, type it where Google goes, not into Google. If you changed the
top-level `"port"` in `registry.json`, use the address the black window printed instead; it always says it.

You should see a chat page with a dropdown of your models, each marked:

| mark | means |
|---|---|
| ● hot | loaded on the card right now, answers immediately |
| ◐ idle | the engine is up, this particular model is not loaded |
| ○ cold | the engine is not even running |

Pick a model, press **Wake this engine**, and watch the line under the dropdown count up. **A cold model takes from a
few seconds to a few minutes** — the first time it is slower than the countdown suggests because Windows has never read
those tens of gigabytes before. Do not press "Free the GPU" during that countdown; it throws the half-loaded engine away
and you start from zero. When it turns hot, send "hi".

Then check the second page, **http://127.0.0.1:8090/about** — it reads each engine's own live facts and shows the flags
it really started with. If a model misbehaves, this page tells you what the router actually launched.


## 6. Something went wrong: read your exact message

The router deliberately says what is wrong in plain words. Match the string you can see:

| what you see | what it means | what to do |
|---|---|---|
| `registry.json is missing. Copy registry.example.json to registry.json and edit it first.` | step 4.1 or 4.4 | the file is not in the same folder as `run-router.bat`, or it got saved as `registry.json.txt` |
| The black window flashes and closes | `registry.json` is invalid JSON, or Python is not on PATH | run `python proxy.py` from a cmd window in this folder — it prints the line and column. The three messages that mean "you have a JSON slip": `Expecting ',' delimiter` (a missing comma between two lines), `Illegal trailing comma` (a comma after the last item in a list), `Expecting property name enclosed in double quotes` (a comma after the last item in a `{ }` block) |
| `'python' is not recognized` | Python missing or installed without ticking PATH | reinstall and tick **Add python.exe to your PATH**, then close and reopen cmd |
| `X cannot start: port 8082 is already taken by docker.exe (pid 12345)` | another program owns that engine's port | close that app, **or** change that backend's `"port"` to a free number — you must also change the port number inside `cmd`, since they have to agree |
| `NOT killing :8082 - held by ..., which is not ...` | the router refused to touch a program it did not start | working as intended; see the row above |
| `the GPU only has 11400 MiB free but 16500 MiB is needed` | your card genuinely does not have room right now | close whatever is using the GPU (a game, a video render, sometimes a browser), or lower `needs_mib`, or use a smaller model |
| A countdown line like `strata-a: loading the model into VRAM - 41s so far, typically 1-2 min, leave it running; 379s before the router gives up` | it is working, not stuck | wait. That message is the router telling you to wait |
| `X is mid-answer for another request. One GPU means one engine: wait for it to finish...` (HTTP 409, `engine_busy`) | an answer is generating on the awake engine and you asked for a model on a different one | let it finish. This refusal is what stops your first answer being cut off |
| `unknown model 'my-model'` | the id you sent is not in `registry.json` | spelling matters — `id` must match the engine's own alias / INI section name exactly |
| It connects but is roughly 10× slower than it should be | something else on the PC is using the GPU memory, and the model got pushed into system RAM | close the other GPU program and reload the model. On a 24 GB card this is the single most common cause of "it used to be fast" |
| An app cannot connect at all | wrong base URL | `http://127.0.0.1:8090/v1`, any text as the key, and the model `id` exactly as in `registry.json` |

To stop everything cleanly, double-click **`stop-all.bat`**. Run it once with `-Report` first: it prints the exact
processes it would stop and who holds GPU memory, and changes nothing.


## 7. Words used in this repo

- **port** — a numbered door on your own machine. `8090` is the router's; every engine has its own number behind it.
- **127.0.0.1 / localhost** — "this computer only". Nothing here is reachable from the internet, or from your
  phone, unless you deliberately change that.
- **front door / backend** — the router's port everyone connects to; an engine's own port behind it.
- **VRAM** — memory on your graphics card. It is the scarce thing; one model fills it.
- **`needs_mib`** — how much free VRAM this router requires before it will start that engine. MiB ≈ megabyte.
- **cold / idle / hot** — engine not running / running without that model / that model loaded and ready.
- **cold start** — the wait while a model is read off disk onto the card. Not a bug.
- **streaming** — the answer arrives a few words at a time instead of all at once. Matters because a cold load that
  streams keeps your app's patience alive instead of timing out.
- **quantization** — how aggressively a model file was compressed. It is why the same model comes as 8 GB, 13 GB and
  16 GB files, and it is the main thing deciding whether your card fits it.


## 8. Two rules

1. **One engine at a time — and let the router do the switching.** Do not start an engine by hand while the router is
   running *and* expect both to be used. The router will adopt an engine it finds already running, but two servers
   reaching for the same card is exactly the failure this repo was written to prevent.
2. **There is no password, on purpose.** Anyone who can reach that port on your machine can load your models and run
   them. Fine on your own desktop. Do not change the bind address to reach your network without putting something that
   does authentication in front of it.


## 9. What to read next

- **README.md → "Which engine versions this works with"** if you are on a recent llama.cpp or Strata release, or if an
  upgrade changed something.
- **README.md → `registry.json`** for the fields this guide skipped (`kind`, `ready_timeout`, per-model `needs_mib`).
- **README.md → "Notable limits"** before you ask why some endpoint 404s.
- **`CREDITS.md`** — which files in this folder are not original, and whose license travels with them.
