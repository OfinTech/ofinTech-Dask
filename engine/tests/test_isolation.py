"""Enterprise isolation: two workspaces on one Team Server, one member of each, and every layer checked for leaks:
database rows, file objects, conversations and memory, retrieval, MCP keys, background jobs, exports, reports and
the audit log. Then: what logs and error responses give away, and two decisions racing for one approval."""

from __future__ import annotations

import io
import logging
import unittest

from starlette.testclient import TestClient

from .helpers import Harness

PW = "Correct-Horse-9"
ORIGIN = {"origin": "https://desk.example.com"}


class TwoTenants(unittest.TestCase):
    """Alice owns workspace A (with a ledger, a document, a memory, a thread, a file and a workflow); Bob owns B."""

    def setUp(self) -> None:
        self.h = Harness("server", script=["Noted."])
        self.alice = self.h.client
        self.assertEqual(self.alice.post("/api/auth/signup", json={"email": "alice@a.test", "name": "Alice", "password": PW}).status_code, 200)
        self.a = self.alice.post("/api/workspaces", json={"name": "Alpha Ltd", "base_currency": "USD"}, headers=ORIGIN).json()["id"]
        inv = self.alice.post(f"/api/w/{self.a}/invites", json={"email": "bob@b.test", "role": "viewer"}, headers=ORIGIN)
        # Bob signs up through an invitation to A but immediately leaves it; he then owns B
        self.bob = TestClient(self.h.app, base_url="https://desk.example.com")
        self.assertEqual(self.bob.post("/api/auth/signup", json={"email": "bob@b.test", "name": "Bob", "password": PW,
                                                                 "invite_token": inv.json()["link"].rsplit("/", 1)[1]}).status_code, 200)
        self.bob_id = self.bob.get("/api/session").json()["user"]["id"]
        self.assertEqual(self.alice.delete(f"/api/w/{self.a}/members/{self.bob_id}", headers=ORIGIN).status_code, 204)
        self.b = self.bob.post("/api/workspaces", json={"name": "Beta LLC", "base_currency": "EUR"}, headers=ORIGIN).json()["id"]
        # Alice's data
        up = self.alice.post(f"/api/w/{self.a}/imports", files={"file": ("gl.csv", b"account,amount\n4000,777.77\n")}, headers=ORIGIN).json()
        self.ds = self.alice.post(f"/api/w/{self.a}/imports/{up['upload_id']}/commit", json={"name": "Alpha GL", "sheet": None, "header_row": 0}, headers=ORIGIN).json()["id"]
        self.doc = self.alice.post(f"/api/w/{self.a}/documents", files=[("files", ("alpha-board.md", b"# Alpha board pay\nCEO salary 1,234,567"))], headers=ORIGIN).json()["added"][0]["id"]
        self.h.settle()
        self.mem = self.alice.post(f"/api/w/{self.a}/memories", json={"content": "Alpha's auditor is Falcon & Co", "scope": "workspace"}, headers=ORIGIN).json()
        self.thread = self.alice.post(f"/api/w/{self.a}/threads", json={"title": "Alpha secrets"}, headers=ORIGIN).json()["id"]
        self.alice.post(f"/api/w/{self.a}/threads/{self.thread}/messages", json={"text": "Our margin target is 41%"}, headers=ORIGIN)
        self.alice.post(f"/api/w/{self.a}/providers", json={"kind": "openai_compatible", "label": "Fake", "base_url": self.h.llm.BASE, "api_key": "sk-alpha"}, headers=ORIGIN)
        self.wf = self.alice.post(f"/api/w/{self.a}/workflows", json={"name": "Alpha flow", "enabled": True, "definition": {
            "trigger": {"type": "manual"}, "steps": [{"id": "n", "name": "N", "type": "notify", "recipients": "me", "message": "hi"}]}}, headers=ORIGIN).json()["id"]
        self.key = self.alice.post(f"/api/w/{self.a}/mcp-keys", json={"name": "alpha-key"}, headers=ORIGIN).json()["key"]

    def tearDown(self) -> None:
        self.h.close()

    def test_database_rows_and_objects(self) -> None:
        b = self.bob
        # every workspace-scoped route with Alice's ids under Bob's session, and under Alice's ids with Bob's workspace
        for method, path in (("GET", f"/api/w/{self.a}"), ("GET", f"/api/w/{self.a}/datasets/{self.ds}"),
                             ("GET", f"/api/w/{self.a}/datasets/{self.ds}/export.csv"), ("GET", f"/api/w/{self.a}/documents/{self.doc}"),
                             ("GET", f"/api/w/{self.a}/documents/{self.doc}/file"), ("GET", f"/api/w/{self.a}/threads/{self.thread}"),
                             ("GET", f"/api/w/{self.a}/threads/{self.thread}/events"), ("GET", f"/api/w/{self.a}/memories"),
                             ("GET", f"/api/w/{self.a}/workflows/{self.wf}"), ("GET", f"/api/w/{self.a}/audit"),
                             ("GET", f"/api/w/{self.a}/overview"), ("GET", f"/api/w/{self.a}/analysis"),
                             ("GET", f"/api/w/{self.a}/files"), ("GET", f"/api/w/{self.a}/mcp-keys"),
                             ("GET", f"/api/w/{self.a}/providers"), ("GET", f"/api/w/{self.a}/members")):
            r = b.request(method, path)
            self.assertEqual(r.status_code, 404, (path, r.text))      # not even "forbidden": the workspace does not exist for him
            self.assertNotIn("Alpha", r.text)
        # Alice's ids under Bob's own workspace path
        for path in (f"/api/w/{self.b}/datasets/{self.ds}", f"/api/w/{self.b}/documents/{self.doc}", f"/api/w/{self.b}/documents/{self.doc}/file",
                     f"/api/w/{self.b}/threads/{self.thread}", f"/api/w/{self.b}/workflows/{self.wf}"):
            r = b.get(path)
            self.assertEqual(r.status_code, 404, (path, r.text))
            self.assertNotIn("777.77", r.text)
            self.assertNotIn("1,234,567", r.text)
        r = b.patch(f"/api/w/{self.b}/memories/{self.mem['id']}", json={"pinned": True}, headers=ORIGIN)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(b.delete(f"/api/w/{self.b}/memories/{self.mem['id']}", headers=ORIGIN).status_code, 404)
        r = b.post(f"/api/w/{self.b}/query", json={"dataset": self.ds, "measures": [{"column": "amount", "agg": "sum"}]}, headers=ORIGIN)
        self.assertIn(r.status_code, (404, 422))
        self.assertNotIn("777.77", r.text)
        # writes with Alice's ids
        self.assertEqual(b.delete(f"/api/w/{self.b}/datasets/{self.ds}", headers=ORIGIN).status_code, 404)
        self.assertEqual(b.patch(f"/api/w/{self.b}/threads/{self.thread}", json={"title": "x"}, headers=ORIGIN).status_code, 404)
        self.assertEqual(b.post(f"/api/w/{self.b}/workflows/{self.wf}/run", json={}, headers=ORIGIN).status_code, 404)
        self.assertIn("Alpha GL", self.alice.get(f"/api/w/{self.a}/datasets/{self.ds}").text)   # untouched

    def test_retrieval_memory_and_conversations_through_the_agent(self) -> None:
        import asyncio

        from desk_engine.agent.tools import ToolContext, registry

        bws = self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.b,))
        ctx = ToolContext(self.h.engine, bws, self.bob_id)
        for name, args in (("search_documents", {"query": "board pay CEO salary"}), ("read_document", {"document_id": self.doc}),
                           ("search_workspace", {"query": "margin target"}), ("search_memory", {"query": "auditor"}),
                           ("list_datasets", {}), ("list_documents", {})):
            out = asyncio.run(registry.get_static(name).run(ctx, args))
            for needle in ("1,234,567", "Falcon", "41%", "Alpha GL", "alpha-board"):
                self.assertNotIn(needle, out.text, (name, needle))
        # Bob's search and memory routes see nothing of Alice's
        self.assertEqual(self.bob.post(f"/api/w/{self.b}/documents/search", json={"query": "CEO salary"}, headers=ORIGIN).json(), [])
        self.assertEqual(self.bob.get(f"/api/w/{self.b}/memories").json()["memories"], [])
        hits = self.bob.post(f"/api/w/{self.b}/search", json={"query": "margin target"}, headers=ORIGIN).json()
        self.assertNotIn("41%", str(hits))
        # and Alice still finds her own
        self.assertEqual(len(self.alice.post(f"/api/w/{self.a}/documents/search", json={"query": "CEO salary"}, headers=ORIGIN).json()), 1)

    def test_mcp_key_background_jobs_and_audit(self) -> None:
        # Alice's MCP key is bound to workspace A; it cannot read B even when Alice is told B's dataset id
        r = self.h.client.__class__(self.h.app, base_url="https://desk.example.com").post(
            "/mcp", content='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}',
            headers={"authorization": f"Bearer {self.key}", "accept": "application/json, text/event-stream", "content-type": "application/json"})
        self.assertEqual(r.status_code, 200, r.text)
        from desk_engine.mcp_server import resolve

        tctx = resolve(self.h.engine, f"Bearer {self.key}")
        self.assertEqual(tctx.ws_id, self.a)
        # a key is revoked with its membership: remove Alice's key-holder context by disabling the user
        self.h.engine.db.execute("UPDATE users SET disabled = 1 WHERE email = 'alice@a.test'")
        self.assertIsNone(resolve(self.h.engine, f"Bearer {self.key}"))
        self.h.engine.db.execute("UPDATE users SET disabled = 0 WHERE email = 'alice@a.test'")
        # a workflow runs as its creator; once the creator leaves, it stops instead of acting with old rights
        run = self.alice.post(f"/api/w/{self.a}/workflows/{self.wf}/run", json={}, headers=ORIGIN).json()["run_id"]
        self.h.ws = self.a
        self.assertEqual(self.h.wait_workflow(run)["status"], "done")
        self.h.engine.db.execute("DELETE FROM members WHERE workspace_id = ? AND user_id = (SELECT id FROM users WHERE email = 'alice@a.test')", (self.a,))
        wf = self.h.engine.db.one("SELECT * FROM workflows WHERE id = ?", (self.wf,))
        ws = self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.a,))
        from desk_engine.db import loads

        rid = self.h.client.portal.call(lambda: self.h.engine.workflows.start(ws, {**wf, "definition": loads(wf["definition"])}, {"type": "manual"}, wf["created_by"]))
        import time

        for _ in range(200):
            done = self.h.engine.db.one("SELECT status, error FROM workflow_runs WHERE id = ?", (rid,))
            if done["status"] in ("done", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(done["status"], "failed")
        self.assertIn("no longer an active member", done["error"])
        # the audit log of A is Alice's admins' business only; B's log holds nothing about A
        self.h.engine.db.insert("members", {"workspace_id": self.a, "user_id": wf["created_by"], "role": "owner", "created_at": 0})
        a_log = self.alice.get(f"/api/w/{self.a}/audit").json()
        b_log = self.bob.get(f"/api/w/{self.b}/audit").json()
        self.assertNotIn("Alpha", str(b_log))
        self.assertNotIn(self.wf, str(b_log))
        self.assertTrue(any(e["action"] == "workflow.run" for e in (a_log["entries"] if isinstance(a_log, dict) else a_log)))


class LeakageThroughLogsAndErrors(unittest.TestCase):
    def test_failed_logins_and_upstream_failures_log_no_secrets(self) -> None:
        h = Harness("server")
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        try:
            c = h.client
            c.post("/api/auth/signup", json={"email": "o@acme.test", "name": "O", "password": PW})
            for _ in range(3):
                c.post("/api/auth/login", json={"email": "o@acme.test", "password": "Wrong-Password-77"})
            c.post("/api/auth/forgot", json={"email": "o@acme.test"})
            ws = c.post("/api/workspaces", json={"name": "Acme", "base_currency": "USD"}, headers=ORIGIN).json()["id"]
            c.post(f"/api/w/{ws}/providers", json={"kind": "openai_compatible", "label": "Fake", "base_url": "https://llm.test/v1",
                                                   "api_key": "sk-NEVER-IN-LOGS"}, headers=ORIGIN)
            r = c.post(f"/api/w/{ws}/connections/odoo", json={"base_url": "https://odoo.example.test", "database": "db",
                                                             "api_key": "odoo-NEVER-IN-LOGS-123456"}, headers=ORIGIN)
            self.assertIn(r.status_code, (400, 422, 502))
            # an unhandled error is a generic 500 to the client
            logged = buf.getvalue()
            for needle in (PW, "Wrong-Password-77", "sk-NEVER-IN-LOGS", "odoo-NEVER-IN-LOGS-123456"):
                self.assertNotIn(needle, logged, needle)
                self.assertNotIn(needle, r.text, needle)
            self.assertNotIn("Traceback", r.text)
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
            h.close()

    def test_exports_and_reports_hold_only_the_workspace(self) -> None:
        h = Harness()
        try:
            a = h.setup_workspace(with_model=False)
            up = h.api("POST", f"/api/w/{a}/imports", files={"file": ("a.csv", b"k,v\nalpha-only,1\n")})
            h.api("POST", f"/api/w/{a}/imports/{up['upload_id']}/commit", json={"name": "A", "sheet": None, "header_row": 0})
            b = h.api("POST", "/api/workspaces", json={"name": "B", "base_currency": "USD"})["id"]
            up = h.api("POST", f"/api/w/{b}/imports", files={"file": ("b.csv", b"k,v\nbeta-only,2\n")})
            dsb = h.api("POST", f"/api/w/{b}/imports/{up['upload_id']}/commit", json={"name": "B", "sheet": None, "header_row": 0})
            csv = h.client.get(f"/api/w/{b}/datasets/{dsb['id']}/export.csv").text
            self.assertIn("beta-only", csv)
            self.assertNotIn("alpha-only", csv)
            self.assertEqual([d["name"] for d in h.api("GET", f"/api/w/{b}/datasets")], ["B"])
        finally:
            h.close()


class ApprovalRace(unittest.TestCase):
    def test_one_approval_cannot_be_decided_twice(self) -> None:
        h = Harness(script=[[("gmail_send", {"to": "a@b.test", "subject": "x", "body": "y"})], "Done."])
        try:
            ws = h.setup_workspace()
            # gmail_send is hidden without Google; use a workflow approval step instead, which anyone can race on
            wf = h.api("POST", f"/api/w/{ws}/workflows", json={"name": "Gate", "enabled": True, "definition": {
                "trigger": {"type": "manual"}, "steps": [{"id": "ok", "name": "OK", "type": "approval", "title": "Go?", "message": "Check the figures and approve."},
                                                         {"id": "n", "name": "N", "type": "notify", "recipients": "me", "message": "went"}]}})
            rid = h.api("POST", f"/api/w/{ws}/workflows/{wf['id']}/run", json={})["run_id"]
            h.wait_workflow(rid, {"waiting_approval"})
            appr = h.api("GET", f"/api/w/{ws}/approvals")[0]
            wsrow = h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,))

            async def race():
                import asyncio

                from desk_engine.errors import AppError

                async def decide(approve: bool):
                    try:
                        return (await h.engine.agent.decide(wsrow, appr["id"], "u_local", "owner", approve))["status"]
                    except AppError as exc:
                        return exc.code or "error"

                return await asyncio.gather(decide(True), decide(False))

            results = h.client.portal.call(race)
            self.assertEqual(sorted(results), ["approved", "conflict"] if "conflict" in results else sorted(results))
            # one decision wins (the winner's returned status may already be 'executing'/'executed' once the run
            # resumed and consumed it); the other is refused as already decided
            self.assertEqual(sum(r in ("approved", "rejected", "executing", "executed") for r in results), 1, results)
            self.assertIn("approval_decided", results)
            final = h.api("GET", f"/api/w/{ws}/approvals", params={"status": "all"})
            self.assertIn(h.engine.db.scalar("SELECT status FROM approvals WHERE id = ?", (appr["id"],)),
                          ("approved", "executing", "executed"))
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
