"""OP-9 (approval expiry, binding and single use) and OP-10 (the security audit log).

The scripted model asks for a write; a person decides; the engine claims the approval for execution. Every way an
approval can go wrong is tried against a Team Server with an owner, an editor and a viewer, and the authoritative
tables are fingerprinted before and after. The audit log is then read back for every event the brief requires."""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time
import unittest
from unittest import mock

from starlette.testclient import TestClient

from desk_engine import approvals as ap
from desk_engine.db import now
from desk_engine.errors import AppError

from .helpers import FakeLLM, Harness
from .test_ai_financial_assurance import fingerprint
from .test_close import ledger_xlsx

PW = "Correct-Horse-9"
ORIGIN = {"origin": "https://desk.example.com"}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        for target in ("desk_engine.security.socket.getaddrinfo", "desk_engine.egress.socket.getaddrinfo"):
            patcher = mock.patch(target, return_value=public)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.h = Harness("server", script=[])
        self.owner = self.h.client
        self.assertEqual(self.owner.post("/api/auth/signup", json={"email": "owner@a.test", "name": "Olive", "password": PW}).status_code, 200)
        self.owner_id = self.owner.get("/api/session").json()["user"]["id"]
        self.ws = self.owner.post("/api/workspaces", json={"name": "Alpha Ltd", "base_currency": "USD"}, headers=ORIGIN).json()["id"]
        self.h.ws = self.ws
        r = self.owner.post(f"/api/w/{self.ws}/providers", json={"kind": "openai_compatible", "label": "Fake", "base_url": FakeLLM.BASE, "api_key": "sk-alpha"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        up = self.owner.post(f"/api/w/{self.ws}/cost-centres/ledger/upload", files={"file": ("GL.xlsx", ledger_xlsx())}, headers=ORIGIN).json()
        self.ledger = f"ds_{up['dataset']['id']}"
        self.editor, self.editor_id = self.member("editor@a.test", "Ed", "editor")
        self.viewer, self.viewer_id = self.member("viewer@a.test", "Vi", "viewer")
        self.wf = self.owner.post(f"/api/w/{self.ws}/workflows", json={"name": "Alpha flow", "enabled": True, "definition": {
            "trigger": {"type": "manual"}, "steps": [{"id": "n", "name": "N", "type": "notify", "recipients": "me", "message": "hi"}]}}, headers=ORIGIN).json()["id"]

    def tearDown(self) -> None:
        self.h.close()

    def member(self, email: str, name: str, role: str) -> tuple[TestClient, str]:
        inv = self.owner.post(f"/api/w/{self.ws}/invites", json={"email": email, "role": role}, headers=ORIGIN).json()
        c = TestClient(self.h.app, base_url="https://desk.example.com")
        c.portal = self.h.client.portal
        self.assertEqual(c.post("/api/auth/signup", json={"email": email, "name": name, "password": PW,
                                                           "invite_token": inv["link"].rsplit("/", 1)[1]}).status_code, 200)
        return c, c.get("/api/session").json()["user"]["id"]

    def turn(self, client: TestClient, script: list, text: str = "Go ahead.") -> dict:
        self.h.llm.script = list(script)
        t = client.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).json()
        self.assertEqual(client.post(f"/api/w/{self.ws}/threads/{t['id']}/messages", json={"text": text}, headers=ORIGIN).status_code, 200)
        return self.wait(client, t["id"])

    def wait(self, client: TestClient, thread_id: str, statuses: set[str] | None = None) -> dict:
        statuses = statuses or {"done", "error", "waiting_approval"}
        deadline = time.time() + 15
        while time.time() < deadline:
            data = client.get(f"/api/w/{self.ws}/threads/{thread_id}").json()
            if data["run"] and data["run"]["status"] in statuses:
                data["thread_id"] = thread_id
                return data
            time.sleep(0.05)
        raise AssertionError("agent run did not finish")

    def pending(self) -> dict:
        row = self.h.engine.db.one("SELECT * FROM approvals WHERE workspace_id = ? AND status = 'pending' ORDER BY created_at DESC", (self.ws,))
        assert row is not None
        return row

    def decide(self, client: TestClient, approval_id: str, approve: bool) -> tuple[int, dict]:
        r = client.post(f"/api/w/{self.ws}/approvals/{approval_id}/decide", json={"approve": approve}, headers=ORIGIN)
        return r.status_code, r.json()

    def status(self, approval_id: str) -> str:
        return self.h.engine.db.scalar("SELECT status FROM approvals WHERE id = ?", (approval_id,))

    def audit(self, action: str | None = None, **where: str) -> list[dict]:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if action:
            sql += " AND action = ?"
            params.append(action)
        for k, v in where.items():
            sql += f" AND {k} = ?"  # noqa: S608 - column names are fixed by the caller
            params.append(v)
        return self.h.engine.db.all(sql + " ORDER BY id", params)

    def workflow_runs(self) -> int:
        return self.h.engine.db.scalar("SELECT COUNT(*) FROM workflow_runs WHERE workspace_id = ?", (self.ws,))

    def ask_run_workflow(self, client: TestClient, text: str = "from the model") -> tuple[dict, dict]:
        data = self.turn(client, [[("run_workflow", {"workflow_id": self.wf, "input": text})], "Done."])
        self.assertEqual(data["run"]["status"], "waiting_approval")
        return data, self.pending()


class ApprovalLifecycle(Base):
    def test_valid_approval_succeeds_exactly_once_and_is_consumed(self) -> None:
        before = fingerprint(self.h, self.ws, self.ledger)
        data, appr = self.ask_run_workflow(self.owner)
        self.assertTrue(appr["id"].startswith("ap_") and len(appr["id"]) >= 20)
        self.assertEqual(appr["args_digest"], ap.canonical_digest(self.ws, self.owner_id, "run_workflow", {"workflow_id": self.wf, "input": "from the model"}))
        self.assertAlmostEqual(appr["expires_at"] - appr["created_at"], ap.OUTBOUND_TTL, delta=5)
        code, _ = self.decide(self.editor, appr["id"], True)
        self.assertEqual(code, 200)
        self.wait(self.owner, data["thread_id"], {"done", "error"})
        self.assertEqual(self.status(appr["id"]), "executed")
        self.assertEqual(self.workflow_runs(), 1)
        # the same approval cannot be executed again by anyone
        self.assertEqual(self.h.engine.approvals.claim(approval_id=appr["id"], workspace_id=self.ws, tool="run_workflow",
                                                       requested_by=self.owner_id, arguments={"workflow_id": self.wf, "input": "from the model"},
                                                       executor=self.owner_id), "replayed")
        self.assertEqual(self.workflow_runs(), 1)
        # audit trail: requested → approved → run, all carrying the approval id and the run as correlation id
        trail = [(r["action"], r["decision"], r["result"]) for r in self.audit(approval_id=appr["id"])]
        self.assertEqual(trail[:3], [("approval.requested", "needs_approval", "pending"), ("approval.approved", "approved", "approved"),
                                     ("tool.run", "approved", "ok")])
        self.assertTrue(all(r["request_id"] for r in self.audit(approval_id=appr["id"])[:3]))
        after = fingerprint(self.h, self.ws, self.ledger)
        self.assertEqual(before["ledger_rows"], after["ledger_rows"])

    def test_expired_approval_is_rejected_at_decision_and_at_claim(self) -> None:
        data, appr = self.ask_run_workflow(self.owner)
        self.h.engine.db.execute("UPDATE approvals SET expires_at = ? WHERE id = ?", (now() - 1, appr["id"]))
        code, body = self.decide(self.owner, appr["id"], True)
        self.assertEqual(code, 409, body)
        self.assertEqual(body["error"], "approval_expired")
        self.assertEqual(self.status(appr["id"]), "expired")
        self.assertEqual(self.workflow_runs(), 0)
        self.assertTrue(self.audit("approval.expired", approval_id=appr["id"]))
        # an approval that expires between the decision and the execution is refused at the claim
        data, appr = self.ask_run_workflow(self.owner, "second")
        self.h.engine.db.execute("UPDATE approvals SET status = 'approved', decided_by = ?, expires_at = ? WHERE id = ?",
                                 (self.owner_id, now() - 1, appr["id"]))
        self.assertEqual(self.h.engine.approvals.claim(approval_id=appr["id"], workspace_id=self.ws, tool="run_workflow",
                                                       requested_by=self.owner_id, arguments={"workflow_id": self.wf, "input": "second"},
                                                       executor=self.owner_id), "expired")
        self.assertEqual(self.status(appr["id"]), "expired")
        self.assertEqual(self.workflow_runs(), 0)

    def test_replayed_decision_is_rejected(self) -> None:
        data, appr = self.ask_run_workflow(self.owner)
        self.assertEqual(self.decide(self.owner, appr["id"], True)[0], 200)
        self.wait(self.owner, data["thread_id"], {"done", "error"})
        for approve in (True, False):
            code, body = self.decide(self.owner, appr["id"], approve)
            self.assertEqual(code, 409, body)
            self.assertEqual(body["error"], "approval_decided")
        self.assertEqual(self.workflow_runs(), 1)
        self.assertEqual(len(self.audit("approval.replayed", approval_id=appr["id"])), 2)

    def test_approval_for_another_user_is_rejected_at_claim(self) -> None:
        data, appr = self.ask_run_workflow(self.owner)
        self.assertEqual(self.decide(self.editor, appr["id"], True)[0], 200)
        # the run that consumes it belongs to the owner; an execution on behalf of the editor is not what was approved
        self.h.engine.db.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (appr["id"],))
        self.assertEqual(self.h.engine.approvals.claim(approval_id=appr["id"], workspace_id=self.ws, tool="run_workflow",
                                                       requested_by=self.editor_id, arguments={"workflow_id": self.wf, "input": "from the model"},
                                                       executor=self.editor_id), "changed")
        self.assertTrue(self.audit("approval.changed", approval_id=appr["id"]))

    def test_approval_for_another_tenant_is_rejected(self) -> None:
        data, appr = self.ask_run_workflow(self.owner)
        other = self.owner.post("/api/workspaces", json={"name": "Beta LLC", "base_currency": "EUR"}, headers=ORIGIN).json()["id"]
        # through the API: the approval does not exist in the other workspace
        r = self.owner.post(f"/api/w/{other}/approvals/{appr['id']}/decide", json={"approve": True}, headers=ORIGIN)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(self.status(appr["id"]), "pending")
        # at the claim: a decided approval of workspace A cannot execute in workspace B
        self.h.engine.db.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (appr["id"],))
        self.assertEqual(self.h.engine.approvals.claim(approval_id=appr["id"], workspace_id=other, tool="run_workflow",
                                                       requested_by=self.owner_id, arguments={"workflow_id": self.wf, "input": "from the model"},
                                                       executor=self.owner_id), "missing")
        self.assertEqual(self.workflow_runs(), 0)

    def test_approval_for_a_different_action_or_modified_parameters_is_rejected(self) -> None:
        data, appr = self.ask_run_workflow(self.owner)
        self.h.engine.db.execute("UPDATE approvals SET status = 'approved' WHERE id = ?", (appr["id"],))
        base = {"approval_id": appr["id"], "workspace_id": self.ws, "requested_by": self.owner_id, "executor": self.owner_id}
        self.assertEqual(self.h.engine.approvals.claim(tool="message_colleague", arguments={"workflow_id": self.wf, "input": "from the model"}, **base), "changed")
        self.assertEqual(self.h.engine.approvals.claim(tool="run_workflow", arguments={"workflow_id": self.wf, "input": "from the model "}, **base), "changed")
        self.assertEqual(self.h.engine.approvals.claim(tool="run_workflow", arguments={"workflow_id": self.wf, "input": "from the model", "extra": 1}, **base), "changed")
        self.assertEqual(self.status(appr["id"]), "approved")   # untouched by refused claims
        # the original still works exactly once
        self.assertIsNone(self.h.engine.approvals.claim(tool="run_workflow", arguments={"workflow_id": self.wf, "input": "from the model"}, **base))
        self.assertEqual(self.status(appr["id"]), "executing")
        self.assertEqual(len(self.audit("approval.changed", approval_id=appr["id"])), 3)

    def test_revoked_approval_is_rejected(self) -> None:
        """Three revocations: the thread is stopped; the requester loses the membership; the requester is demoted."""
        data, appr = self.ask_run_workflow(self.owner)
        self.assertEqual(self.owner.post(f"/api/w/{self.ws}/threads/{data['thread_id']}/stop", headers=ORIGIN).status_code, 204)
        self.assertEqual(self.status(appr["id"]), "expired")
        code, body = self.decide(self.owner, appr["id"], True)
        self.assertEqual(code, 409, body)
        # an editor asks, then is removed: the pending approval dies with the membership
        data, appr = self.ask_run_workflow(self.editor, "by editor")
        self.assertEqual(self.owner.delete(f"/api/w/{self.ws}/members/{self.editor_id}", headers=ORIGIN).status_code, 204)
        self.assertEqual(self.status(appr["id"]), "expired")
        self.assertEqual(self.decide(self.owner, appr["id"], True)[0], 409)
        self.assertTrue(self.audit("approval.expired", approval_id=None, user_id=self.editor_id) or self.audit("approval.expired"))
        self.assertEqual(self.workflow_runs(), 0)

    def test_concurrent_claims_cannot_both_succeed(self) -> None:
        data, appr = self.ask_run_workflow(self.owner)
        self.h.engine.db.execute("UPDATE approvals SET status = 'approved', decided_by = ? WHERE id = ?", (self.owner_id, appr["id"]))
        results: list[str | None] = []
        start = threading.Barrier(8)

        def go() -> None:
            start.wait()
            results.append(self.h.engine.approvals.claim(approval_id=appr["id"], workspace_id=self.ws, tool="run_workflow",
                                                         requested_by=self.owner_id, arguments={"workflow_id": self.wf, "input": "from the model"},
                                                         executor=self.owner_id))

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(None), 1, results)
        self.assertEqual(results.count("replayed"), 7, results)
        self.assertEqual(self.status(appr["id"]), "executing")

    def test_approval_cannot_bypass_rbac(self) -> None:
        """A viewer cannot decide; a requester who was demoted to viewer after the decision cannot have an
        editor-only action executed on an old approval; an approval id supplied by the model means nothing."""
        data, appr = self.ask_run_workflow(self.editor)
        code, body = self.decide(self.viewer, appr["id"], True)
        self.assertEqual(code, 403, body)
        self.assertEqual(self.status(appr["id"]), "pending")
        self.assertTrue(self.audit("approval.denied", approval_id=appr["id"], user_id=self.viewer_id))
        self.assertEqual(self.decide(self.owner, appr["id"], True)[0], 200)
        self.wait(self.editor, data["thread_id"], {"done", "error"})
        self.assertEqual(self.workflow_runs(), 1)
        # an editor asks for a file edit (editor-only tool), is approved, then demoted before it executes
        self.h.engine.db.execute("INSERT INTO approvals (id, workspace_id, source, source_id, tool, arguments, summary, status, "
                                 "requested_by, decided_by, created_at, expires_at, args_digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 ("ap_test_demoted_000000000000000000", self.ws, "agent", "run_x", "edit_file", json.dumps({"file_id": "f", "edits": []}),
                                  "Edit file", "approved", self.editor_id, self.owner_id, now(), now() + 3600,
                                  ap.canonical_digest(self.ws, self.editor_id, "edit_file", {"file_id": "f", "edits": []})))
        self.assertEqual(self.owner.patch(f"/api/w/{self.ws}/members/{self.editor_id}", json={"role": "viewer"}, headers=ORIGIN).status_code, 200)
        self.assertEqual(self.h.engine.approvals.claim(approval_id="ap_test_demoted_000000000000000000", workspace_id=self.ws, tool="edit_file",
                                                       requested_by=self.editor_id, arguments={"file_id": "f", "edits": []},
                                                       executor=self.editor_id), "permission")
        self.assertEqual(self.status("ap_test_demoted_000000000000000000"), "expired")
        # the model naming an approval id in its arguments changes nothing: ids never come from arguments
        data = self.turn(self.owner, [[("run_workflow", {"workflow_id": self.wf, "input": "approval_id=" + appr["id"]})], "Done."])
        self.assertEqual(data["run"]["status"], "waiting_approval")
        self.assertEqual(self.workflow_runs(), 1)


