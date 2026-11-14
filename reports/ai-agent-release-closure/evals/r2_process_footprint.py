"""R2 — the engine's own footprint under load, measured from outside the process (for container limits).

Starts a scripted OpenAI-compatible model server on 127.0.0.1 (zero latency, no real model), a real
`python -m desk_engine serve` process (server mode, temporary data directory), and drives it over HTTP from this
process with 10, 50 and 100 concurrent users. Samples the engine process's RSS, thread count and CPU from /proc.
Run from engine/:

    python3 ../reports/ai-agent-release-closure/evals/r2_process_footprint.py out.json
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STAGE_SECONDS = 20


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Model(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        self._send({"data": [{"id": "scripted-model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if body["messages"][-1]["role"] == "tool":
            chunks = [{"choices": [{"delta": {"content": "Growth was 20.00%."}}]},
                      {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
        else:
            chunks = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": "calc_growth", "arguments": json.dumps({"values": [100, 120]})}}]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}]
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data.encode())

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def call(method, url, body=None, token=None, origin=None):
    h = {"content-type": "application/json"}
    if token:
        h["authorization"] = f"Bearer {token}"
    if origin:
        h["origin"] = origin
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=h,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 -- local test servers
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def proc_stats(pid):
    st = {}
    for line in open(f"/proc/{pid}/status"):
        k, _, v = line.partition(":")
        if k in ("VmRSS", "Threads"):
            st[k] = int(v.split()[0])
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    st["cpu_s"] = (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
    return st


def main():
    model = ThreadingHTTPServer(("127.0.0.1", free_port()), Model)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    key = subprocess.check_output([sys.executable, "-m", "desk_engine", "keygen"], text=True).strip()
    out = {"model": "scripted OpenAI-compatible server on 127.0.0.1 (zero latency)", "stages": {}}
    with tempfile.TemporaryDirectory() as tmp:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("DESK_")}
        env.update(DESK_MODE="server", DESK_DATA_DIR=str(Path(tmp) / "d"), DESK_PORT=str(port), DESK_HOST="127.0.0.1",
                   DESK_PUBLIC_URL=base, DESK_ALLOW_HTTP="1", DESK_ALLOW_PRIVATE_URLS="1", DESK_LOG_LEVEL="warning")
        env["DESK_MASTER_KEY"] = key           # generated above by `keygen` for this throwaway run
        p = subprocess.Popen([sys.executable, "-m", "desk_engine", "serve"], env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        try:
            for _ in range(300):
                try:
                    if urllib.request.urlopen(base + "/health", timeout=2).status == 200:  # noqa: S310
                        break
                except OSError:
                    time.sleep(0.1)
            _, su = call("POST", base + "/api/auth/signup", {"email": "o@example.test", "name": "O",
                                                            "password": "Correct-Horse-9"}, origin=base)
            tok = su["token"]
            _, w = call("POST", base + "/api/workspaces", {"name": "Load Ltd", "base_currency": "GBP"}, tok, base)
            ws = w["id"]
            s, _ = call("POST", f"{base}/api/w/{ws}/providers", {"kind": "openai_compatible", "label": "Scripted",
                        "base_url": f"http://127.0.0.1:{model.server_port}/v1", "api_key": "k"}, tok, base)
            assert s == 200, s
            out["idle"] = proc_stats(p.pid)
            for users in (10, 50, 100):
                lat, errs, peak = [], [0], {"VmRSS": 0, "Threads": 0}
                stop = time.perf_counter() + STAGE_SECONDS
                cpu0, t0 = proc_stats(p.pid)["cpu_s"], time.perf_counter()

                def user(i):
                    while time.perf_counter() < stop:
                        _, t = call("POST", f"{base}/api/w/{ws}/threads", {}, tok, base)
                        a = time.perf_counter()
                        s, _ = call("POST", f"{base}/api/w/{ws}/threads/{t['id']}/messages", {"text": "growth?"},
                                    tok, base)
                        while True:
                            _, d = call("GET", f"{base}/api/w/{ws}/threads/{t['id']}", token=tok)
                            run = (d or {}).get("run") or {}
                            if run.get("status") in ("done", "error") or time.perf_counter() - a > 60:
                                break
                            time.sleep(0.1)
                        lat.append(time.perf_counter() - a)
                        if s != 200 or run.get("status") != "done":
                            errs[0] += 1

                def sample():
                    while time.perf_counter() < stop + 2:
                        st = proc_stats(p.pid)
                        peak["VmRSS"] = max(peak["VmRSS"], st["VmRSS"])
                        peak["Threads"] = max(peak["Threads"], st["Threads"])
                        time.sleep(0.25)
                sm = threading.Thread(target=sample)
                sm.start()
                with ThreadPoolExecutor(users) as ex:
                    list(ex.map(user, range(users)))
                wall = time.perf_counter() - t0
                sm.join()
                lat.sort()
                q = lambda f: round(lat[min(len(lat) - 1, int(f * (len(lat) - 1)))] * 1000, 1) if lat else None  # noqa: E731
                out["stages"][str(users)] = {"turns": len(lat), "turns_per_s": round(len(lat) / wall, 1),
                                             "errors": errs[0], "p50_ms": q(.5), "p95_ms": q(.95), "p99_ms": q(.99),
                                             "engine_peak_rss_mb": round(peak["VmRSS"] / 1024, 1),
                                             "engine_peak_threads": peak["Threads"],
                                             "engine_cpu_cores": round((proc_stats(p.pid)["cpu_s"] - cpu0) / wall, 2)}
                print(users, json.dumps(out["stages"][str(users)]), flush=True)
            out["after"] = proc_stats(p.pid)
        finally:
            p.terminate()
            out["exit_code"] = p.wait(30)
    model.shutdown()
    json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
