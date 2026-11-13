"""D2 — deployment validation of a real engine process (server mode, isolated temporary data directory).

Starts `python -m desk_engine serve` as a separate process and checks: configuration validation (`check`, and a
refusal to start without a master key), liveness and readiness, metrics scraping with DESK_METRICS_TOKEN (and its
refusal without), graceful shutdown on SIGTERM, monitoring after a restart, and an encrypted backup restored into a
fresh directory that a new process then serves. Nothing outside the temporary directory is touched. Run from engine/:

    python3 ../reports/ai-agent-final-release/evals/d2_deployment.py out.json
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")

TOKEN = "m" * 48


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def env_for(data: Path, port: int, key: str) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("DESK_")}
    e.update(DESK_MODE="server", DESK_DATA_DIR=str(data), DESK_PORT=str(port), DESK_HOST="127.0.0.1",
             DESK_PUBLIC_URL=f"http://127.0.0.1:{port}", DESK_ALLOW_HTTP="1", DESK_MASTER_KEY=key,
             DESK_METRICS_TOKEN=TOKEN)
    return e


def get(url: str, token: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:     # noqa: S310 -- local test server
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except OSError:
        return 0, ""


def post(url: str, body: dict, origin: str, token: str | None = None) -> tuple[int, dict]:
    headers = {"content-type": "application/json", "origin": origin}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:     # noqa: S310 -- local test server
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}


def start(env: dict[str, str]) -> tuple[subprocess.Popen, float]:
    p = subprocess.Popen([sys.executable, "-m", "desk_engine", "serve"], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    t0 = time.perf_counter()
    port = env["DESK_PORT"]
    while time.perf_counter() - t0 < 60:
        if get(f"http://127.0.0.1:{port}/health")[0] == 200:
            return p, time.perf_counter() - t0
        if p.poll() is not None:
            break
        time.sleep(0.1)
    raise RuntimeError("did not start: " + (p.stdout.read() if p.stdout else ""))


def stop(p: subprocess.Popen) -> dict:
    t0 = time.perf_counter()
    p.send_signal(signal.SIGTERM)
    try:
        code = p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        code = "killed"
    log = p.stdout.read() if p.stdout else ""
    # uvicorn shuts down (lifespan, engine.stop_background, database close) and then re-raises the signal, so the
    # exit status is -15 after a clean shutdown; the log line is the evidence that the shutdown ran
    return {"exit_code": code, "seconds": round(time.perf_counter() - t0, 2),
            "traceback_in_log": "Traceback" in log,
            "application_shutdown_complete": "Application shutdown complete" in log,
            "graceful": code in (0, -15) and "Application shutdown complete" in log and "Traceback" not in log}


def main() -> None:
    out: dict = {}
    key = subprocess.check_output([sys.executable, "-m", "desk_engine", "keygen"], text=True).strip()
    with tempfile.TemporaryDirectory() as tmp:
        data, restored = Path(tmp) / "data", Path(tmp) / "restored"
        port = free_port()
        env = env_for(data, port, key)
        bad = dict(env)
        bad.pop("DESK_MASTER_KEY")
        r = subprocess.run([sys.executable, "-m", "desk_engine", "serve"], env=bad, capture_output=True, text=True,
                           timeout=60)
        out["refuses_to_start_without_master_key"] = r.returncode != 0
        r = subprocess.run([sys.executable, "-m", "desk_engine", "check"], env=env, capture_output=True, text=True,
                           timeout=60)
        out["config_check"] = {"exit_code": r.returncode, "output_tail": r.stdout.strip().splitlines()[-3:]}

        p, secs = start(env)
        base = f"http://127.0.0.1:{port}"
        ready = get(f"{base}/ready")
        out["first_start"] = {"seconds_to_health": round(secs, 2), "ready_status": ready[0],
                              "ready_body": json.loads(ready[1]) if ready[1] else None}
        m = get(f"{base}/api/admin/metrics", TOKEN)
        out["scrape_with_token"] = {"status": m[0], "desk_up": "desk_up 1" in m[1],
                                    "series": sum(1 for x in m[1].splitlines() if x and not x.startswith("#"))}
        out["scrape_without_token"] = get(f"{base}/api/admin/metrics")[0]
        out["scrape_wrong_token"] = get(f"{base}/api/admin/metrics", "x" * 48)[0]
        su = post(f"{base}/api/auth/signup", {"email": "owner@example.test", "name": "Owner",
                                             "password": "Correct-Horse-9"}, base)
        ws = post(f"{base}/api/workspaces", {"name": "Restore Check Ltd", "base_currency": "GBP"}, base,
                  su[1].get("token"))
        out["data_created"] = {"signup": su[0], "workspace": ws[0]}
        start_1 = next(x for x in m[1].splitlines() if x.startswith("desk_process_start_time_seconds"))
        out["shutdown_1"] = stop(p)

        p, secs = start(env)
        m2 = get(f"{base}/api/admin/metrics", TOKEN)
        start_2 = next(x for x in m2[1].splitlines() if x.startswith("desk_process_start_time_seconds"))
        out["after_restart"] = {"seconds_to_health": round(secs, 2), "scrape_status": m2[0],
                                "desk_up": "desk_up 1" in m2[1], "start_time_changed": start_1 != start_2,
                                "ready_status": get(f"{base}/ready")[0]}
        out["shutdown_2"] = stop(p)

        bkey = Path(tmp) / "backup.key"
        bkey.write_text(subprocess.check_output([sys.executable, "-m", "desk_engine", "keygen"], text=True))
        from desk_engine.backup import SUFFIX
        bfile = Path(tmp) / f"nightly{SUFFIX}"
        b = subprocess.run([sys.executable, "-m", "desk_engine", "backup", str(bfile), "--key-file", str(bkey)],
                           env=env, capture_output=True, text=True, timeout=120)
        c = subprocess.run([sys.executable, "-m", "desk_engine", "backup-check", str(bfile), "--key-file", str(bkey)],
                           env=env, capture_output=True, text=True, timeout=120)
        restored.mkdir()
        env_r = env_for(restored, free_port(), key)
        rr = subprocess.run([sys.executable, "-m", "desk_engine", "restore", str(bfile), "--key-file", str(bkey)],
                            env=env_r, capture_output=True, text=True, timeout=120)
        out["backup"] = {"backup_exit": b.returncode, "check_exit": c.returncode, "restore_exit": rr.returncode,
                         "check_tail": (c.stdout + c.stderr).strip().splitlines()[-1:],
                         "restore_tail": (rr.stdout + rr.stderr).strip().splitlines()[-2:]}
        if rr.returncode == 0:
            p, secs = start(env_r)
            out["restored_instance"] = {"health": True, "ready_status": get(f"http://127.0.0.1:{env_r['DESK_PORT']}/ready")[0],
                                     "workspaces_restored": None}
            out["shutdown_3"] = stop(p)
    print(json.dumps(out, indent=1))
    json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
