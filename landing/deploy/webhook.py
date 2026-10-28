"""Rebuilds the website when GitHub reports a push to the deploy branch.

A small HTTP server (Python standard library only) that accepts GitHub webhook deliveries on POST /hooks/deploy:

* every delivery must carry ``X-Hub-Signature-256``, an HMAC-SHA256 of the body with the shared secret; anything
  else is refused with 401 before the body is read as JSON;
* ``ping`` answers 200; a ``push`` to another branch or another repository is acknowledged and ignored;
* a push to the deploy branch answers 202 straight away and runs ``deploy.sh`` in the background. One deployment
  runs at a time; pushes that arrive meanwhile are folded into one more run.

* the repository also holds the product, so a push that changes nothing under DEPLOY_PATHS (default ``landing/``)
  is acknowledged and ignored. When GitHub does not list the changed files (more than 20 commits), it deploys.

Settings (environment): WEBHOOK_SECRET (required, at least 24 characters), DEPLOY_BRANCH (default main),
DEPLOY_REPOSITORY (default OfinTech/ofinTech-Dask, compared without regard to case), DEPLOY_PATHS (default
landing/; space-separated prefixes, empty for every push), DEPLOY_COMMAND (default /app/deploy.sh), WEBHOOK_PORT
(default 9000), WEBHOOK_PATH (default /hooks/deploy).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 1024 * 1024
SECRET = os.environ.get("WEBHOOK_SECRET", "").encode()
BRANCH = os.environ.get("DEPLOY_BRANCH", "main")
REPOSITORY = os.environ.get("DEPLOY_REPOSITORY", "OfinTech/ofinTech-Dask").lower()
PATHS = os.environ.get("DEPLOY_PATHS", "landing/").split()
COMMAND = os.environ.get("DEPLOY_COMMAND", "/app/deploy.sh")
PATH = os.environ.get("WEBHOOK_PATH", "/hooks/deploy")


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, flush=True)


def touches_site(payload: dict) -> bool:
    """Whether the pushed commits change a file under one of PATHS (True when GitHub does not say)."""
    if not PATHS:
        return True
    commits = payload.get("commits") or []
    if not commits or len(commits) >= 20:
        return True
    for c in commits:
        for key in ("added", "modified", "removed"):
            if any(str(f).startswith(tuple(PATHS)) for f in c.get(key) or []):
                return True
    return False


def valid_signature(body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


class Deployer:
    """Runs one deployment at a time; a push during a run schedules exactly one more run."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.pending = False
        self.last: dict[str, object] = {}

    def request(self, commit: str) -> bool:
        """True when a deployment starts now, False when it is queued behind the running one."""
        with self.lock:
            if self.running:
                self.pending = True
                log(f"deploy of {commit[:7]} queued behind the running one")
                return False
            self.running = True
        threading.Thread(target=self._loop, args=(commit,), daemon=True).start()
        return True

    def _loop(self, commit: str) -> None:
        while True:
            started = time.time()
            log(f"deploying {commit[:7]}")
            try:
                proc = subprocess.run([COMMAND], capture_output=True, text=True, timeout=900)
                ok = proc.returncode == 0
                out = (proc.stdout + proc.stderr)[-4000:]
            except Exception as exc:  # noqa: BLE001 - reported, the listener keeps running
                ok, out = False, repr(exc)
            self.last = {"commit": commit, "ok": ok, "seconds": round(time.time() - started, 1), "finished": time.time()}
            log(f"deploy {'succeeded' if ok else 'FAILED'} in {self.last['seconds']}s\n{out}")
            with self.lock:
                if not self.pending:
                    self.running = False
                    return
                self.pending = False
                commit = "latest"


DEPLOYER = Deployer()


class Handler(BaseHTTPRequestHandler):
    server_version = "deploy-hook"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:  # one line per request, without client addresses
        log(f"{self.command} {self.path} -> {fmt % args}")

    def reply(self, code: int, message: str) -> None:
        body = json.dumps({"message": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == PATH + "/health":
            return self.reply(200, "ok")
        return self.reply(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != PATH:
            return self.reply(404, "not found")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self.reply(413, "body missing or too large")
        body = self.rfile.read(length)
        if not valid_signature(body, self.headers.get("X-Hub-Signature-256")):
            log("refused a delivery with a missing or wrong signature")
            return self.reply(401, "bad signature")
        event = self.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return self.reply(200, "pong")
        if event != "push":
            return self.reply(202, f"ignored event {event}")
        try:
            payload = json.loads(body)
        except ValueError:
            return self.reply(400, "body is not JSON")
        repo = str((payload.get("repository") or {}).get("full_name", "")).lower()
        if repo != REPOSITORY:
            return self.reply(202, "ignored: another repository")
        if payload.get("ref") != f"refs/heads/{BRANCH}":
            return self.reply(202, f"ignored: not {BRANCH}")
        if payload.get("deleted"):
            return self.reply(202, "ignored: branch deleted")
        if not touches_site(payload):
            return self.reply(202, "ignored: the website did not change")
        started = DEPLOYER.request(str(payload.get("after") or "unknown"))
        return self.reply(202, "deploy started" if started else "deploy queued")


def main() -> int:
    if len(SECRET) < 24:
        print("WEBHOOK_SECRET must be set to a random value of at least 24 characters.", file=sys.stderr)
        return 1
    port = int(os.environ.get("WEBHOOK_PORT", "9000"))
    log(f"listening on :{port}{PATH} for pushes to {REPOSITORY}@{BRANCH}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