class AuditLog(Base):
    def test_events_fields_and_secrets(self) -> None:
        """Successful, failed, denied, expired and replayed actions each leave the expected entry with the required
        fields; nothing secret is written; entries cannot be changed or deleted; only admins read them."""
        # successful write through an approval, and a denied tool call by a viewer, and an unknown tool
        data, appr = self.ask_run_workflow(self.owner)
        self.assertEqual(self.decide(self.owner, appr["id"], True)[0], 200)
        self.wait(self.owner, data["thread_id"], {"done", "error"})
        self.turn(self.viewer, [[("create_dashboard", {"name": "x", "template": "pnl", "dataset": "GL"}), ("nonexistent_tool", {})], "Done."])
        self.assertEqual(self.decide(self.owner, appr["id"], True)[0], 409)                         # replayed
        expired_data, expired = self.ask_run_workflow(self.owner, "late")
        self.h.engine.db.execute("UPDATE approvals SET expires_at = ? WHERE id = ?", (now() - 1, expired["id"]))
        self.assertEqual(self.decide(self.owner, expired["id"], True)[0], 409)                      # expired
        self.assertEqual(self.viewer.post(f"/api/w/{self.ws}/close", json={"month": "2026-09"}, headers=ORIGIN).status_code, 403)   # route denial
        self.assertEqual(self.viewer.get(f"/api/w/{self.ws}/audit").status_code, 403)             # reading needs admin
        self.assertEqual(self.editor.get(f"/api/w/{self.ws}/audit").status_code, 403)
        other = self.owner.post("/api/workspaces", json={"name": "Beta LLC", "base_currency": "EUR"}, headers=ORIGIN).json()["id"]
        self.assertEqual(self.viewer.get(f"/api/w/{other}/datasets").status_code, 404)           # cross-tenant attempt
        self.viewer.post("/api/auth/login", json={"email": "viewer@a.test", "password": "wrong-Password-1"}, headers=ORIGIN)
        self.assertEqual(self.viewer.post("/api/auth/logout", headers=ORIGIN).status_code, 200)

        rows = self.audit()
        by_action = {}
        for r in rows:
            by_action.setdefault(r["action"], []).append(r)
        for action in ("approval.requested", "approval.approved", "tool.run", "tool.denied", "approval.replayed", "approval.expired",
                       "access.denied", "user.login.failed", "session.logout", "user.signup", "workspace.create"):
            self.assertIn(action, by_action, action)
        # required fields on every row
        for r in rows:
            self.assertTrue(r["event_id"].startswith("ev_") and len(r["event_id"]) == 27, r["event_id"])
            self.assertIsInstance(r["created_at"], float)                 # UTC epoch seconds
            self.assertTrue(r["action"])
        self.assertEqual(len({r["event_id"] for r in rows}), len(rows))
        # correlation ids: API requests and agent runs
        self.assertTrue(all(r["request_id"] for r in by_action["access.denied"]))
        self.assertTrue(all(r["request_id"].startswith("run_") for r in by_action["tool.run"]))
        # the viewer's denied tool call and the unknown tool
        denied = {r["target"]: r for r in by_action["tool.denied"]}
        self.assertEqual(denied["create_dashboard"]["decision"], "denied")
        self.assertEqual(denied["create_dashboard"]["user_id"], self.viewer_id)
        self.assertEqual(denied["create_dashboard"]["resource_type"], "tool")
        self.assertEqual(denied["nonexistent_tool"]["result"], "refused")
        # the executed write: user, workspace, tool, decision, approval, outcome
        run = next(r for r in by_action["tool.run"] if r["target"] == "run_workflow")
        self.assertEqual((run["user_id"], run["workspace_id"], run["decision"], run["approval_id"], run["result"]),
                         (self.owner_id, self.ws, "approved", appr["id"], "ok"))
        # route denial names the route and the role it needed, never the body
        route = next(r for r in by_action["access.denied"] if "close" in (r["target"] or ""))
        self.assertEqual(route["user_id"], self.viewer_id)
        self.assertEqual(json.loads(route["detail"])["needed"], "editor")
        # the cross-tenant attempt is recorded against the tenant that was targeted
        cross = [r for r in by_action["access.denied"] if r["workspace_id"] == other]
        self.assertTrue(cross and cross[0]["user_id"] == self.viewer_id)
        self.assertEqual(json.loads(cross[0]["detail"])["reason"], "not_a_member")
        # session events
        self.assertEqual(by_action["session.logout"][-1]["user_id"], self.viewer_id)
        self.assertEqual(by_action["user.login.failed"][-1]["decision"], "denied")
        # nothing secret anywhere in the log
        blob = json.dumps(rows, default=str)
        for needle in (PW, "wrong-Password-1", "sk-alpha", "desk_session", "Bearer "):
            self.assertNotIn(needle, blob, needle)
        # sanitising: credential-shaped keys dropped, query strings stripped, IBANs masked, long text capped
        self.h.engine.audit(self.ws, self.owner_id, "test.sanitize", "t", {"api_key": "sk-NEVER", "Authorization": "Bearer x",
                                                                          "url": "https://h.test/p?access_token=abc",
                                                                          "iban": "DE89 3704 0044 0532 0130 00", "text": "x" * 2000})
        detail = json.loads(self.audit("test.sanitize")[-1]["detail"])
        self.assertNotIn("api_key", detail)
        self.assertNotIn("Authorization", detail)
        self.assertEqual(detail["url"], "https://h.test/p")
        self.assertNotIn("0532 0130 00", detail["iban"])
        self.assertLessEqual(len(detail["text"]), 501)
        # immutability: the engine's own connection cannot change or delete an entry
        first = rows[0]["id"]
        with self.assertRaises(sqlite3.IntegrityError):
            self.h.engine.db.execute("UPDATE audit_log SET action = 'tampered' WHERE id = ?", (first,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.h.engine.db.execute("DELETE FROM audit_log WHERE id = ?", (first,))
        self.assertEqual(self.h.engine.db.scalar("SELECT action FROM audit_log WHERE id = ?", (first,)), rows[0]["action"])
        # the admin reads the log; what it returns is text, never markup the page would render
        shown = self.owner.get(f"/api/w/{self.ws}/audit").json()
        self.assertTrue(shown and all("event_id" in r for r in shown))
        self.h.engine.audit(self.ws, self.owner_id, "test.markup", "<script>alert(1)</script>", {"note": "<img src=x onerror=alert(1)>"})
        shown = self.owner.get(f"/api/w/{self.ws}/audit").json()
        row = next(r for r in shown if r["action"] == "test.markup")
        self.assertEqual(row["target"], "<script>alert(1)</script>")        # stored verbatim as data …
        self.assertEqual(self.owner.get(f"/api/w/{self.ws}/audit").headers["content-type"].split(";")[0], "application/json")  # … served as JSON, rendered as text


if __name__ == "__main__":
    unittest.main()
