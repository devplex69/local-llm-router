"""proxy.py - one port in front of every local LLM engine on this box.

Why this exists: several engines can serve the same models, but on a single-GPU machine only ONE may hold the
GPU at a time - a 20-30 GB load pins the card, and two engines loading at once make both stall and report
0 MiB free.  So the proxy owns engine lifecycle: pick a model in the UI, and it stops whatever is resident,
starts what you asked for, waits for a *real* generation (some engines answer /health with 200 even when the
model server behind them is dead, so liveness is only proven by tokens), then forwards your request.

    GET  /                       the chat UI (ui/index.html)
    GET  /v1/models              every model in registry.json, each tagged hot / idle / cold
    GET  /router/state           backend detail for the UI: up, pid, VRAM, recent events
    GET  /router/metrics         the Monitor feed: what the router is doing this second
    GET  /about                  what each engine actually starts with, read off disk
    POST /router/load            {"model": id} - warm an engine without sending a chat
    POST /router/stop            stop every engine, free the GPU
    POST /v1/chat/completions    routed by "model"; cold swaps stream SSE keepalive comments so clients don't time out
    POST /v1/messages            Anthropic shape, forwarded to a hot backend that speaks it

Run:  python proxy.py      (registry.json next to this file; start from registry.example.json)
"""
from __future__ import annotations

import codecs
import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REG = json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))
PORT = int(REG.get("port", 8090))
UI = ROOT / "ui"
MIME = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".ico": "image/x-icon"}

sys.path.insert(0, str(ROOT))          # so telemetry.py, which sits next to this file, imports
try:
    from telemetry import Telemetry
except ImportError:                   # the page still works without the hardware tiles
    Telemetry = None

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
EVENTS: list[dict] = []
PROVEN: dict[str, float] = {}   # backend name -> when a real generation last succeeded; skips re-probing per message
PROVEN_FRESH_S = 30
BUSY: dict[str, int] = {}       # in-flight generations per backend: a swap must not kill an engine mid-answer
BUSY_LOCK = threading.Lock()
SWAP = threading.Lock()       # one swap at a time: two requests must never race two engines onto the GPU
SWAPPING = {"active": False, "message": "", "since": 0.0}


def event(msg: str):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    EVENTS.append({"t": time.time(), "msg": msg})
    del EVENTS[:-40]
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


# -------------------------------------------------------------------------------------------------- backends
class Backend:
    def __init__(self, d: dict):
        self.name = d["name"]
        self.kind = d["kind"]
        self.port = int(d["port"])
        self.cwd = d.get("cwd")
        self.cmd = d["cmd"]
        self.log = d.get("log")
        self.ready_timeout = int(d.get("ready_timeout", 300))
        self.needs_mib = int(d.get("needs_mib", 19000))   # VRAM the engine itself needs, measured not guessed
        self.model_needs = {m["id"]: int(m["needs_mib"]) for m in d.get("models", []) if m.get("needs_mib")}
        self.labels = {m["id"]: m.get("label", "") for m in d.get("models", [])}
        self.order = [m["id"] for m in d.get("models", [])]
        self.proc: subprocess.Popen | None = None
        first = str(self.cmd[0]) if self.cmd else ""
        self.expected_exe = str(Path(first).resolve()).lower() if first.lower().endswith(".exe") else ""

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def needs_for(self, model_id: str) -> int:
        """One llama.cpp router can host a 16 GB dense model and a CPU-MoE model that needs almost no VRAM."""
        return self.model_needs.get(model_id, self.needs_mib)

    def first_model(self) -> str:
        return self.order[0] if self.order else "strata"


BACKENDS = [Backend(d) for d in REG["backends"]]
BY_NAME = {b.name: b for b in BACKENDS}


