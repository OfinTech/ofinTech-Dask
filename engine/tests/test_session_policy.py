"""The session policy beyond inactivity: absolute lifetimes (12 h, 8 h for admins), step-up authentication for
high-risk actions, and every way a session is revoked or revalidated (logout, expiry, disabled account, removal
from a workspace, lost permission, administrator termination, password change)."""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from starlette.testclient import TestClient

from desk_engine.security import token_hash

from .helpers import Harness

PW = "Correct-Horse-9"
ORIGIN = {"origin": "https://desk.example.com"}
H = 3600


class Clock:
    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Policy(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        for target in ("desk_engine.web.now", "desk_engine.routes.auth.now"):
            patcher = mock.patch(target, self.clock)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.h = Harness("server")
        self.c = self.h.client
        self.assertEqual(self.c.post("/api/auth/signup", json={"email": "owner@acme.test", "name": "O", "password": PW}).status_code, 200)
        self.uid = self.c.get("/api/session").json()["user"]["id"]
        self.ws = self.c.post("/api/workspaces", json={"name": "Acme", "base_currency": "USD"}, headers=ORIGIN).json()["id"]
        inv = self.c.post(f"/api/w/{self.ws}/invites", json={"email": "ed@acme.test", "role": "editor"}, headers=ORIGIN).json()
        self.ed = TestClient(self.h.app, base_url="https://desk.example.com")
        self.ed.portal = self.c.portal
        self.assertEqual(self.ed.post("/api/auth/signup", json={"email": "ed@acme.test", "name": "Ed", "password": PW,
                                                                "invite_token": inv["link"].rsplit("/", 1)[1]}).status_code, 200)
        self.ed_id = self.ed.get("/api/session").json()["user"]["id"]

    def tearDown(self) -> None:
        self.h.close()

    def signed_in(self, c: TestClient) -> bool:
        return c.get("/api/session").json()["user"] is not None

    def audit(self, action: str) -> list[dict]:
        return self.h.engine.db.all("SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,))

    # -- lifetimes -------------------------------------------------------------------------------------------------
    def test_lifetimes_twelve_hours_and_eight_for_administrators(self) -> None:
        """Ed (editor only) lives 12 hours; the owner 8. Both stay active the whole time. A promotion shortens the
        sitting session at the next request."""
        def active(clients: list[TestClient], seconds: float) -> None:
            """Everyone does something every 29 minutes (inside the 30-minute idle window) for this long."""
            step = 29 * 60
            done = 0
            for c in clients:
                self.assertEqual(c.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).status_code, 200, "start")
            while done + step <= seconds:
                self.clock.advance(step)
                done += step
                for c in clients:
                    self.assertEqual(c.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).status_code, 200, done)
            self.clock.advance(seconds - done)

        active([self.c, self.ed], 7 * H + 59 * 60)      # 7 h 59 min, both active throughout
        self.assertTrue(self.signed_in(self.c) and self.signed_in(self.ed))
        self.clock.advance(61)                          # 8 h 1 s
        self.assertFalse(self.signed_in(self.c))        # the owner: out
        self.assertTrue(self.signed_in(self.ed))        # the editor: in
        self.assertEqual(json.loads(self.audit("session.expired")[-1]["detail"])["reason"], "lifetime")
        active([self.ed], 3 * H + 58 * 60)              # up to 11 h 59 min, still active
        self.assertTrue(self.signed_in(self.ed))
        self.clock.advance(2 * 60)                      # 12 h 1 min
        self.assertFalse(self.signed_in(self.ed))
        # promotion shortens: a fresh editor session, promoted to admin at hour 7, is over at hour 8
        self.ed.post("/api/auth/login", json={"email": "ed@acme.test", "password": PW}, headers=ORIGIN)
        self.c.post("/api/auth/login", json={"email": "owner@acme.test", "password": PW}, headers=ORIGIN)
        active([self.c, self.ed], 7 * H)
        self.assertEqual(self.c.patch(f"/api/w/{self.ws}/members/{self.ed_id}", json={"role": "admin"}, headers=ORIGIN).status_code, 403)   # 7 h since the owner's sign-in: no longer fresh
        self.assertEqual(self.c.post("/api/auth/reauth", json={"password": PW}, headers=ORIGIN).status_code, 200)
        self.assertEqual(self.c.patch(f"/api/w/{self.ws}/members/{self.ed_id}", json={"role": "admin"}, headers=ORIGIN).status_code, 200)
        self.assertTrue(self.signed_in(self.ed))
        active([self.ed], 59 * 60)
        self.assertTrue(self.signed_in(self.ed))
        self.clock.advance(61)
        self.assertFalse(self.signed_in(self.ed))       # 8 h for an admin, counted from sign-in

    # -- step-up ---------------------------------------------------------------------------------------------------
    def test_high_risk_actions_need_a_recent_password(self) -> None:
        """Right after signing in the person is fresh; eleven minutes later a role change, a connection, an MCP
        server, closing a month, deleting a dataset and approving an outbound action all ask for the password; a
        wrong password is refused and audited; the right one opens a ten-minute window; reads and ordinary
        writes never ask; the desktop app never asks."""
        up = self.c.post(f"/api/w/{self.ws}/imports", files={"file": ("gl.csv", b"account,amount\n4000,1\n")}, headers=ORIGIN).json()
        ds = self.c.post(f"/api/w/{self.ws}/imports/{up['upload_id']}/commit", json={"name": "GL", "sheet": None, "header_row": 0}, headers=ORIGIN).json()["id"]
        self.assertEqual(self.c.patch(f"/api/w/{self.ws}/members/{self.ed_id}", json={"role": "viewer"}, headers=ORIGIN).status_code, 200)   # fresh
        self.clock.advance(11 * 60)
        self.assertEqual(self.c.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).status_code, 200)  # activity, not freshness
        risky = [("PATCH", f"/api/w/{self.ws}/members/{self.ed_id}", {"role": "editor"}),
                 ("DELETE", f"/api/w/{self.ws}/members/{self.ed_id}", None),
                 ("POST", f"/api/w/{self.ws}/connections/custom", {"label": "ERP", "mode": "push"}),
                 ("POST", f"/api/w/{self.ws}/mcp-servers", {"name": "x", "transport": "http", "url": "https://mcp.example.test/"}),
                 ("POST", f"/api/w/{self.ws}/providers", {"kind": "openai_compatible", "label": "M", "base_url": "https://llm.example.test/v1", "api_key": "k"}),
                 ("DELETE", f"/api/w/{self.ws}/datasets/{ds}", None),
                 ("POST", f"/api/w/{self.ws}/close/cl_x/status", {"status": "closed"}),
                 ("PATCH", f"/api/w/{self.ws}", {"egress_policy": "sealed"}),
                 ("POST", f"/api/w/{self.ws}/mcp-keys", {"name": "k"}),
                 ("PUT", f"/api/w/{self.ws}/files-settings", {}),
                 ("DELETE", f"/api/w/{self.ws}", None)]
        for method, path, body in risky:
            r = self.c.request(method, path, json=body, headers=ORIGIN)
            self.assertEqual(r.status_code, 403, (method, path, r.text))
            self.assertEqual(r.json()["error"], "reauth_required", (method, path))
        self.assertEqual(self.h.engine.db.scalar("SELECT COUNT(*) FROM datasets WHERE id = ?", (ds,)), 1)
        self.assertEqual(self.h.engine.db.scalar("SELECT role FROM members WHERE user_id = ?", (self.ed_id,)), "viewer")
        # ordinary writes and reads do not ask
        self.assertEqual(self.c.post(f"/api/w/{self.ws}/memories", json={"content": "x", "scope": "workspace"}, headers=ORIGIN).status_code, 200)
        self.assertEqual(self.c.get(f"/api/w/{self.ws}/datasets/{ds}").status_code, 200)
        # wrong password: refused, audited, still not fresh
        r = self.c.post("/api/auth/reauth", json={"password": "Wrong-Password-77"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 401)
        self.assertTrue(self.audit("user.reauth.failed"))
        self.assertEqual(self.c.patch(f"/api/w/{self.ws}/members/{self.ed_id}", json={"role": "editor"}, headers=ORIGIN).status_code, 403)
        # right password: a ten-minute window
        r = self.c.post("/api/auth/reauth", json={"password": PW}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(r.json()["fresh_until"], self.clock.t + 600, delta=2)
        self.assertEqual(self.c.patch(f"/api/w/{self.ws}/members/{self.ed_id}", json={"role": "editor"}, headers=ORIGIN).status_code, 200)
        self.assertEqual(self.c.get("/api/session/status").json()["fresh_seconds_left"], 600)
        self.clock.advance(601)
        self.assertEqual(self.c.patch(f"/api/w/{self.ws}/members/{self.ed_id}", json={"role": "viewer"}, headers=ORIGIN).status_code, 403)
        self.assertNotIn("Wrong-Password-77", json.dumps(self.h.engine.db.all("SELECT * FROM audit_log"), default=str))
        # the desktop app: no password, never asks
        d = Harness()
        try:
            ws = d.setup_workspace(with_model=False)
            self.assertEqual(d.client.patch(f"/api/w/{ws}", json={"name": "Renamed"}).status_code, 200)
            self.assertEqual(d.client.post("/api/auth/reauth", json={"password": "anything"}).status_code, 200)
        finally:
            d.close()

    def test_approving_an_outbound_action_needs_a_recent_password(self) -> None:
        self.h.engine.db.insert("approvals", {"id": "ap_t1", "workspace_id": self.ws, "source": "agent", "source_id": "run_x",
                                              "tool": "message_colleague", "arguments": "{}", "summary": "msg", "status": "pending",
                                              "requested_by": self.uid, "created_at": self.clock.t, "expires_at": self.clock.t + 3600})
        self.h.engine.db.insert("approvals", {"id": "ap_t2", "workspace_id": self.ws, "source": "agent", "source_id": "run_x",
                                              "tool": "edit_file", "arguments": "{}", "summary": "edit", "status": "pending",
                                              "requested_by": self.uid, "created_at": self.clock.t, "expires_at": self.clock.t + 3600})
        self.clock.advance(11 * 60)
        r = self.c.post(f"/api/w/{self.ws}/approvals/ap_t1/decide", json={"approve": True}, headers=ORIGIN)
        self.assertEqual((r.status_code, r.json()["error"]), (403, "reauth_required"))
        self.assertEqual(self.h.engine.db.scalar("SELECT status FROM approvals WHERE id = 'ap_t1'"), "pending")
        # rejecting never needs it; an internal edit never needs it
        self.assertEqual(self.c.post(f"/api/w/{self.ws}/approvals/ap_t2/decide", json={"approve": True}, headers=ORIGIN).status_code, 200)
        self.assertEqual(self.c.post("/api/auth/reauth", json={"password": PW}, headers=ORIGIN).status_code, 200)
        self.assertEqual(self.c.post(f"/api/w/{self.ws}/approvals/ap_t1/decide", json={"approve": True}, headers=ORIGIN).status_code, 200)

    # -- revocation --------------------------------------------------------------------------------------------------
    def test_every_revocation_path(self) -> None:
        phone = TestClient(self.h.app, base_url="https://desk.example.com")
        phone.portal = self.c.portal
        phone.post("/api/auth/login", json={"email": "owner@acme.test", "password": PW}, headers=ORIGIN)
        # 1. logout ends that session only
        self.assertEqual(phone.post("/api/auth/logout", headers=ORIGIN).status_code, 200)
        self.assertFalse(self.signed_in(phone))
        self.assertTrue(self.signed_in(self.c))
        # 2. a listed session can be ended from another device; "sign out everywhere" ends the rest
        phone.post("/api/auth/login", json={"email": "owner@acme.test", "password": PW}, headers=ORIGIN)
        sessions = self.c.get("/api/auth/sessions").json()
        self.assertEqual(len(sessions), 2)
        other = next(s for s in sessions if not s["current"])
        self.assertNotIn(self.c.cookies.get("desk_session", ""), json.dumps(sessions))     # ids are not tokens
        self.assertEqual(self.c.delete(f"/api/auth/sessions/{other['id']}", headers=ORIGIN).status_code, 204)
        self.assertFalse(self.signed_in(phone))
        self.assertTrue(self.audit("session.revoked"))
        phone.post("/api/auth/login", json={"email": "owner@acme.test", "password": PW}, headers=ORIGIN)
        self.assertEqual(self.c.post("/api/auth/sign-out-everywhere", headers=ORIGIN).json()["revoked"], 1)
        self.assertFalse(self.signed_in(phone))
        self.assertTrue(self.signed_in(self.c))
        # 3. the instance administrator (the first account) terminates another person's sessions, with a fresh password
        self.assertTrue(self.signed_in(self.ed))
        self.clock.advance(11 * 60)
        self.assertEqual(self.c.post(f"/api/admin/users/{self.ed_id}/sign-out", headers=ORIGIN).json()["error"], "reauth_required")
        self.c.post("/api/auth/reauth", json={"password": PW}, headers=ORIGIN)
        self.assertEqual(self.c.post(f"/api/admin/users/{self.ed_id}/sign-out", headers=ORIGIN).json()["revoked"], 1)
        self.assertFalse(self.signed_in(self.ed))
        self.assertEqual(json.loads(self.audit("session.revoked")[-1]["detail"])["by"], "instance_admin")
        # 4. a password change ends every session of that person and starts a fresh one
        self.ed.post("/api/auth/login", json={"email": "ed@acme.test", "password": PW}, headers=ORIGIN)
        old = self.ed.cookies.get("desk_session")
        r = self.ed.post("/api/auth/password", json={"current": PW, "new": "Another-Horse-10"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.h.engine.db.one("SELECT 1 FROM sessions WHERE id = ?", (token_hash(old),)))
        self.assertTrue(self.signed_in(self.ed))
        # 5. a disabled account is refused on its next request and its sessions are gone
        self.h.engine.db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (self.ed_id,))
        self.assertEqual(self.ed.get(f"/api/w/{self.ws}/datasets").status_code, 401)
        self.h.engine.db.execute("UPDATE users SET disabled = 0 WHERE id = ?", (self.ed_id,))
        # 6. removal from the workspace: the session lives, the workspace is gone (404), approvals expired
        self.ed.post("/api/auth/login", json={"email": "ed@acme.test", "password": "Another-Horse-10"}, headers=ORIGIN)
        self.c.post("/api/auth/reauth", json={"password": PW}, headers=ORIGIN)
        self.assertEqual(self.c.delete(f"/api/w/{self.ws}/members/{self.ed_id}", headers=ORIGIN).status_code, 204)
        self.assertEqual(self.ed.get(f"/api/w/{self.ws}/datasets").status_code, 404)
        self.assertTrue(self.signed_in(self.ed))
        # 7. a non-member's session cannot be used to renew anything in that workspace, and an unknown token is refused
        stranger = TestClient(self.h.app, base_url="https://desk.example.com", headers={"authorization": "Bearer " + "x" * 43})
        stranger.portal = self.c.portal
        self.assertEqual(stranger.post("/api/session/ping", headers=ORIGIN).status_code, 401)
        self.assertEqual(stranger.post("/api/auth/reauth", json={"password": PW}, headers=ORIGIN).status_code, 401)

    def test_background_credentials_are_not_sessions(self) -> None:
        """An MCP access key and a push connection's ingest key authenticate their own endpoints and nothing else:
        they open no session, and a session cookie opens neither of those endpoints."""
        self.c.post("/api/auth/reauth", json={"password": PW}, headers=ORIGIN)
        key = self.c.post(f"/api/w/{self.ws}/mcp-keys", json={"name": "k"}, headers=ORIGIN).json()["key"]
        conn = self.c.post(f"/api/w/{self.ws}/connections/custom", json={"label": "Tally", "mode": "push"}, headers=ORIGIN).json()
        ingest = conn["ingest_key"]
        bot = TestClient(self.h.app, base_url="https://desk.example.com", headers={"authorization": f"Bearer {key}"})
        bot.portal = self.c.portal
        self.assertEqual(bot.get("/api/session").json()["user"], None)
        self.assertEqual(bot.get(f"/api/w/{self.ws}/datasets").status_code, 401)
        self.assertEqual(self.h.engine.db.scalar("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (self.uid,)), 1)
        bot2 = TestClient(self.h.app, base_url="https://desk.example.com", headers={"authorization": f"Bearer {ingest}"})
        bot2.portal = self.c.portal
        self.assertEqual(bot2.get(f"/api/w/{self.ws}/datasets").status_code, 401)
        r = self.c.post("/api/ingest/v1/accounting/profit-and-loss", json={"lines": []}, headers=ORIGIN)
        self.assertEqual(r.status_code, 401)     # a session is not an ingest key either


if __name__ == "__main__":
    unittest.main()
