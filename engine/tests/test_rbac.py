"""RBAC across every execution path: a sweep of every role-gated route under every lower role, then the specific
cases the gate names — viewer vs editor actions, editor vs admin/owner actions, revocation during an active
session, resource-id manipulation, background jobs, streams — each denied financial modification checked against
a fingerprint of the authoritative tables taken before the attempt."""

from __future__ import annotations

import re
import socket
import time
import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from desk_engine.web import ROLES, role_at_least

from .helpers import FakeLLM, Harness
from .test_ai_financial_assurance import fingerprint
from .test_close import ledger_xlsx

PW = "Correct-Horse-9"
ORIGIN = {"origin": "https://desk.example.com"}
ROUTE = re.compile(r'@router\.(get|post|put|patch|delete)\("([^"]+)"(?:[^)]*?role="([a-z]+)")?')


def routes() -> list[tuple[str, str, str]]:
    """(method, path, role) for every workspace route declared in the engine."""
    out = []
    for f in sorted((Path(__file__).resolve().parents[1] / "desk_engine" / "routes").glob("*.py")):
        for m in ROUTE.finditer(f.read_text()):
            method, path, role = m.groups()
            if role and "{ws}" in path:
                out.append((method.upper(), path, role))
    return out


class Members(unittest.TestCase):
    """Alpha: owner, admin, editor, viewer. Beta: another owner. Every client runs on the engine's own loop."""

    def setUp(self) -> None:
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        for target in ("desk_engine.security.socket.getaddrinfo", "desk_engine.egress.socket.getaddrinfo"):
            patcher = mock.patch(target, return_value=public)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.h = Harness("server", script=[])
        self.owner = self.h.client
        self.assertEqual(self.owner.post("/api/auth/signup", json={"email": "owner@a.test", "name": "Olive", "password": PW}).status_code, 200)
        self.ws = self.owner.post("/api/workspaces", json={"name": "Alpha Ltd", "base_currency": "USD"}, headers=ORIGIN).json()["id"]
        self.h.ws = self.ws
        self.assertEqual(self.owner.post(f"/api/w/{self.ws}/providers", json={"kind": "openai_compatible", "label": "Fake", "base_url": FakeLLM.BASE, "api_key": "sk-alpha"}, headers=ORIGIN).status_code, 200)
        up = self.owner.post(f"/api/w/{self.ws}/cost-centres/ledger/upload", files={"file": ("GL.xlsx", ledger_xlsx())}, headers=ORIGIN).json()
        self.ds = up["dataset"]["id"]
        self.ledger = f"ds_{self.ds}"
        self.doc = self.owner.post(f"/api/w/{self.ws}/documents", files=[("files", ("alpha.md", b"# Alpha\nsecret 1,234,567"))], headers=ORIGIN).json()["added"][0]["id"]
        self.h.settle()
        self.clients: dict[str, TestClient] = {"owner": self.owner}
        self.ids: dict[str, str] = {"owner": self.owner.get("/api/session").json()["user"]["id"]}
        for role in ("admin", "editor", "viewer"):
            self.clients[role], self.ids[role] = self.member(f"{role}@a.test", role.title(), role)
        self.other = self.owner.post("/api/workspaces", json={"name": "Beta LLC", "base_currency": "EUR"}, headers=ORIGIN).json()["id"]

    def tearDown(self) -> None:
        self.h.close()

    def member(self, email: str, name: str, role: str) -> tuple[TestClient, str]:
        inv = self.owner.post(f"/api/w/{self.ws}/invites", json={"email": email, "role": role}, headers=ORIGIN).json()
        c = TestClient(self.h.app, base_url="https://desk.example.com")
        c.portal = self.h.client.portal
        self.assertEqual(c.post("/api/auth/signup", json={"email": email, "name": name, "password": PW,
                                                           "invite_token": inv["link"].rsplit("/", 1)[1]}).status_code, 200)
        return c, c.get("/api/session").json()["user"]["id"]

    def fill(self, path: str) -> str:
        return (path.replace("{ws}", self.ws).replace("{did}", self.ds).replace("{doc}", self.doc)
                .replace("{user_id}", self.ids["viewer"]))

    # -- the sweep -------------------------------------------------------------------------------------------------
    def test_every_role_gated_route_refuses_every_lower_role(self) -> None:
        """Each of the engine's workspace routes that needs editor, admin or owner answers 403 to every member
        below that role — before any body is parsed or any handler runs — and the workspace state is unchanged."""
        before = fingerprint(self.h, self.ws, self.ledger)
        all_routes = routes()
        self.assertGreater(len(all_routes), 150)
        checked = 0
        for method, path, needed in all_routes:
            for role in ROLES:
                if role_at_least(role, needed) or role == "owner":
                    continue
                r = self.clients[role].request(method, self.fill(path), json={}, headers=ORIGIN)
                self.assertEqual(r.status_code, 403, (method, path, role, r.text[:200]))
                self.assertIn("role", r.json()["message"])
                checked += 1
        self.assertGreater(checked, 120)
        self.assertEqual(fingerprint(self.h, self.ws, self.ledger), before)
        # every one of those refusals is on the audit log, attributed to the member who tried
        denied = self.h.engine.db.scalar("SELECT COUNT(*) FROM audit_log WHERE workspace_id = ? AND action = 'access.denied' "
                                         "AND decision = 'denied'", (self.ws,))
        self.assertGreaterEqual(denied, checked)

    # -- specific cases -------------------------------------------------------------------------------------------
    def test_viewer_cannot_modify_financial_records_by_any_path(self) -> None:
        v = self.clients["viewer"]
        before = fingerprint(self.h, self.ws, self.ledger)
        attempts = [
            ("POST", f"/api/w/{self.ws}/close", {"month": "2026-09"}),
            ("DELETE", f"/api/w/{self.ws}/datasets/{self.ds}", None),
            ("PATCH", f"/api/w/{self.ws}/datasets/{self.ds}", {"name": "renamed"}),
            ("POST", f"/api/w/{self.ws}/imports", None),
            ("POST", f"/api/w/{self.ws}/dashboards", {"name": "x", "widgets": []}),
            ("POST", f"/api/w/{self.ws}/files/upload", None),
            ("POST", f"/api/w/{self.ws}/workflows", {"name": "w", "enabled": True, "definition": {"trigger": {"type": "manual"}, "steps": []}}),
            ("POST", f"/api/w/{self.ws}/connections/{self.ds}/sync", None),
            ("DELETE", f"/api/w/{self.ws}/documents/{self.doc}", None),
        ]
        for method, path, body in attempts:
            r = v.request(method, path, json=body, headers=ORIGIN)
            self.assertEqual(r.status_code, 403, (method, path, r.text[:200]))
        # through the agent: editor-only tools are not offered and are refused if named
        self.h.llm.script = [[("prepare_month_end_close", {"month": "2026-09"}), ("create_dashboard", {"name": "x", "template": "pnl", "dataset": self.ds}),
                              ("edit_file", {"file_id": "f", "edits": []})], "Done."]
        t = v.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).json()
        v.post(f"/api/w/{self.ws}/threads/{t['id']}/messages", json={"text": "do it"}, headers=ORIGIN)
        deadline = time.time() + 15
        while time.time() < deadline:
            data = v.get(f"/api/w/{self.ws}/threads/{t['id']}").json()
            if data["run"] and data["run"]["status"] in {"done", "error", "waiting_approval"}:
                break
            time.sleep(0.05)
        self.assertEqual(data["run"]["status"], "done")        # nothing to approve: none of the tools existed for a viewer
        tools = [m for m in data["messages"] if m["role"] == "tool"]
        self.assertTrue(all(m["content"].startswith(("Unknown tool", "Refused", "Error")) for m in tools), tools)
        # reading stays open
        self.assertEqual(v.get(f"/api/w/{self.ws}/datasets/{self.ds}").status_code, 200)
        self.assertEqual(fingerprint(self.h, self.ws, self.ledger), before)

    def test_editor_cannot_do_admin_or_owner_actions(self) -> None:
        e = self.clients["editor"]
        before = fingerprint(self.h, self.ws, self.ledger)
        for method, path, body in (
            ("PATCH", f"/api/w/{self.ws}/members/{self.ids['viewer']}", {"role": "editor"}),
            ("DELETE", f"/api/w/{self.ws}/members/{self.ids['viewer']}", None),
            ("POST", f"/api/w/{self.ws}/invites", {"email": "x@a.test", "role": "admin"}),
            ("POST", f"/api/w/{self.ws}/mcp-servers", {"name": "x", "transport": "http", "url": "https://x.test"}),
            ("PATCH", f"/api/w/{self.ws}/connections/c1", {"label": "x"}),
            ("POST", f"/api/w/{self.ws}/close/cl_x/status", {"status": "closed"}),
            ("GET", f"/api/w/{self.ws}/audit", None),
            ("PUT", f"/api/w/{self.ws}/files-settings", {}),
        ):
            r = e.request(method, path, json=body, headers=ORIGIN)
            self.assertEqual(r.status_code, 403, (method, path, r.text[:200]))
        # an admin cannot grant owner
        r = self.clients["admin"].patch(f"/api/w/{self.ws}/members/{self.ids['viewer']}", json={"role": "owner"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.h.engine.db.scalar("SELECT role FROM members WHERE workspace_id = ? AND user_id = ?", (self.ws, self.ids["viewer"])), "viewer")
        self.assertEqual(fingerprint(self.h, self.ws, self.ledger), before)

    def test_revocation_takes_effect_on_the_next_request_of_an_active_session(self) -> None:
        e = self.clients["editor"]
        self.assertEqual(e.post(f"/api/w/{self.ws}/close", json={"month": "2026-09"}, headers=ORIGIN).status_code, 200)
        # demoted while signed in: the very next protected call is refused; reads still work
        self.assertEqual(self.owner.patch(f"/api/w/{self.ws}/members/{self.ids['editor']}", json={"role": "viewer"}, headers=ORIGIN).status_code, 200)
        self.assertEqual(e.post(f"/api/w/{self.ws}/close", json={"month": "2026-09"}, headers=ORIGIN).status_code, 403)
        self.assertEqual(e.get(f"/api/w/{self.ws}/close").status_code, 200)
        self.assertEqual(e.get("/api/session").status_code, 200)              # the session itself is intact
        # removed: the workspace disappears for that session, with no trace of its data in the answer
        self.assertEqual(self.owner.delete(f"/api/w/{self.ws}/members/{self.ids['editor']}", headers=ORIGIN).status_code, 204)
        r = e.get(f"/api/w/{self.ws}/datasets/{self.ds}")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("GL", r.text)
        self.assertEqual(e.get("/api/session").json()["workspaces"], [])
        # disabled account: every request is refused and the session is gone
        self.h.engine.db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (self.ids["viewer"],))
        self.assertEqual(self.clients["viewer"].get(f"/api/w/{self.ws}/datasets").status_code, 401)

    def test_resource_ids_of_another_tenant_and_of_other_users_are_refused(self) -> None:
        before = fingerprint(self.h, self.ws, self.ledger)
        # Beta's owner (the same person as Alpha's owner here) has a private thread in Alpha the viewer must not see
        t = self.owner.post(f"/api/w/{self.ws}/threads", json={"visibility": "private"}, headers=ORIGIN).json()
        v = self.clients["viewer"]
        self.assertEqual(v.get(f"/api/w/{self.ws}/threads/{t['id']}").status_code, 404)
        self.assertEqual(v.get(f"/api/w/{self.ws}/threads/{t['id']}/events").status_code, 404)   # the stream too
        self.assertEqual(v.post(f"/api/w/{self.ws}/threads/{t['id']}/messages", json={"text": "hi"}, headers=ORIGIN).status_code, 404)
        self.assertEqual(v.delete(f"/api/w/{self.ws}/threads/{t['id']}", headers=ORIGIN).status_code, 404)
        # Alpha ids under Beta's path, by Alpha's own owner: scoping, not just membership
        for path in (f"/api/w/{self.other}/datasets/{self.ds}", f"/api/w/{self.other}/documents/{self.doc}",
                     f"/api/w/{self.other}/documents/{self.doc}/file", f"/api/w/{self.other}/datasets/{self.ds}/export.csv"):
            r = self.owner.get(path)
            self.assertEqual(r.status_code, 404, path)
            self.assertNotIn("1,234,567", r.text)
        self.assertEqual(self.owner.delete(f"/api/w/{self.other}/datasets/{self.ds}", headers=ORIGIN).status_code, 404)
        self.assertEqual(self.owner.delete(f"/api/w/{self.other}/documents/{self.doc}", headers=ORIGIN).status_code, 404)
        self.assertEqual(fingerprint(self.h, self.ws, self.ledger), before)
        # memory of Alpha is not retrievable from Beta
        self.owner.post(f"/api/w/{self.ws}/memories", json={"content": "Alpha auditor is Falcon & Co", "scope": "workspace"}, headers=ORIGIN)
        self.assertNotIn("Falcon", self.owner.get(f"/api/w/{self.other}/memories").text)

    def test_background_job_stops_when_its_creator_loses_the_editor_role(self) -> None:
        e = self.clients["editor"]
        wf = e.post(f"/api/w/{self.ws}/workflows", json={"name": "Flow", "enabled": True, "definition": {
            "trigger": {"type": "manual"}, "steps": [{"id": "a", "name": "A", "type": "approval", "message": "ok?"},
                                                      {"id": "n", "name": "N", "type": "notify", "recipients": "me", "message": "hi"}]}}, headers=ORIGIN).json()["id"]
        r = e.post(f"/api/w/{self.ws}/workflows/{wf}/run", json={}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        run = {"id": r.json()["run_id"]}
        self.h.wait_workflow(run["id"], {"waiting_approval"})
        appr = self.h.engine.db.one("SELECT id FROM approvals WHERE workspace_id = ? AND status = 'pending'", (self.ws,))
        # the creator is demoted while the run waits; the owner then approves the step
        self.assertEqual(self.owner.patch(f"/api/w/{self.ws}/members/{self.ids['editor']}", json={"role": "viewer"}, headers=ORIGIN).status_code, 200)
        self.assertEqual(self.h.engine.db.scalar("SELECT status FROM approvals WHERE id = ?", (appr["id"],)), "expired")
        r = self.owner.post(f"/api/w/{self.ws}/approvals/{appr['id']}/decide", json={"approve": True}, headers=ORIGIN)
        self.assertEqual(r.status_code, 409, r.text)
        final = self.h.engine.db.one("SELECT status FROM workflow_runs WHERE id = ?", (run["id"],))
        self.assertIn(final["status"], ("waiting_approval", "failed"))
        self.assertFalse(self.h.engine.db.all("SELECT 1 FROM notifications WHERE workspace_id = ? AND user_id = ? AND kind = 'workflow'",
                                              (self.ws, self.ids["editor"])))
        # a viewer cannot start a workflow at all
        self.assertEqual(self.clients["viewer"].post(f"/api/w/{self.ws}/workflows/{wf}/run", json={}, headers=ORIGIN).status_code, 403)


if __name__ == "__main__":
    unittest.main()