# -------------------------------------------------------------------------------------------------- system
def http_json(url: str, body: dict | None = None, timeout: float = 10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


LISTEN_TTL = 1.0
LISTEN = {"ts": 0.0, "map": {}}    # port -> pids listening on it, from one cached netstat snapshot


def listen_map(fresh: bool = False) -> dict[int, set[int]]:
    """Which ports have a listener, from a single `netstat -ano -p tcp` (about 45 ms), reused for a second.

    This exists because a TCP connect to an unbound loopback port does NOT come back as a quick refusal on this
    box - it hangs for the whole timeout, ~2 s each. Probing four cold backends over HTTP therefore cost about
    eight seconds per cycle, which is what made /about.json take 18 s and the status line look frozen.
    """
    now = time.time()
    if fresh or now - LISTEN["ts"] > LISTEN_TTL:
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                                 timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        ports: dict[int, set[int]] = {}
        for line in out.splitlines():
            f = line.split()
            if len(f) >= 5 and f[3] == "LISTENING" and ":" in f[1]:
                try:
                    port, pid = int(f[1].rsplit(":", 1)[1]), int(f[4])
                except ValueError:
                    continue
                ports.setdefault(port, set()).add(pid)
        LISTEN.update(ts=now, map=ports)
    return LISTEN["map"]


def pids_on_port(port: int, fresh: bool = False) -> set[int]:
    """Who listens on a local TCP port. Pass fresh=True when the answer decides a kill."""
    return listen_map(fresh).get(int(port), set())


def proc_exes(pids: set[int]) -> dict[int, tuple[str, str]]:
    """(executable path, command line) per PID, so a listener that is not one of our engines can be told apart from one.

    The command line matters because a venv python.exe is a redirector: Windows reports the listener's path as
    the base interpreter no matter which venv launched it, so the path alone cannot tell our engine server
    from an unrelated Python program.
    """
    if not pids:
        return {}
    ids = ",".join(str(int(p)) for p in sorted(pids))
    script = (f"$ids = @({ids}); Get-CimInstance Win32_Process | Where-Object {{ $ids -contains $_.ProcessId }} "
              "| ForEach-Object { [string]$_.ProcessId + '|' + $_.Path + '|' + $_.CommandLine }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    procs = {}
    for line in out.splitlines():
        pid, sep, rest = line.strip().partition("|")
        if not sep or not pid.isdigit():
            continue
        path, _, cmdline = rest.partition("|")
        procs[int(pid)] = (path.strip().lower(), cmdline.strip().lower())
    return procs


def ours_by_cmdline(b: Backend, cmdline: str) -> bool:
    """True when the command line is unmistakably this backend's server, even if the exe path is a venv redirector."""
    script = Path(str(b.cmd[1] if len(b.cmd) > 1 else b.cmd[0])).name.lower()
    return script in cmdline and re.search(rf"--port\s*{b.port}\b", cmdline) is not None


def classify_listeners(b: Backend) -> tuple[set[int], set[int]]:
    """Split whoever listens on this backend's port into (ours, foreign).

    Foreign means we did not spawn it and its executable is not this backend's executable. Foreign listeners are
    never killed: Docker Desktop was found holding :8081, and the old port-based kill would have force-removed it
    and its whole process tree during an ordinary model swap.
    """
    pids = pids_on_port(b.port, fresh=True)
    if b.proc is not None and b.proc.poll() is None:
        return pids | {b.proc.pid}, set()
    if not pids:
        return set(), set()
    procs = proc_exes(pids)
    ours = {pid for pid, (exe, cmd) in procs.items()
            if exe and (exe == b.expected_exe or ours_by_cmdline(b, cmd))}
    return ours, set(pids) - ours


def vram_used_mib() -> int:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=20)
        return int(r.stdout.strip().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return -1


def is_up(b: Backend) -> bool:
    if not pids_on_port(b.port):        # nothing bound: an HTTP probe would burn its whole timeout, so ask netstat
        return False
    try:
        d = http_json(b.url + "/v1/models", timeout=4)
        return isinstance(d, dict) and "data" in d
    except Exception:
        return False


def llama_child_loaded(b: Backend) -> bool:
    """The llama.cpp router process itself costs no VRAM; only a loaded child model does."""
    try:
        d = http_json(b.url + "/v1/models", timeout=4)
        return any((m.get("status") or {}).get("value") not in (None, "unloaded") for m in d["data"])
    except Exception:
        return False


def holds_vram(b: Backend) -> bool:
    if not is_up(b):
        return bool(classify_listeners(b)[0])
    return llama_child_loaded(b) if b.kind == "llama" else True


def kill_backend(b: Backend):
    PROVEN.pop(b.name, None)
    ours, foreign = classify_listeners(b)
    if foreign:
        detail = ", ".join(f"pid {p} ({exe or 'path hidden'})"
                           for p, (exe, _cmd) in sorted(proc_exes(foreign).items()))
        event(f"NOT killing :{b.port} - held by {detail}, which is not {b.name}")
    pids = set(ours)
    if b.proc is not None and b.proc.poll() is None:
        pids.add(b.proc.pid)
    for pid in sorted(pids):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=60)
    b.proc = None
    for _ in range(60):
        if not pids_on_port(b.port, fresh=True) - foreign:
            break
        time.sleep(0.5)


def spawn(b: Backend):
    lf = subprocess.DEVNULL
    if b.log:
        # a registry pointing at logs/<name>.log is the normal case, and open(a) will not create the folder -
        # run-router.bat does, but "python proxy.py" on a fresh clone would otherwise die here
        Path(b.log).parent.mkdir(parents=True, exist_ok=True)
        lf = open(b.log, "a", encoding="utf-8", errors="replace")
        lf.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} router spawn: {' '.join(b.cmd)}\n")
        lf.flush()
    b.proc = subprocess.Popen(b.cmd, cwd=b.cwd, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    event(f"spawned {b.name} (pid {b.proc.pid})")


def vram_total_mib() -> int:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=20)
        return int(r.stdout.strip().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return 24576


def wait_vram_for(need_mib: int, timeout: float = 120) -> int:
    """Wait until the card has `need_mib` free. An absolute "used < N" gate is wrong here: the desktop alone
    (browsers, WhatsApp, overlay apps) was measured at 6.8 GB, which would block every swap."""
    total = vram_total_mib()
    deadline = time.time() + timeout
    while time.time() < deadline:
        used = vram_used_mib()
        if used < 0 or total - used >= need_mib:
            return total - used if used >= 0 else -1
        time.sleep(2)
    used = vram_used_mib()
    raise RuntimeError(f"the GPU only has {total - used} MiB free but {need_mib} MiB is needed, and no engine of "
                       f"mine is running - other applications are holding {used} MiB. Close some (browsers and "
                       f"GPU-accelerated apps are the usual culprits), then send again.")


def generation_probe(b: Backend, model_id: str) -> bool:
    """/health lies on Strata (200 from the Python wrapper even with a dead engine); only tokens prove liveness."""
    body = {"model": model_id,
            "messages": [{"role": "user", "content": "Reply with the single word OK."}],
            "max_tokens": 4, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
    d = http_json(b.url + "/v1/chat/completions", body, timeout=300)
    ok = int((d.get("usage") or {}).get("completion_tokens", 0)) > 0
    if ok:
        PROVEN[b.name] = time.time()
    return ok


def wait_ready(b: Backend, model_id: str, progress=lambda m: None):
    deadline = time.time() + b.ready_timeout
    t0 = time.time()
    while True:
        if b.proc is not None and b.proc.poll() is not None:
            raise RuntimeError(f"{b.name} exited with code {b.proc.returncode} during startup - see {b.log}")
        if is_up(b):
            progress(f"warming {model_id} (proving it really decodes)")
            try:
                if generation_probe(b, model_id):
                    event(f"{b.name} ready in {time.time() - t0:.0f}s")
                    return
            except Exception as e:
                progress(f"probe failed ({type(e).__name__}), retrying")
        left = int(deadline - time.time())
        if left <= 0:
            raise TimeoutError(f"{b.name} did not become ready within {b.ready_timeout}s - see {b.log}")
        if b.kind == "strata":
            hint, expect = "loading the model into RAM and VRAM", "1-2 min"
        else:
            hint, expect = "loading the model into VRAM", "15-30 s"
        progress(f"{b.name}: {hint} - {time.time() - t0:.0f}s so far, typically {expect}. Leave it running; "
                 f"{left}s before the router gives up")
        time.sleep(3)


class EngineBusy(RuntimeError):
    """Another request is mid-answer on the resident engine; a swap now would kill it."""


def resident_and_proven(b: Backend) -> bool:
    return status_of(b) == "hot" and time.time() - PROVEN.get(b.name, 0) < PROVEN_FRESH_S


def ensure(b: Backend, model_id: str, progress=lambda m: None):
    """Make b the one and only resident engine, then prove it can generate."""
    if resident_and_proven(b):
        return
    with SWAP:
        if resident_and_proven(b):          # another request may have warmed it while we waited for the lock
            return
        SWAPPING.update(active=True, message=f"preparing {model_id}", since=time.time())

        def report(msg: str):
            # publish to the polled state too: a slow load must read as progress, not as a hang
            SWAPPING["message"] = msg
            progress(msg)

        try:
            with BUSY_LOCK:
                busy = sorted(n for n, c in BUSY.items() if c and n != b.name)
            if busy:
                raise EngineBusy(f"{', '.join(busy)} is mid-answer for another request. One GPU means one engine: "
                                 f"wait for it to finish, or press Stop, then send again.")
            if not is_up(b):
                _, foreign = classify_listeners(b)
                if foreign:
                    pid = sorted(foreign)[0]
                    exe, _cmd = proc_exes(foreign).get(pid, ("", ""))
                    raise RuntimeError(f"{b.name} cannot start: port {b.port} is already taken by {exe or 'an unknown application'} (pid {pid}). "
                                       f"Close that application, or move this backend to a free port in "
                                       f"registry.json. The router only kills a listener that is this backend's own "
                                       f"server, by executable path or by its command line naming our script on this port.")
            try:
                if holds_vram(b) and generation_probe(b, model_id):
                    return
            except Exception:
                report(f"{b.name} answers /v1/models but did not decode - restarting it")
                kill_backend(b)
            for other in BACKENDS:
                if other is not b and holds_vram(other):
                    report(f"stopping {other.name} to free the GPU")
                    event(f"killing {other.name} (VRAM handover to {b.name})")
                    kill_backend(other)
            needs = b.needs_for(model_id)
            report(f"waiting for {needs} MiB of free VRAM")
            free = wait_vram_for(needs)
            report(f"GPU has {free} MiB free, starting {b.name}")
            if not is_up(b):
                spawn(b)
            wait_ready(b, model_id, report)
        finally:
            SWAPPING.update(active=False, message="", since=0.0)


@contextmanager
def generating(name: str):
    """Marks a backend busy so a concurrent swap request refuses instead of killing the engine mid-answer."""
    with BUSY_LOCK:
        BUSY[name] = BUSY.get(name, 0) + 1
    try:
        yield
    finally:
        with BUSY_LOCK:
            BUSY[name] = max(0, BUSY.get(name, 0) - 1)


def route(model_id: str) -> Backend | None:
    for b in BACKENDS:
        if model_id in b.labels:
            return b
    ll = next((x for x in BACKENDS if x.kind == "llama"), None)
    if ll is not None and model_id and is_up(ll):
        try:                      # a preset added to models-preset.ini after the proxy started
            d = http_json(ll.url + "/v1/models", timeout=4)
            if any(m.get("id") == model_id for m in d["data"]):
                ll.labels[model_id] = "(from llama.cpp preset)"
                ll.order.append(model_id)
                return ll
        except Exception:
            pass
    return None


def status_of(b: Backend) -> str:
    """From the monitor's cache - probing inline made /v1/models slower than clients' timeouts."""
    with STATUS_LOCK:
        s = STATUS.get(b.name, {})
    if not s.get("up"):
        return "cold"
    if b.kind == "llama":
        return "hot" if s.get("child") else "idle"
    return "hot"


# -------------------------------------------------------------------------------------------------- monitor
STATUS: dict[str, dict] = {}
STATUS_LOCK = threading.Lock()
VRAM_CACHE = {"mib": -1}


def probe_once():
    vram = vram_used_mib()
    for b in BACKENDS:
        up, child, loaded = False, False, set()
        if not pids_on_port(b.port):        # the 1 s netstat cache: cold ports cost ~0 instead of 2.5 s each
            d = None
        else:
            try:
                d = http_json(b.url + "/v1/models", timeout=2.5)
            except Exception:
                d = None
        if isinstance(d, dict):
            up = "data" in d
            for m in d.get("data", []) if up else []:
                if (m.get("status") or {}).get("value") not in (None, "unloaded"):
                    loaded.add(m.get("id"))
            child = bool(loaded)
        pid = b.proc.pid if (b.proc and b.proc.poll() is None) else None
        if up and pid is None:          # a server the user started by hand: show the pid that answers the port
            listeners = sorted(pids_on_port(b.port))
            pid = listeners[0] if listeners else None
        with STATUS_LOCK:
            STATUS[b.name] = {"up": up, "child": child, "loaded": loaded, "pid": pid, "ts": time.time()}
    VRAM_CACHE["mib"] = vram


def shown_pid(b: Backend) -> int | None:
    with STATUS_LOCK:
        return STATUS.get(b.name, {}).get("pid")


def monitor():
    while True:
        try:
            probe_once()
        except Exception as e:
            event(f"monitor: {type(e).__name__}: {e}")
        time.sleep(3)


def status_of_model(b: Backend, mid: str) -> str:
    """The llama.cpp router holds many presets but only ever one loaded child, so a backend-wide status
    would mark all of them hot. Only the resident model is hot; its unloaded siblings are idle."""
    with STATUS_LOCK:
        s = STATUS.get(b.name, {})
    if not s.get("up"):
        return "cold"
    if b.kind == "llama":
        return "hot" if mid in s.get("loaded", ()) else "idle"
    return "hot"


def models_payload() -> dict:
    data = []
    for b in BACKENDS:
        for mid, label in b.labels.items():
            data.append({"id": mid, "object": "model", "owned_by": b.name, "created": 0,
                         "status": status_of_model(b, mid), "engine": b.name, "engine_kind": b.kind,
                         "label": label})
    order = {"hot": 0, "idle": 1, "cold": 2}
    data.sort(key=lambda m: (order[m["status"]], m["engine"]))
    return {"object": "list", "data": data}


def state_payload() -> dict:
    return {"proxy_port": PORT, "swapping": SWAPPING["active"], "message": SWAPPING["message"],
            "swap_seconds": round(time.time() - SWAPPING["since"]) if SWAPPING["active"] else 0,
            "live": live_state(),
            "vram_used_mib": VRAM_CACHE["mib"],
            "backends": [{"name": b.name, "kind": b.kind, "port": b.port, "status": status_of(b),
                          "pid": shown_pid(b),
                          "busy": BUSY.get(b.name, 0),
                          "models": list(b.labels)} for b in BACKENDS],
            "events": EVENTS[-20:][::-1]}


# ------------------------------------------------------------------------------ traffic and hardware
TRAFFIC: dict = {"live": None, "recent": []}
TRAFFIC_LOCK = threading.Lock()
SEEN: set = set()          # (method, path, user-agent) already logged, so the access log stays readable


def traffic_start(b: Backend, model: str, stream: bool) -> dict:
    rec = {"t": time.time(), "engine": b.name, "model": model, "stream": stream,
           "events": 0, "bytes": 0, "first": None}
    with TRAFFIC_LOCK:
        TRAFFIC["live"] = rec
    return rec


def note(rec: dict | None, text: str):
    """One SSE `data:` event is one streamed chunk, which for these engines is near enough to one token to make
    the Speed tile honest - and unlike the engines' own counters it works for llama.cpp and Strata alike."""
    if rec is None:
        return
    if rec["first"] is None:
        rec["first"] = time.time()
    rec["events"] += text.count("data: ")
    rec["bytes"] += len(text)


def traffic_stop(rec: dict, tokens: int | None = None) -> dict:
    now, start = time.time(), rec["first"] or rec["t"]
    n = max(0, rec["events"] - 1) if tokens is None else tokens      # -1: the trailing [DONE] is not a token
    dur = now - start
    out = {"time": round(rec["t"], 1), "engine": rec["engine"], "model": rec["model"], "stream": rec["stream"],
           "output_tokens": n, "duration_s": round(dur, 1),
           # a three-word answer is over in half a second and still a real rate; below that the first token dominates
           "decode_tok_s": round(n / dur, 1) if dur > 0.4 and n >= 3 else None}
    with TRAFFIC_LOCK:
        TRAFFIC["recent"].insert(0, out)
        del TRAFFIC["recent"][12:]
        if TRAFFIC["live"] is rec:
            TRAFFIC["live"] = None
    return out


def live_rate() -> float | None:
    with TRAFFIC_LOCK:
        r = TRAFFIC["live"]
    if not r or r["first"] is None:
        return None
    dt = time.time() - r["first"]
    return round(r["events"] / dt, 1) if dt > 1 else None


def live_state() -> dict:
    """What the router is doing this second, for the Monitor tab.

    Neither engine can tell us: a request handed to llama.cpp is invisible to Strata's /metrics, and a model
    being loaded has no server to ask yet. The proxy owns both the lifecycle and the stream, so it is the only
    place that can answer - and it answers from memory, which is why /router/metrics can be polled every second.
    """
    with TRAFFIC_LOCK:
        r = dict(TRAFFIC["live"]) if TRAFFIC["live"] else None
    if SWAPPING["active"]:
        return {"state": "loading", "message": SWAPPING["message"],
                "seconds": round(time.time() - SWAPPING["since"])}
    if r:
        start = r["first"] or r["t"]
        dt = time.time() - start
        # a rate the same second the answer started has to come from a short window, or the tile reads "–" until it ends
        rate = round(max(0, r["events"] - 1) / dt, 1) if r["first"] and dt > 0.5 else None
        return {"state": "generating" if r["first"] else "prefill", "engine": r["engine"], "model": r["model"],
                "stream": r["stream"], "tokens": max(0, r["events"] - 1), "bytes": r["bytes"],
                "seconds": round(dt, 1), "tok_s": rate}
    return {"state": "idle"}


# Hardware series the tiles read, in the order the About page lists them.
HW_KEYS = ("gpu_util", "gpu_mem_used", "gpu_mem_total", "gpu_temp", "gpu_power", "gpu_power_limit",
           "gpu_pcie_gen", "gpu_pcie_gen_max", "gpu_pcie_width", "gpu_pcie_rx_mb", "cpu", "ram_used",
           "ram_total", "disk_read_mb")


def metrics_payload() -> dict:
    """The Monitor tab's feed: router state and hardware, from caches only - no backend is touched, so a cold
    engine costs nothing here and the 1 s poll never queues behind a 4 s probe."""
    snap = TEL.snapshot() if TEL else {"now": {}, "static": {}, "history": {}}
    return {"live": live_state(), "swapping": SWAPPING["active"], "message": SWAPPING["message"],
            "swap_seconds": round(time.time() - SWAPPING["since"]) if SWAPPING["active"] else 0,
            "vram_used_mib": VRAM_CACHE["mib"], "pc": {**snap["static"], **{k: snap["now"].get(k) for k in HW_KEYS}},
            "history": snap["history"],
            "backends": [{"name": b.name, "kind": b.kind, "port": b.port, "status": status_of(b),
                          "pid": shown_pid(b), "busy": BUSY.get(b.name, 0), "models": list(b.labels)}
                         for b in BACKENDS],
            "requests": TRAFFIC["recent"], "events": EVENTS[-10:][::-1]}


TEL = Telemetry(extra=lambda: {"tok_s": live_rate()}) if Telemetry else None


# ------------------------------------------------------------------------------- the About page's data
def argv_of(cmd: list) -> dict:
    """A command line as {flag: value}, so the page can show what an engine actually starts with."""
    out, i = {}, 0
    while i < len(cmd):
        a = str(cmd[i])
        if a.startswith("--"):
            nxt = str(cmd[i + 1]) if i + 1 < len(cmd) else ""
            if nxt.startswith("--") or nxt == "":
                out[a[2:]] = True
                i += 1
            else:
                out[a[2:]] = nxt
                i += 2
        else:
            i += 1
    return out


def backend_config(b: Backend) -> dict:
    """The engine's parameters read off disk. The engines are cold most of the day, so an About page that only
    asked a live server would be empty most of the day too."""
    if b.kind == "strata":
        cfg = argv_of(b.cmd).get("config")
        try:
            d = json.loads(Path(str(cfg)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {"config": cfg, "model": d.get("model"), "args": argv_of([str(x) for x in d.get("args") or []])}
    ini = argv_of(b.cmd).get("models-preset")
    try:
        text = Path(str(ini)).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    sections: dict[str, dict] = {}
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            sections[cur] = {}
        elif cur and "=" in s and not s.startswith((";", "#")):
            k, v = s.split("=", 1)
            sections[cur][k.strip()] = v.strip()
    return {"config": ini, "presets": sections}


def live_facts(b: Backend) -> dict:
    """Whatever the running engine will volunteer: Strata 0.1.13 has /metrics, 0.1.2 only /health, llama.cpp has
    /props. Each is tried and ignored on failure - that is why this is a loop and not one call."""
    if not pids_on_port(b.port):        # cold backend: three dead connects would cost ~6 s of this page's time
        return {}
    out: dict = {}
    for p, key in (("/metrics", "engine"), ("/health", None), ("/props", None)):
        try:
            d = http_json(b.url + p, timeout=2.5)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if key and isinstance(d.get(key), dict):
            out.update(d[key])
            reqs = d.get("requests") or []
            if reqs:
                out["last_request"] = reqs[0]
        elif not key:
            out.update({k: v for k, v in d.items()
                        if k in ("status", "model", "max_context", "images", "api_key", "model_path", "n_ctx",
                                 "build_info", "role", "max_instances", "model_alias")})
    return out


def is_adopted(b: Backend) -> bool:
    """A server the router did not spawn: the page should say so, because the router can still stop it."""
    return shown_pid(b) is not None and (b.proc is None or b.proc.poll() is not None)


def about_payload() -> dict:
    snap = TEL.snapshot() if TEL else {"now": {}, "static": {}, "history": {}}
    backends = []
    for b in BACKENDS:
        cfg, live = backend_config(b), live_facts(b)
        presets = cfg.get("presets") or {}
        backends.append({"name": b.name, "kind": b.kind, "port": b.port, "status": status_of(b),
                         "pid": shown_pid(b), "adopted": is_adopted(b),
                         "busy": BUSY.get(b.name, 0), "cwd": b.cwd, "log": b.log,
                         "needs_mib": b.needs_mib, "ready_timeout": b.ready_timeout,
                         "openai_url": f"{b.url}/v1", "anthropic_url": b.url,
                         "server_flags": argv_of(b.cmd), "config": cfg.get("config"),
                         "model_file": cfg.get("model"), "args": cfg.get("args"),
                         "engine": live,
                         "models": [{"id": mid, "status": status_of_model(b, mid),
                                     "label": b.labels.get(mid, ""), "params": presets.get(mid, {})}
                                    for mid in b.order]})
    hw = HW_KEYS
    return {"proxy": {"port": PORT, "openai_url": f"http://127.0.0.1:{PORT}/v1",
                      "anthropic_url": f"http://127.0.0.1:{PORT}", "swapping": SWAPPING["active"],
                      "message": SWAPPING["message"], "vram_used_mib": VRAM_CACHE["mib"],
                      "now": time.strftime("%Y-%m-%d %H:%M:%S")},
            "pc": {**snap["static"], **{k: snap["now"].get(k) for k in hw}},
            "live": live_state(), "history": snap["history"], "backends": backends,
            "requests": TRAFFIC["recent"], "events": EVENTS[-25:][::-1]}


# ---------------------------------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "local-router/1.0"

    def log_message(self, *a):
        pass

    def _json(self, code: int, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _sse(self, text: str):
        self.wfile.write(text.encode("utf-8", "replace"))
        self.wfile.flush()

    def _body(self) -> bytes:
        n = int(self.headers.get("content-length") or 0)
        return self.rfile.read(n) if n else b""

    def _page(self, name: str):
        f = UI / name
        if not f.exists():
            return self._json(404, {"error": {"message": f"missing UI file {f}"}})
        return self._send(f.read_bytes(), "text/html; charset=utf-8", "no-store")

    def _asset(self, subdir: str, name: str):
        """One file out of ui/<subdir>/ for the /web/ and /fonts/ routes.

        The name comes straight off the URL, so it is checked twice: no ".." at all, and the resolved path
        still under the folder we mean. A request that fails either gets a 404, never a file outside ui/.
        """
        base = (UI / subdir).resolve()
        if ".." in name or "/" in name.replace("\\", "/").strip("/"):
            return self._json(404, {"error": {"message": "bad asset path"}})
        f = (base / name).resolve()
        if not str(f).lower().startswith(str(base).lower()) or not f.is_file():
            return self._json(404, {"error": {"message": f"no asset {name}"}})
        kind = MIME.get(f.suffix.lower(), "application/octet-stream")
        return self._send(f.read_bytes(), kind, "no-cache")

    def _send(self, raw: bytes, kind: str, cache: str):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        return self.wfile.write(raw)

    def do_OPTIONS(self):
        self.note_request()
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def note_request(self):
        """One line per distinct (method, path, client) pair.

        A third-party app that reports "server unavailable" while the router is provably up is asking for
        something we do not route, or reaching a loopback we do not bind; its User-Agent and the exact paths it
        probes are the whole diagnosis, and deduplicating keeps our own 3 s polls from flooding the log.
        """
        path = self.path.split("?")[0]
        agent = self.headers.get("user-agent", "")
        key = (self.command, path, agent)
        if key in SEEN:
            return
        SEEN.add(key)
        origin = self.headers.get("origin")
        event(f"{self.command} {path} from {self.client_address[0]} ({agent[:64] or 'no user-agent'})"
              + (f" origin={origin}" if origin else ""))

    def do_GET(self):
        self.note_request()
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._page("index.html")
        if path.startswith("/web/") or path.startswith("/fonts/"):
            sub, _, name = path[1:].partition("/")
            return self._asset(sub, name)
        if path in ("/v1/models", "/models"):     # clients that append "models" to a base URL already ending in /v1
            return self._json(200, models_payload())
        if path == "/router/state":
            return self._json(200, state_payload())
        if path == "/router/metrics":
            return self._json(200, metrics_payload())
        if path == "/about.json":
            return self._json(200, about_payload())
        if path in ("/about", "/monitor"):
            return self._page("about.html")
        if path == "/health":
            hot = [b.name for b in BACKENDS if status_of(b) == "hot"]
            return self._json(200, {"status": "ok", "hot": hot, "swapping": SWAPPING["active"]})
        if path == "/favicon.ico":                     # every browser asks; answer nothing instead of logging a 404
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return None
        return self._json(404, {"error": {"message": f"no route for {path}"}})

    def do_POST(self):
        self.note_request()
        path = self.path.split("?")[0]
        raw = self._body()
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError as e:
            return self._json(400, {"error": {"message": f"bad JSON: {e}"}})

        if path == "/router/stop":
            for b in BACKENDS:
                if holds_vram(b) or pids_on_port(b.port, fresh=True):
                    event(f"/router/stop: killing {b.name}")
                    kill_backend(b)
            return self._json(200, {"stopped": True, "vram_used_mib": vram_used_mib()})

        if path == "/router/load":
            mid = body.get("model", "")
            b = route(mid)
            if b is None:
                return self._json(404, {"error": {"message": f"unknown model {mid!r}", "known": list_models()}})
            try:
                ensure(b, mid, progress=lambda m: None)
            except EngineBusy as e:
                return self._json(409, {"error": {"message": str(e), "type": "engine_busy"}})
            except Exception as e:
                return self._json(500, {"error": {"message": str(e)}})
            return self._json(200, {"model": mid, "engine": b.name, "status": status_of(b)})

        if path in ("/v1/chat/completions", "/v1/messages"):
            return self._relay(path, body)

        return self._json(404, {"error": {"message": f"no route for {path}"}})

    # ----------------------------------------------------------------------------------------- forwarding
    def _relay(self, path: str, body: dict):
        mid = body.get("model") or ""
        b = route(mid)
        if b is None:
            if path == "/v1/messages":
                b = next((x for x in BACKENDS if x.kind == "strata" and status_of(x) == "hot"), None)
            if b is None:
                return self._json(404, {"error": {"message": f"unknown model {mid!r}", "known": list_models()}})
        if path == "/v1/messages" and b.kind != "strata":
            return self._json(400, {"error": {"message": "the Anthropic endpoint is only served by the Strata "
                                                         "backends; use /v1/chat/completions for llama.cpp models"}})
        body["model"] = mid or b.first_model()      # llama.cpp matches the id against its preset; Strata ignores it

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            last = [0.0]

            def progress(msg: str):
                now = time.time()
                if now - last[0] >= 2.0:        # a comment every 2 s at most, so the client's watchdog stays calm
                    last[0] = now
                    try:
                        self._sse(f": {msg}\n\n")
                    except OSError:
                        pass

            try:
                ensure(b, body["model"], progress)
            except EngineBusy as e:
                self._sse(f"data: {json.dumps({'error': {'message': str(e), 'type': 'engine_busy'}})}\n\n"
                          f"data: [DONE]\n\n")
                return
            except Exception as e:
                err = {"error": {"message": str(e), "type": "engine_swap_failed"}}
                self._sse(f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n")
                return
            rec = traffic_start(b, body["model"], True)
            try:
                with generating(b.name):
                    self._pipe(b, path, body, rec)
            except (BrokenPipeError, ConnectionResetError):
                event(f"client went away during {body['model']}")
            finally:
                traffic_stop(rec)
            return

        try:
            ensure(b, body["model"], progress=lambda m: None)
        except EngineBusy as e:
            return self._json(409, {"error": {"message": str(e), "type": "engine_busy"}})
        except Exception as e:
            return self._json(500, {"error": {"message": str(e), "type": "engine_swap_failed"}})
        rec = traffic_start(b, body["model"], False)
        try:
            with generating(b.name):
                req = urllib.request.Request(b.url + path, data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=3600) as r:
                    payload, code, ctype = r.read(), r.status, r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            payload, code, ctype = e.read(), e.code, "application/json"
        except Exception as e:
            traffic_stop(rec)
            return self._json(502, {"error": {"message": f"{b.name} did not answer: {e}"}})
        try:                      # a non-streamed answer states its own token count; trust it over our estimate
            n = json.loads(payload.decode("utf-8", "replace")).get("usage", {}).get("completion_tokens")
        except (ValueError, AttributeError):
            n = None
        traffic_stop(rec, tokens=n)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _pipe(self, b: Backend, path: str, body: dict, rec: dict | None = None):
        req = urllib.request.Request(b.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=3600) as r:
            read = getattr(r, "read1", r.read)      # read1 returns as soon as any bytes arrive: no buffering lag
            dec = codecs.getincrementaldecoder("utf-8")("replace")   # a token's bytes can straddle two chunks
            while True:
                chunk = read(65536)
                if not chunk:
                    break
                out = dec.decode(chunk)
                if out:
                    note(rec, out)
                    self._sse(out)
            tail = dec.decode(b"", True)
            if tail:
                self._sse(tail)


def list_models() -> list[str]:
    return [m for b in BACKENDS for m in b.labels]


class QuietHTTPServer(ThreadingHTTPServer):
    """Do not print a traceback just because a client hung up. A browser that gives up on a request makes
    wfile.write raise ConnectionAbortedError (WinError 10053), and socketserver's default dumps the whole stack
    into the launcher's console window - which reads as a crash, even though every request had been answered."""

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class HTTPServerV6(QuietHTTPServer):
    """ThreadingHTTPServer defaults to address_family AF_INET, which makes getaddrinfo reject "::1"."""
    address_family = socket.AF_INET6


class Router(QuietHTTPServer):
    """One server object, both loopbacks: some clients resolve localhost to ::1 first and never fall back."""

    def __init__(self, port: int, handler):
        self.v4 = QuietHTTPServer(("127.0.0.1", port), handler)
        self.v4.daemon_threads = True
        self.note = ""
        try:
            self.v6 = HTTPServerV6(("::1", port), handler)
            self.v6.daemon_threads = True
            self.note = f"and http://[::1]:{port}"
        except OSError as e:
            self.v6 = None
            self.note = f"(IPv6 loopback refused: {e.strerror or e} - write 127.0.0.1 in client URLs)"

    def serve_forever(self):
        if self.v6:
            threading.Thread(target=self.v6.serve_forever, daemon=True).start()
        self.v4.serve_forever()

    def shutdown(self):
        self.v4.shutdown()
        if self.v6:
            self.v6.shutdown()


def main():
    event(f"router proxy on http://127.0.0.1:{PORT} - models: {', '.join(list_models())}")
    event(f"about page: http://127.0.0.1:{PORT}/about"
          f"{'' if TEL else ' (hardware tiles off: telemetry.py not importable)'}")
    probe_once()
    threading.Thread(target=monitor, daemon=True).start()
    for b in BACKENDS:
        if status_of(b) != "cold":
            event(f"adopting an already-running {b.name} on port {b.port} (pids {sorted(pids_on_port(b.port, fresh=True))})")
    srv = Router(PORT, Handler)
    event(f"both loopbacks up - http://127.0.0.1:{PORT} {srv.note}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        event("proxy stopped (engines left running; POST /router/stop to free the GPU)")


if __name__ == "__main__":
    main()
