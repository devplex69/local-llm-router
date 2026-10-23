"""Does a swap request get refused while another engine is mid-answer? Poll /router/state for busy>0, then fire.

Picks the models off the running router itself: the long request goes to whatever is hot, the conflicting one to a
model on a different (cold) engine. So it works against any registry, not just the one it was written on.

Run:  python busytest.py [router_url]      (default http://127.0.0.1:8090)
"""
import json, sys, threading, time, urllib.request

P = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090"


def get(path):
    with urllib.request.urlopen(P + path, timeout=30) as r:
        return json.load(r)


def post(model, prompt, max_tokens, out):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(P + "/v1/chat/completions", json.dumps(body).encode(),
                                                          {"Content-Type": "application/json"}), timeout=600) as r:
            d = json.load(r)
        out.append(("ok", round(time.time() - t, 1), (d["choices"][0]["message"].get("content") or "")[:60],
                    d.get("error")))
    except urllib.error.HTTPError as e:
        out.append(("http", e.code, e.read()[:300].decode("utf-8", "replace"), None))
    except Exception as e:
        out.append(("exc", type(e).__name__, str(e)[:200], None))


models = get("/v1/models")["data"]
state = get("/router/state")
hot = [b["name"] for b in state["backends"] if b["status"] == "hot"]
if not hot:
    sys.exit("no engine is hot - wake one first (POST /router/load, or press Wake in the UI), then run this")
hot_model = next(m["id"] for m in models if m["engine"] == hot[0])
cold = next((m for m in models if m["engine"] != hot[0] and m["status"] != "hot"), None)
if cold is None:
    sys.exit("every model is on the hot engine - register a second backend to test the swap refusal")
cold_model = cold["id"]
print(f"hot engine: {hot[0]} -> long request on {hot_model}; conflicting request for {cold_model}")

long_out, swap_out = [], []
threading.Thread(target=post, args=(hot_model, "Write a detailed 900 word story about a lighthouse keeper.",
                                    1400, long_out)).start()
deadline = time.time() + 120
while time.time() < deadline:
    s = get("/router/state")
    busy = {b["name"]: b["busy"] for b in s["backends"] if b["busy"]}
    if busy:
        print(f"busy={busy} swapping={s['swapping']} -> firing the conflicting request now")
        break
    time.sleep(0.3)
else:
    print("never observed busy>0 - test inconclusive")

post(cold_model, "hi", 8, swap_out)
print("conflicting request ->", swap_out)
for _ in range(400):
    if long_out:
        break
    time.sleep(0.5)
print("long request      ->", long_out)
s = get("/router/state")
print("after:", [(b["name"], b["status"]) for b in s["backends"]], "vram", s["vram_used_mib"])
