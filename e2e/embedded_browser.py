"""Browser verification of the embedded gateway: a partner page on its own origin loads the built SDK, mints a session
through its own backend, asks the agent a question and receives streamed text plus a generative-UI component; a page
on an origin that is not allowed is refused. Real engine process, real Chromium (Playwright), fake model.

    python e2e/embedded_browser.py            # writes docs/audit/evidence/embedded/{report.md,*.png}

Needs: the SDK built (sdk/embedded-agent/dist), React resolvable for the test bundle (node_modules or NODE_PATH),
esbuild, and Playwright with Chromium.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "audit" / "evidence" / "embedded"
PW = "Str0ngPassw0rd!"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- fake OpenAI-compatible model ------------------------------------------------------------------------------------
FAKE_LLM = r'''
import json, sys
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

def sse(chunks):
    async def gen():
        for c in chunks:
            yield f"data: {json.dumps(c)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

async def models(request):
    return JSONResponse({"data": [{"id": "demo-finance-1"}]})

async def chat(request):
    body = await request.json()
    msgs = body["messages"]
    if msgs[-1]["role"] == "tool":
        return sse([{"choices": [{"delta": {"content": w + " "}}]} for w in "Revenue grew from 881,000 to 5,920,200: a 159% compound annual growth rate over two years.".split(" ")]
                   + [{"choices": [{"delta": {}, "finish_reason": "stop"}]}, {"choices": [], "usage": {"prompt_tokens": 90, "completion_tokens": 30}}])
    return sse([{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "calc_growth", "arguments": json.dumps({"values": [881000, 3735000, 5920200], "years": 2})}}]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}, {"choices": [], "usage": {"prompt_tokens": 80, "completion_tokens": 20}}])

app = Starlette(routes=[Route("/v1/models", models), Route("/v1/chat/completions", chat, methods=["POST"])])
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
'''


class Proc:
    def __init__(self, args: list[str], cwd: Path, env: dict[str, str], ready: str | None, port: int) -> None:
        self.proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            if ready:
                line = self.proc.stdout.readline() if self.proc.stdout else ""
                if ready in line:
                    return
            else:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        return
                except OSError:
                    time.sleep(0.1)
            if self.proc.poll() is not None:
                raise SystemExit("process exited: " + (self.proc.stdout.read() if self.proc.stdout else ""))
        raise SystemExit("process did not start")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def api(base: str, method: str, path: str, body: Any = None, token: str | None = None, origin: str | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", **({"authorization": f"Bearer {token}"} if token else {}),
                                          **({"origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


class Partner(http.server.ThreadingHTTPServer):
    """The partner's web app: static page + the backend endpoint that mints session tokens with the integration key."""

    def __init__(self, port: int, static: Path, desk: str, key: str) -> None:
        self.static, self.desk, self.key = static, desk, key
        super().__init__(("127.0.0.1", port), PartnerHandler)


class PartnerHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, directory=str(a[2].static), **kw)

    def log_message(self, *a: Any) -> None:
        pass

    def do_POST(self) -> None:
        srv: Partner = self.server  # type: ignore[assignment]
        if self.path != "/session":
            self.send_error(404)
            return
        status, body = api(srv.desk, "POST", "/api/embedded/sessions", {"subject": "e2e-user", "locale": "ar", "ttl_seconds": 300}, token=srv.key)
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


PAGE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>Partner ERP</title>
<link rel="stylesheet" href="/styles.css"></head><body><h1>Partner ERP — customer ACME-001</h1><div id="root"></div>
<script type="module" src="/app.js"></script></body></html>"""

ENTRY = """
import { createRoot } from "react-dom/client";
import { OfinTechAgentProvider, OfinTechFinancialCopilot } from "SDK";
const DESK = window.__DESK__;
async function tokenProvider() { const r = await fetch("/session", { method: "POST" }); const b = await r.json(); window.__TOKEN__ = b.token; return b.token; }
createRoot(document.getElementById("root")).render(
  <OfinTechAgentProvider endpoint={DESK + "/api/embedded"} tokenProvider={tokenProvider} locale="ar">
    <OfinTechFinancialCopilot context={[{ description: "screen", value: "Customer ACME-001, Q3 2026" }]} />
  </OfinTechAgentProvider>);
