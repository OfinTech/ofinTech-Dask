"""Two organisations on one real Team Server process, over real HTTP.

    python e2e/multi_tenant.py [evidence.json]

Starts the engine (`python -m desk_engine`, server mode, its own data directory, a fake OpenAI-compatible model on
the loopback) and drives it with plain HTTP from several sessions at once: Alpha (owner Alice, viewer Vic) and
Beta (owner Bob, viewer Vera), each with its own synthetic ledger, document, memory and conversations. Every
scenario records the HTTP status, the authorization decision, the audit event it produced (read back through the
admin audit API) and a fingerprint of the authoritative tables of both workspaces taken from the database file
before and after. Nothing here is mocked in-process; the only simulated component is the language model.

Exit code 0 when every scenario passes. The evidence file is what docs/audit/MULTI-TENANT-INTEGRATION-RESULTS.md
reproduces."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PW = "Correct-Horse-9"
AUTHORITATIVE = ("datasets", "close_runs", "close_items", "dashboards", "files", "file_versions", "documents",
                 "memories", "workflows", "connections", "members")


# ---------------------------------------------------------------------------------------------------------------
# a fake model: each request pops the next scripted answer for the workspace named in the request's system prompt
class FakeModel(BaseHTTPRequestHandler):
    scripts: dict[str, list[Any]] = {}
    lock = threading.Lock()
    calls: list[dict[str, Any]] = []

    def log_message(self, *a: Any) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        self._json({"data": [{"id": "fake-model"}]})

    def do_POST(self) -> None:
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/__script":
            with self.lock:
                self.scripts[body["key"]] = list(body["script"])
            self._json({"ok": True})
            return
        key = next((k for k in self.scripts if k in json.dumps(body["messages"][0])), None)
        last = body["messages"][-1]
        with self.lock:
            self.calls.append({"key": key, "n": len(body["messages"])})
            # a deterministic rule for the concurrency scenario: "sum ledger <id>" → one query of that dataset
            if last.get("role") == "user" and isinstance(last.get("content"), str) and "sum ledger " in last["content"]:
                ds = last["content"].split("sum ledger ", 1)[1].split()[0]
                item: Any = [("query_dataset", {"dataset": ds, "measures": [{"column": "amount", "agg": "sum"}]})]
            elif last.get("role") == "tool" and key and self.scripts.get(key) and not isinstance(self.scripts[key][0], str) and False:
                item = self.scripts[key].pop(0)
            elif key and self.scripts.get(key):
                item = self.scripts[key].pop(0)
            else:
                item = "Done."
        chunks: list[dict[str, Any]] = []
        if isinstance(item, str):
            chunks = [{"choices": [{"delta": {"content": item}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
        else:
            for i, (name, args) in enumerate(item):
                chunks.append({"choices": [{"delta": {"tool_calls": [{"index": i, "id": f"c{i}", "function": {"name": name, "arguments": json.dumps(args)}}]}}]})
            chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
        chunks.append({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data.encode())

    def _json(self, obj: Any) -> None:
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------------------------------------------
class Client:
    """One person's browser: its own cookie jar, real TCP connections to the engine."""

    def __init__(self, base: str, name: str) -> None:
        self.host, self.port = base.split("//")[1].split(":")
        self.origin = base
        self.name = name
        self.cookie: str | None = None
        self.bearer: str | None = None

    def request(self, method: str, path: str, body: Any = None, *, files: dict[str, tuple[str, bytes]] | None = None,
                headers: dict[str, str] | None = None) -> tuple[int, Any, dict[str, str]]:
        h = {"origin": self.origin, **(headers or {})}
        if self.bearer:
            h["authorization"] = f"Bearer {self.bearer}"
        elif self.cookie:
            h["cookie"] = f"desk_session={self.cookie}"
        data: bytes | None = None
        if files:
            boundary = "----desk" + secrets.token_hex(8)
            parts = b""
            for field, (fname, content) in files.items():
                parts += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{fname}\"\r\n"
                          f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + b"\r\n"
            data = parts + f"--{boundary}--\r\n".encode()
            h["content-type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            data = json.dumps(body).encode()
            h["content-type"] = "application/json"
        conn = http.client.HTTPConnection(self.host, int(self.port), timeout=30)
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        raw = resp.read()
        rh = {k.lower(): v for k, v in resp.getheaders()}
        if "set-cookie" in rh and "desk_session=" in rh["set-cookie"]:
            value = rh["set-cookie"].split("desk_session=", 1)[1].split(";", 1)[0]
            self.cookie = value or None
        conn.close()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw.decode(errors="replace")
        return resp.status, parsed, rh

    def get(self, path: str) -> tuple[int, Any, dict[str, str]]:
        return self.request("GET", path)

    def post(self, path: str, body: Any = None, **kw: Any) -> tuple[int, Any, dict[str, str]]:
        return self.request("POST", path, body if body is not None else {}, **kw)


# ---------------------------------------------------------------------------------------------------------------
class Run:
    def __init__(self) -> None:
        self.evidence: list[dict[str, Any]] = []
        self.failures: list[str] = []

    def record(self, scenario: str, step: str, *, status: int | str, decision: str, expected: str, ok: bool,
               audit: str | None = None, before: str | None = None, after: str | None = None, note: str = "") -> None:
        self.evidence.append({"scenario": scenario, "step": step, "status": status, "decision": decision,
                              "expected": expected, "result": "PASS" if ok else "FAIL", "audit_event": audit,
                              "db_before": before, "db_after": after, "note": note})
        if not ok:
            self.failures.append(f"{scenario}: {step} -> {status} ({note})")

    def check(self, scenario: str, step: str, cond: bool, status: int | str, decision: str, expected: str, **kw: Any) -> bool:
        self.record(scenario, step, status=status, decision=decision, expected=expected, ok=cond, **kw)
        return cond


def fingerprint(db_path: Path, ws_id: str) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        h = hashlib.sha256()
        for table in AUTHORITATIVE:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE workspace_id = ? ORDER BY 1", (ws_id,))]  # noqa: S608
            h.update(json.dumps(rows, sort_keys=True, default=str).encode())
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'ds_%'"):
            ds = conn.execute("SELECT id FROM datasets WHERE workspace_id = ? AND id = ?", (ws_id, table[3:])).fetchone()
            if ds:
                rows = [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]  # noqa: S608
                h.update(json.dumps(rows, default=str).encode())
        return h.hexdigest()[:16]
    finally:
        conn.close()


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "e2e-output" / "multi-tenant-evidence.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run = Run()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()

    # the fake model
    model_port = free_port()
    model = ThreadingHTTPServer(("127.0.0.1", model_port), FakeModel)
    threading.Thread(target=model.serve_forever, daemon=True).start()

    # the engine, as a separate process, in server mode
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="desk-mt-")
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "DESK_MODE": "server", "DESK_DATA_DIR": tmp, "DESK_PORT": str(port), "DESK_HOST": "127.0.0.1",
           "DESK_PUBLIC_URL": base, "DESK_MASTER_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
           "DESK_ALLOW_PRIVATE_URLS": "1", "DESK_ALLOW_HTTP": "true", "DESK_SESSION_IDLE_MINUTES": "30", "DESK_LOG_LEVEL": "warning"}
    env.pop("DESK_LOCAL_TOKEN", None)
    engine = subprocess.Popen([sys.executable, "-m", "desk_engine"], cwd=ROOT / "engine", env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    db_path = Path(tmp) / "desk.sqlite3"
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            line = engine.stdout.readline() if engine.stdout else ""
            if "DESK_READY" in line:
                break
            if engine.poll() is not None:
                raise SystemExit("engine exited: " + line + (engine.stdout.read() if engine.stdout else ""))
        else:
            raise SystemExit("engine did not start")
        if not db_path.exists():
            db_path = next(Path(tmp).glob("*.sqlite3"))

        def script(key: str, items: list[Any]) -> None:
            c = http.client.HTTPConnection("127.0.0.1", model_port, timeout=10)
            c.request("POST", "/__script", body=json.dumps({"key": key, "script": items}), headers={"content-type": "application/json"})
            c.getresponse().read()

        def wait_run(client: Client, ws: str, tid: str) -> dict[str, Any]:
            end = time.time() + 30
            while time.time() < end:
                s, d, _ = client.get(f"/api/w/{ws}/threads/{tid}")
                if s == 200 and d["run"] and d["run"]["status"] in {"done", "error", "waiting_approval"}:
                    return d
                time.sleep(0.1)
            raise AssertionError("agent run did not finish")

        def turn(client: Client, ws: str, name: str, items: list[Any], text: str = "Go ahead.") -> dict[str, Any]:
            script(name, items)
            s, t, _ = client.post(f"/api/w/{ws}/threads", {})
            assert s == 200, t
            s, _, _ = client.post(f"/api/w/{ws}/threads/{t['id']}/messages", {"text": text})
            assert s == 200
            return wait_run(client, ws, t["id"])

        def audit_event(admin: Client, ws: str, action: str, **match: Any) -> str | None:
            s, rows, _ = admin.get(f"/api/w/{ws}/audit")
            if s != 200:
                return None
            for r in rows:
                if r["action"] == action and all(r.get(k) == v for k, v in match.items()):
                    return r["event_id"]
            return None

        # ---- 1. two organisations, four people, four sessions, at the same time ----------------------------------
        alice, bob = Client(base, "alice"), Client(base, "bob")
        results: dict[str, Any] = {}

        def signup(c: Client, email: str, name: str) -> None:
            results[name] = c.post("/api/auth/signup", {"email": email, "name": name, "password": PW})

        t1 = threading.Thread(target=signup, args=(alice, "alice@alpha.test", "Alice"))
        t1.start(); t1.join()                                     # the first account opens the server; the rest are invited
        ok = results["Alice"][0] == 200
        run.check("1 simultaneous authentication", "Alice signs up (first account)", ok, results["Alice"][0], "allowed", "200")
        s, ws_a, _ = alice.post("/api/workspaces", {"name": "Test Company Alpha", "base_currency": "USD"})
        A = ws_a["id"]
        # Bob joins through an invitation to Alpha, leaves it and founds Beta — the server is invite-only
        s, inv, _ = alice.post(f"/api/w/{A}/invites", {"email": "bob@beta.test", "role": "viewer"})
        token = inv["link"].rsplit("/", 1)[1]
        s, bob_user, _ = bob.post("/api/auth/signup", {"email": "bob@beta.test", "name": "Bob", "password": PW, "invite_token": token})
        bob_id = bob_user["user"]["id"]
        alice.request("DELETE", f"/api/w/{A}/members/{bob_id}")
        s, ws_b, _ = bob.post("/api/workspaces", {"name": "Test Company Beta", "base_currency": "EUR"})
        B = ws_b["id"]

        def invite(owner: Client, ws: str, email: str, name: str, role: str) -> tuple[Client, str]:
            s, inv, _ = owner.post(f"/api/w/{ws}/invites", {"email": email, "role": role})
            c = Client(base, name)
            s, u, _ = c.post("/api/auth/signup", {"email": email, "name": name, "password": PW, "invite_token": inv["link"].rsplit("/", 1)[1]})
            assert s == 200, u
            return c, u["user"]["id"]

        vic, vic_id = invite(alice, A, "vic@alpha.test", "Vic", "viewer")
        vera, vera_id = invite(bob, B, "vera@beta.test", "Vera", "viewer")
        # all four sign in again at the same moment, from fresh clients
        clients = {"Alice": (Client(base, "alice2"), "alice@alpha.test"), "Bob": (Client(base, "bob2"), "bob@beta.test"),
                   "Vic": (Client(base, "vic2"), "vic@alpha.test"), "Vera": (Client(base, "vera2"), "vera@beta.test")}
        statuses: dict[str, int] = {}

        def login(name: str) -> None:
            c, email = clients[name]
            statuses[name] = c.post("/api/auth/login", {"email": email, "password": PW})[0]

        threads = [threading.Thread(target=login, args=(n,)) for n in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        run.check("1 simultaneous authentication", "four people sign in concurrently", all(v == 200 for v in statuses.values()),
                  str(statuses), "allowed", "200 ×4")
        alice, bob, vic, vera = clients["Alice"][0], clients["Bob"][0], clients["Vic"][0], clients["Vera"][0]
        alice_id = alice.get("/api/session")[1]["user"]["id"]
        for who, c, expect in (("Alice", alice, [A]), ("Bob", bob, [B]), ("Vic", vic, [A]), ("Vera", vera, [B])):
            got = [w["id"] for w in c.get("/api/session")[1]["workspaces"]]
            run.check("1 simultaneous authentication", f"{who} sees only their own workspace", got == expect, 200, "allowed", str(expect))

        # ---- data: a model, a ledger, a document, a memory per organisation ---------------------------------------
        for owner, ws, label, key in ((alice, A, "Alpha", "Test Company Alpha"), (bob, B, "Beta", "Test Company Beta")):
            s, d, _ = owner.post(f"/api/w/{ws}/providers", {"kind": "openai_compatible", "label": "Fake", "base_url": f"http://127.0.0.1:{model_port}/v1", "api_key": f"sk-{label.lower()}"})
            assert s == 200, d
            csv = f"account,amount,memo\n4000,{'1234.56' if label == 'Alpha' else '987.65'},{label} sales\n5000,{'200.00' if label == 'Alpha' else '300.00'},{label} rent\n".encode()
            s, up, _ = owner.request("POST", f"/api/w/{ws}/imports", files={"file": (f"{label}-gl.csv", csv)})
            assert s == 200, up
            s, ds, _ = owner.post(f"/api/w/{ws}/imports/{up['upload_id']}/commit", {"name": f"{label} GL", "sheet": None, "header_row": 0})
            assert s == 200, ds
            s, doc, _ = owner.request("POST", f"/api/w/{ws}/documents", files={"files": (f"{label.lower()}-board.md", f"# {label} board\nCEO salary {label}-SECRET-{'111' if label == 'Alpha' else '222'}".encode())})
            assert s == 200 and not doc["failed"], doc
            owner.post(f"/api/w/{ws}/memories", {"content": f"{label} auditor is {label}-AUDITOR-LLP", "scope": "workspace"})
            results[f"ds_{label}"] = ds["id"]
            results[f"doc_{label}"] = doc["added"][0]["id"]
        time.sleep(1.5)   # indexing
        fp_a0, fp_b0 = fingerprint(db_path, A), fingerprint(db_path, B)

        # ---- 2. each organisation reads its own dashboard / overview --------------------------------------------
        for who, c, ws, needle in (("Alice", alice, A, "1,234.56"), ("Bob", bob, B, "987.65"), ("Vic", vic, A, "1,234.56"), ("Vera", vera, B, "987.65")):
            s, d, _ = c.get(f"/api/w/{ws}/datasets/{results['ds_Alpha' if ws == A else 'ds_Beta']}")
            own = s == 200 and any(abs(float(r[1]) - float(needle.replace(",", ""))) < 0.001 for r in d["rows"])
            run.check("2 own financial data", f"{who} reads their ledger", own, s, "allowed", "200 with own figures")
            s2, d2, _ = c.get(f"/api/w/{ws}/dashboards")
            s3, d3, _ = c.get(f"/api/w/{ws}/datasets")
            mine = s2 == 200 and s3 == 200 and all(x["name"].startswith("Alpha" if ws == A else "Beta") for x in d3)
            run.check("2 own financial data", f"{who} lists dashboards and datasets of their own organisation only", mine, f"{s2}/{s3}", "allowed", "200, own names only")

        # ---- 3 & 4. Alpha reaches for Beta's records and documents ----------------------------------------------
        for path, what in ((f"/api/w/{B}/datasets/{results['ds_Beta']}", "Beta ledger by id"),
                           (f"/api/w/{B}/datasets/{results['ds_Beta']}/export.csv", "Beta ledger export"),
                           (f"/api/w/{A}/datasets/{results['ds_Beta']}", "Beta ledger under Alpha's path"),
                           (f"/api/w/{B}/documents/{results['doc_Beta']}", "Beta document"),
                           (f"/api/w/{B}/documents/{results['doc_Beta']}/file", "Beta document file"),
                           (f"/api/w/{A}/documents/{results['doc_Beta']}", "Beta document under Alpha's path"),
                           (f"/api/w/{B}/memories", "Beta memories"), (f"/api/w/{B}/audit", "Beta audit log"),
                           (f"/api/w/{B}/overview", "Beta overview")):
            s, d, _ = alice.get(path)
            leak = "987.65" in json.dumps(d) or "Beta-SECRET" in json.dumps(d) or "Beta-AUDITOR" in json.dumps(d)
            ev = audit_event(bob, B, "access.denied", user_id=alice_id) if path.startswith(f"/api/w/{B}") else None
            run.check("3/4 cross-tenant read", f"Alice → {what}", s == 404 and not leak, s, "denied (404)", "404, no Beta value",
                      audit=ev, note="recorded against Beta" if ev else "")
        s, _, _ = alice.post(f"/api/w/{B}/query", {"dataset": results["ds_Beta"], "measures": [{"column": "amount", "agg": "sum"}]})
        run.check("3/4 cross-tenant read", "Alice queries Beta's ledger", s == 404, s, "denied (404)", "404")
        s, _, _ = alice.request("DELETE", f"/api/w/{B}/datasets/{results['ds_Beta']}")
        run.check("3/4 cross-tenant write", "Alice deletes Beta's ledger", s == 404, s, "denied (404)", "404",
                  before=fp_b0, after=fingerprint(db_path, B))

        # ---- 5. an AI agent tries to cross tenants ----------------------------------------------------------------
        data = turn(alice, A, "Test Company Alpha", [[("query_dataset", {"dataset": results["ds_Beta"], "measures": [{"column": "amount", "agg": "sum"}]}),
                                                      ("read_document", {"document_id": results["doc_Beta"]}),
                                                      ("search_documents", {"query": "Beta-SECRET"}),
                                                      ("search_memory", {"query": "auditor"})], "Done."])
        tools = {m["payload"]["name"]: m["content"] for m in data["messages"] if m["role"] == "tool"}
        leak = any("987.65" in v or "Beta-SECRET" in v or "Beta-AUDITOR" in v for v in tools.values())
        run.check("5 AI cross-tenant", "Alice's agent asks for Beta's ledger, document, text and memory",
                  len(tools) == 4 and not leak and "Alpha-AUDITOR" in tools.get("search_memory", ""), data["run"]["status"],
                  "denied per tool", "4 tools answered, no Beta value, Alpha's own memory found",
                  before=fp_b0, after=fingerprint(db_path, B), note=json.dumps({k: v[:60] for k, v in tools.items()}))

        # ---- 6. a viewer attempts restricted financial operations ----------------------------------------------
        fp_a1 = fingerprint(db_path, A)
        for method, path, body, what in (("POST", f"/api/w/{A}/close", {"month": "2026-09"}, "prepare a close"),
                                         ("DELETE", f"/api/w/{A}/datasets/{results['ds_Alpha']}", None, "delete the ledger"),
                                         ("POST", f"/api/w/{A}/dashboards", {"name": "x", "widgets": []}, "create a dashboard"),
                                         ("POST", f"/api/w/{A}/imports", None, "import data")):
            s, _, _ = vic.request(method, path, body)
            run.check("6 viewer restrictions", f"Vic (viewer) tries to {what}", s == 403, s, "denied (403)", "403",
                      audit=audit_event(alice, A, "access.denied", user_id=vic_id))
        data = turn(vic, A, "Test Company Alpha", [[("prepare_month_end_close", {"month": "2026-09"}), ("create_dashboard", {"name": "v", "template": "pnl", "dataset": results["ds_Alpha"]}),
                                                    ("query_dataset", {"dataset": results["ds_Alpha"], "measures": [{"column": "amount", "agg": "sum"}]})], "Done."])
        tools = {m["payload"]["name"]: m["content"] for m in data["messages"] if m["role"] == "tool"}
        blocked = all(tools[n].startswith(("Unknown tool", "Refused", "Error")) for n in ("prepare_month_end_close", "create_dashboard")) and "1,434.56" in tools["query_dataset"]
        run.check("6 viewer restrictions", "Vic's agent: editor tools refused, reading allowed", blocked, data["run"]["status"], "denied / allowed",
                  "two refusals, one read", before=fp_a1, after=fingerprint(db_path, A), audit=audit_event(alice, A, "tool.denied", user_id=vic_id))
        data = turn(vic, A, "Test Company Alpha", [[("message_colleague", {"member": "alice@alpha.test", "message": "please wire 5,000"})], "Done."])
        s, appr_list, _ = alice.get(f"/api/w/{A}/approvals")
        pending = [a for a in appr_list if a["status"] == "pending"]
        s_v, _, _ = vic.post(f"/api/w/{A}/approvals/{pending[0]['id']}/decide", {"approve": True}) if pending else (0, None, None)
        run.check("6 viewer restrictions", "Vic's write waits for an editor; Vic cannot approve it himself",
                  data["run"]["status"] == "waiting_approval" and s_v == 403, s_v, "denied (403)", "waiting_approval, 403",
                  audit=audit_event(alice, A, "approval.denied", user_id=vic_id))
        if pending:
            alice.post(f"/api/w/{A}/approvals/{pending[0]['id']}/decide", {"approve": False})

        # ---- 7. permissions revoked during an active session ----------------------------------------------------
        ed, ed_id = invite(alice, A, "ed@alpha.test", "Ed", "editor")
        s1, _, _ = ed.post(f"/api/w/{A}/close", {"month": "2026-09"})          # allowed? the ledger has no Sept lines: 4xx from the handler, not 403
        s, _, _ = alice.request("PATCH", f"/api/w/{A}/members/{ed_id}", {"role": "viewer"})
        s2, _, _ = ed.post(f"/api/w/{A}/dashboards", {"name": "x", "widgets": []})
        s3, _, _ = ed.get(f"/api/w/{A}/datasets")
        run.check("7 revocation mid-session", "editor demoted to viewer: next write refused, reads continue", s1 != 403 and s2 == 403 and s3 == 200,
                  f"{s1}/{s2}/{s3}", "denied after demotion", "not-403 / 403 / 200", audit=audit_event(alice, A, "member.role"))
        alice.request("DELETE", f"/api/w/{A}/members/{ed_id}")
        s4, d4, _ = ed.get(f"/api/w/{A}/datasets")
        run.check("7 revocation mid-session", "member removed: workspace gone for that session", s4 == 404 and "Alpha" not in json.dumps(d4), s4, "denied (404)", "404")

        # ---- 8. several people invoke AI tools at the same time -------------------------------------------------
        outcomes: dict[str, Any] = {}

        def agent(name: str, client: Client, ws: str, key: str, ds: str) -> None:
            try:
                d = turn(client, ws, key, [[("query_dataset", {"dataset": ds, "measures": [{"column": "amount", "agg": "sum"}]})], f"{name} done."])
                outcomes[name] = {m["payload"]["name"]: m["content"] for m in d["messages"] if m["role"] == "tool"}
            except Exception as exc:  # noqa: BLE001
                outcomes[name] = {"error": str(exc)}

        script("Test Company Alpha", [])
        script("Test Company Beta", [])

        def agent_noscript(name: str, client: Client, ws: str) -> None:
            try:
                s, t, _ = client.post(f"/api/w/{ws}/threads", {})
                client.post(f"/api/w/{ws}/threads/{t['id']}/messages", {"text": f"{name}: sum ledger {results['ds_Alpha' if ws == A else 'ds_Beta']} please"})
                d = wait_run(client, ws, t["id"])
                outcomes[name] = {m["payload"]["name"]: m["content"] for m in d["messages"] if m["role"] == "tool"}
            except Exception as exc:  # noqa: BLE001
                outcomes[name] = {"error": str(exc)}

        ths = [threading.Thread(target=agent_noscript, args=a) for a in (("Alice", alice, A), ("Bob", bob, B), ("Vic", vic, A), ("Vera", vera, B))]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        ok = all("query_dataset" in outcomes.get(n, {}) for n in ("Alice", "Bob", "Vic", "Vera"))
        ok = ok and "1,434.56" in outcomes["Alice"]["query_dataset"] and "1,434.56" in outcomes["Vic"]["query_dataset"]
        ok = ok and "1,287.65" in outcomes["Bob"]["query_dataset"] and "1,287.65" in outcomes["Vera"]["query_dataset"]
        run.check("8 concurrent AI tools", "four agents at once, each sees only its own ledger", ok, "4 runs", "allowed, scoped",
                  "Alpha sums for Alpha, Beta sums for Beta", note=json.dumps({k: str(v)[:50] for k, v in outcomes.items()}))

        # ---- 9. background jobs keep their tenant ------------------------------------------------------------------
        s, wf, _ = alice.post(f"/api/w/{A}/workflows", {"name": "Alpha nightly", "enabled": True, "definition": {
            "trigger": {"type": "manual"}, "steps": [{"id": "n", "name": "N", "type": "notify", "recipients": "me", "message": "Alpha ran"}]}})
        s, r, _ = alice.post(f"/api/w/{A}/workflows/{wf['id']}/run", {})
        end = time.time() + 15
        while time.time() < end:
            s, rr, _ = alice.get(f"/api/w/{A}/workflow-runs/{r['run_id']}")
            if rr["status"] in {"done", "failed"}:
                break
            time.sleep(0.1)
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute("SELECT workspace_id FROM workflow_runs WHERE id = ?", (r["run_id"],)).fetchone()
        notes = conn.execute("SELECT workspace_id, user_id FROM notifications WHERE kind = 'workflow' OR title LIKE '%Alpha ran%'").fetchall()
        conn.close()
        s_b, _, _ = bob.get(f"/api/w/{B}/workflow-runs/{r['run_id']}")
        run.check("9 background tenant context", "Alpha's workflow run belongs to Alpha and notifies Alpha's owner only",
                  rr["status"] == "done" and row[0] == A and all(n[0] == A for n in notes) and s_b == 404, rr["status"], "scoped", "done in A, 404 from B",
                  audit=audit_event(alice, A, "workflow.run.done"))

        # ---- 10. audit attribution ----------------------------------------------------------------------------------
        s, audit_a, _ = alice.get(f"/api/w/{A}/audit")
        s, audit_b, _ = bob.get(f"/api/w/{B}/audit")
        users_a = {r["user_id"] for r in audit_a}
        users_b = {r["user_id"] for r in audit_b}
        cross_ok = alice_id in users_b   # her denied attempts on Beta are recorded there, as denials
        denials = [r for r in audit_b if r["user_id"] == alice_id]
        attribution = all(r["workspace_id"] == A for r in audit_a) and all(r["workspace_id"] == B for r in audit_b) \
            and all(r["action"] == "access.denied" and r["decision"] == "denied" for r in denials) and cross_ok \
            and all(r.get("event_id") and r.get("request_id") for r in audit_a + audit_b) \
            and vic_id in users_a and bob_id in users_b and vera_id not in users_a
        s_v, _, _ = vic.get(f"/api/w/{A}/audit")
        run.check("10 audit attribution", "every entry carries its tenant, user, event and correlation id; a viewer cannot read the log",
                  attribution and s_v == 403, f"{len(audit_a)}+{len(audit_b)} entries", "correct", "scoped, attributed, admin-only",
                  note=f"Alice's denied attempts on Beta: {len(denials)}")

        # ---- 11. no cross-corruption ------------------------------------------------------------------------------
        run.check("11 no cross-corruption", "Beta's authoritative tables and ledger rows unchanged by everything Alpha did and by Beta's own reads",
                  fingerprint(db_path, B) == fp_b0, "fingerprint", "unchanged", "identical digest", before=fp_b0, after=fingerprint(db_path, B),
                  note="digest covers the authoritative tables and the ledger rows; Beta's agent runs touched threads only")
        s, d, _ = bob.get(f"/api/w/{B}/datasets/{results['ds_Beta']}")
        run.check("11 no cross-corruption", "Beta's ledger rows read back intact", s == 200 and len(d["rows"]) == 2 and any("987.65" in json.dumps(r) for r in d["rows"]), s, "allowed", "2 rows")

        # ---- 12. logout and expiry do not touch other sessions -------------------------------------------------------
        s, _, _ = alice.post("/api/auth/logout")
        s_a, _, _ = alice.get(f"/api/w/{A}/datasets")
        s_b, _, _ = bob.get(f"/api/w/{B}/datasets")
        s_v, _, _ = vic.get(f"/api/w/{A}/datasets")
        run.check("12 logout isolation", "Alice logs out: Alice 401, Bob and Vic still signed in", s_a == 401 and s_b == 200 and s_v == 200,
                  f"{s_a}/{s_b}/{s_v}", "per session", "401/200/200", audit=audit_event(bob, B, "session.logout") or "session.logout rows have no workspace")
        # Vic's session is made idle in the database (the clock cannot be moved in a live process); Vera is untouched
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("UPDATE sessions SET last_seen = last_seen - 31 * 60 WHERE user_id = ?", (vic_id,))
        conn.commit(); conn.close()
        s_v, dv, _ = vic.get(f"/api/w/{A}/datasets")
        s_ve, _, _ = vera.get(f"/api/w/{B}/datasets")
        run.check("12 logout isolation", "Vic idle for 31 minutes: signed out; Vera continues", s_v == 401 and (dv or {}).get("error") == "session_idle" and s_ve == 200,
                  f"{s_v}/{s_ve}", "per session", "401 session_idle / 200")

    finally:
        engine.terminate()
        try:
            engine.wait(10)
        except subprocess.TimeoutExpired:
            engine.kill()
        model.shutdown()

    passed = sum(1 for e in run.evidence if e["result"] == "PASS")
    summary = {"commit": commit, "engine": "python -m desk_engine (server mode, separate process)", "base_url": base,
               "model": "simulated (loopback OpenAI-compatible server); live model coverage: none",
               "scenarios": len(run.evidence), "passed": passed, "failed": len(run.failures), "evidence": run.evidence}
    out_path.write_text(json.dumps(summary, indent=1))
    print(f"multi-tenant integration: {passed}/{len(run.evidence)} checks passed on {commit[:7]} -> {out_path}")
    for f in run.failures:
        print("FAIL", f)
    return 1 if run.failures else 0


if __name__ == "__main__":
    sys.exit(main())