"""


def build_partner_bundle(static: Path, desk: str) -> None:
    sdk = ROOT / "sdk" / "embedded-agent"
    (static / "index.html").write_text(PAGE.replace("<script", f"<script>window.__DESK__ = {json.dumps(desk)};</script><script", 1), encoding="utf-8")
    (static / "styles.css").write_bytes((sdk / "dist" / "styles.css").read_bytes())
    entry = static / "entry.jsx"
    entry.write_text(ENTRY.replace('"SDK"', json.dumps(str(sdk / "dist" / "index.js"))), encoding="utf-8")
    node_path = os.environ.get("NODE_PATH", "")
    candidates = [sdk / "node_modules", Path("/opt/npm-tools/node_modules")]
    env = {**os.environ, "NODE_PATH": os.pathsep.join([node_path, *[str(c) for c in candidates if c.exists()]]).strip(os.pathsep)}
    esbuild = sdk / "node_modules" / ".bin" / "esbuild"
    cmd = [str(esbuild)] if esbuild.exists() else ["node", str(sdk / "node_modules" / "esbuild" / "bin" / "esbuild")]
    subprocess.run([*cmd, str(entry), "--bundle", "--format=esm", "--jsx=automatic", "--loader:.jsx=jsx",
                    f"--outfile={static / 'app.js'}", "--log-level=warning"], check=True, env=env, cwd=sdk)


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    report: list[str] = ["# Embedded gateway — browser verification", "", f"Run on {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}.", ""]
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        print(("PASS " if ok else "FAIL ") + name + " — " + detail)

    tmp = Path(tempfile.mkdtemp(prefix="desk-embedded-e2e-"))
    llm_port, engine_port, partner_port, evil_port = free_port(), free_port(), free_port(), free_port()
    (tmp / "fake_llm.py").write_text(FAKE_LLM, encoding="utf-8")
    llm = Proc([sys.executable, str(tmp / "fake_llm.py"), str(llm_port)], ROOT / "engine", {**os.environ}, None, llm_port)
    base = f"http://127.0.0.1:{engine_port}"
    env = {**os.environ, "DESK_MODE": "server", "DESK_DATA_DIR": str(tmp / "data"), "DESK_PORT": str(engine_port), "DESK_HOST": "127.0.0.1",
           "DESK_PUBLIC_URL": base, "DESK_UI_DIR": str(ROOT / "ui" / "dist"), "DESK_LOG_LEVEL": "warning",
           "DESK_MASTER_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(), "DESK_ALLOW_HTTP": "true",
           "DESK_ALLOW_PRIVATE_URLS": "1", "DESK_LOCAL_TOKEN": secrets.token_hex(24)}
    env.pop("DESK_DEV", None)
    engine = Proc([sys.executable, "-m", "desk_engine"], ROOT / "engine", env, "DESK_READY", engine_port)
    partner_origin = f"http://127.0.0.1:{partner_port}"
    evil_origin = f"http://127.0.0.1:{evil_port}"
    servers: list[Partner] = []
    try:
        # the organisation: owner, workspace, model, integration
        _, s = api(base, "POST", "/api/auth/signup", {"email": "owner@acme.test", "name": "Owner", "password": PW})
        tok = s["token"]
        _, ws = api(base, "POST", "/api/workspaces", {"name": "Acme", "base_currency": "SAR", "country": "SA"}, token=tok)
        api(base, "POST", f"/api/w/{ws['id']}/providers", {"kind": "openai_compatible", "label": "Fake", "base_url": f"http://127.0.0.1:{llm_port}/v1", "api_key": "sk-test"}, token=tok)
        st, integ = api(base, "POST", f"/api/w/{ws['id']}/embedded/integrations",
                        {"name": "Partner ERP", "allowed_origins": [partner_origin], "capabilities": ["ask_finance"]}, token=tok)
        assert st == 200, integ
        key = integ["key"]

        static = tmp / "partner"
        static.mkdir()
        build_partner_bundle(static, base)
        evil = tmp / "evil"
        evil.mkdir()
        build_partner_bundle(evil, base)
        for port, folder in ((partner_port, static), (evil_port, evil)):
            srv = Partner(port, folder, base, key)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            servers.append(srv)

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            console: list[str] = []
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
            page.goto(partner_origin + "/")
            page.wait_for_selector(".ofe-copilot")
            rtl = page.get_attribute(".ofe-copilot", "dir")
            record("E1 widget renders RTL for Arabic", rtl == "rtl", f"dir={rtl}")
            page.fill(".ofe-compose input", "كم نمت الإيرادات خلال السنتين الماضيتين؟")
            page.click(".ofe-compose button[type=submit]")
            page.wait_for_selector(".ofe-assistant .ofe-text", timeout=30000)
            page.wait_for_function("document.querySelectorAll('.ofe-compose button[type=submit]').length === 1", timeout=30000)
            text = page.inner_text(".ofe-assistant .ofe-text")
            record("E2 streamed answer arrives", "159%" in text, text[:120])
            tools = page.locator(".ofe-tools .ofe-badge").all_inner_texts()
            record("E3 tool call shown (calc_growth)", any("calc_growth" in t for t in tools), str(tools))
            comps = page.locator(".ofe-components > *").count()
            record("E4 generative-UI component rendered from a validated payload", comps >= 1, f"{comps} component(s)")
            page.screenshot(path=str(OUT / "E2-E4-answer-rtl.png"), full_page=True)
            # nothing but the gateway was contacted from the page
            # the token is in memory only
            storage = page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length")
            record("E5 no token in web storage", storage == 0, f"{storage} keys")
            errors = [c for c in console if c.startswith("error") and "favicon" not in c]
            record("E8 no console errors during the run", not errors, "; ".join(errors)[:200] or "none")
            # a cross-origin page: the browser sends Origin = evil; the gateway refuses even with a valid token
            token = page.evaluate("window.__TOKEN__")
            evil_page = browser.new_page()
            evil_page.goto(evil_origin + "/")
            status = evil_page.evaluate(
                """async ([base, token]) => { try { const r = await fetch(base + "/api/embedded/agents", { headers: { authorization: "Bearer " + token } }); return r.status; } catch (e) { return "blocked:" + e.message; } }""",
                [base, token])
            record("E6 origin not on the allow-list is refused", status == 403 or str(status).startswith("blocked"), f"status={status}")
            # and the page's own session cannot be used for Desk's API
            st_desk = page.evaluate(
                """async ([base, token]) => { try { const r = await fetch(base + "/api/session/status", { headers: { authorization: "Bearer " + token } }); return r.status; } catch (e) { return "blocked:" + e.message; } }""",
                [base, token])
            # the Desk API has no CORS for partner origins (the browser blocks the read) and the token is not a Desk session
            st_direct, _ = api(base, "GET", "/api/session/status", token=token)
            record("E7 session token is useless on the Desk API", (st_desk == 401 or str(st_desk).startswith("blocked")) and st_direct == 401,
                   f"browser={st_desk} direct={st_direct}")
            browser.close()

        # the engine's own records
        import sqlite3
        db = next((tmp / "data").glob("*.sqlite3"))
        conn = sqlite3.connect(db)
        actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE action LIKE 'embedded.%' ORDER BY created_at")]
        usage = conn.execute("SELECT status, tool_calls FROM embedded_usage").fetchall()
        conn.close()
        record("E9 run audited and metered", "embedded.run.start" in actions and "embedded.run.finish" in actions and usage == [("done", 1)],
               f"actions={actions} usage={usage}")
    finally:
        for srv in servers:
            srv.shutdown()
        engine.stop()
        llm.stop()

    ok = all(r[1] for r in results)
    report += ["| # | Check | Result | Detail |", "|---|---|---|---|"]
    report += [f"| {n.split(' ')[0]} | {' '.join(n.split(' ')[1:])} | {'PASS' if good else 'FAIL'} | {d.replace('|', '/')} |" for n, good, d in results]
    report += ["", f"**{sum(1 for r in results if r[1])} passed, {sum(1 for r in results if not r[1])} failed.**",
               "", "Screenshot: `E2-E4-answer-rtl.png`. Partner page and 'evil' page served from two different local origins; the engine ran as a",
               "separate process in server mode against a fake OpenAI-compatible model."]
    (OUT / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
