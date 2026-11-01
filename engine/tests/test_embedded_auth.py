"""Embedded gateway: authentication, origin allow-list, rate limits, registration, audit of refusals
(threats T1, T10, T12, T15, T16, T17 in docs/embedded/SECURITY-THREAT-MODEL.md)."""

from __future__ import annotations

import unittest

from desk_engine.db import now
from tests.embedded_helpers import ORIGIN, audit_actions, issue_session, make_org, new_harness, run_input, signup, stream
from tests.helpers import FakeLLM


class Registration(unittest.TestCase):
    def setUp(self) -> None:
        self.h = new_harness()

    def tearDown(self) -> None:
        self.h.close()

    def test_registration_needs_admin_and_fresh_session(self) -> None:
        h = self.h
        org = make_org(h, email="o@acme.test", integration=False)
        # a viewer cannot register an integration
        viewer_token = signup(h, "v@acme.test", "Viewer")
        inv = h.api("POST", f"/api/w/{org.ws}/invites", headers=org.owner(), json={"email": "v@acme.test", "role": "viewer"})
        self.assertTrue(inv.get("added"))   # an existing account is added to the workspace directly
        r = h.client.post(f"/api/w/{org.ws}/embedded/integrations", headers={"authorization": f"Bearer {viewer_token}"},
                          json={"name": "x", "allowed_origins": [ORIGIN], "capabilities": ["ask_finance"]})
        self.assertEqual(r.status_code, 403)
        # the owner needs a fresh session (step-up): age the session's reauth mark
        h.engine.db.execute("UPDATE sessions SET reauth_at = ?", (now() - 3600,))
        r = h.client.post(f"/api/w/{org.ws}/embedded/integrations", headers=org.owner(),
                          json={"name": "x", "allowed_origins": [ORIGIN], "capabilities": ["ask_finance"]})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["error"], "reauth_required")
        h.engine.db.execute("UPDATE sessions SET reauth_at = ?", (now(),))
        created = h.api("POST", f"/api/w/{org.ws}/embedded/integrations", headers=org.owner(),
                        json={"name": "x", "allowed_origins": [ORIGIN], "capabilities": ["ask_finance"]})
        self.assertTrue(created["key"].startswith("oei_"))
        self.assertEqual(created["status"], "active")
        # the key is stored hashed; the plain value appears nowhere in the database
        for row in h.engine.db.all("SELECT * FROM embedded_integrations"):
            self.assertNotIn(created["key"], " ".join(str(v) for v in row.values()))
        # the service user is a viewer member and cannot be promoted
        member = h.engine.db.one("SELECT role FROM members WHERE workspace_id = ? AND user_id = ?",
                                 (org.ws, created["service_user_id"]))
        self.assertEqual(member["role"], "viewer")
        r = h.client.patch(f"/api/w/{org.ws}/members/{created['service_user_id']}", headers=org.owner(), json={"role": "editor"})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["error"], "service_user_role")
        # the service user cannot sign in
        r = h.client.post("/api/auth/login", json={"email": f"embedded+{created['id']}@integrations.ofintech.local", "password": "anything"})
        self.assertIn(r.status_code, (401, 403, 422, 429))

    def test_registration_audited(self) -> None:
        org = make_org(self.h, email="o@acme.test", session=False)
        actions = [a["action"] for a in audit_actions(self.h)]
        self.assertIn("embedded.integration.create", actions)
        detail = next(a for a in audit_actions(self.h) if a["action"] == "embedded.integration.create")["detail"]
        self.assertIn(ORIGIN, detail)
        self.assertNotIn(org.key, detail)

    def test_registration_validates_origins_and_capabilities(self) -> None:
        org = make_org(self.h, email="o@acme.test", integration=False)
        for bad in (["http://erp.partner.test"], ["https://erp.partner.test/path"], ["erp.partner.test"], []):
            r = self.h.client.post(f"/api/w/{org.ws}/embedded/integrations", headers=org.owner(),
                                   json={"name": "x", "allowed_origins": bad, "capabilities": ["ask_finance"]})
            self.assertEqual(r.status_code, 422, (bad, r.text))
        r = self.h.client.post(f"/api/w/{org.ws}/embedded/integrations", headers=org.owner(),
                               json={"name": "x", "allowed_origins": [ORIGIN], "capabilities": ["payments"]})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"], "bad_capability")

    def test_sealed_workspace_refuses_integrations(self) -> None:
        h = self.h
        org = make_org(h, email="o@acme.test")
        h.api("PATCH", f"/api/w/{org.ws}", headers=org.owner(), json={"egress_policy": "sealed"})
        # existing sessions stop working at once, and new integrations cannot be created
        r = h.client.get("/api/embedded/agents", headers=org.page())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "egress_blocked")
        r = h.client.post(f"/api/w/{org.ws}/embedded/integrations", headers=org.owner(),
                          json={"name": "y", "allowed_origins": [ORIGIN], "capabilities": ["ask_finance"]})
        self.assertEqual(r.status_code, 403)
        r = h.client.post("/api/embedded/sessions", headers={"authorization": f"Bearer {org.key}"}, json={"subject": "u"})
        self.assertEqual(r.status_code, 403)


class Sessions(unittest.TestCase):
    def setUp(self) -> None:
        self.h = new_harness(script=["Fine."])
        self.org = make_org(self.h, email="o@acme.test")

    def tearDown(self) -> None:
        self.h.close()

    def test_session_issue_and_shape(self) -> None:
        data = issue_session(self.org, "user-2", locale="ar", ttl=120)
        self.assertTrue(data["token"].startswith("oes_"))
        self.assertEqual([a["id"] for a in data["agents"]], ["finance-copilot", "financial-intelligence", "reconciliation-review"])
        row = self.h.engine.db.one("SELECT * FROM embedded_sessions WHERE id = ?", (data["session_id"],))
        self.assertEqual(row["locale"], "ar")
        self.assertAlmostEqual(row["expires_at"] - row["created_at"], 120, delta=1)
        self.assertNotIn("user-2", " ".join(str(v) for v in row.values()))   # subject stored as a hash only
        issue_session(self.org, "u", ttl=10, expect=422)
        issue_session(self.org, "u", ttl=7200, expect=422)

    def test_expired_session_refused(self) -> None:
        self.h.engine.db.execute("UPDATE embedded_sessions SET expires_at = ?", (now() - 1,))
        r = self.h.client.get("/api/embedded/agents", headers=self.org.page())
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers.get("www-authenticate"), "Bearer")

    def test_revoked_session_refused(self) -> None:
        self.org.admin_api("DELETE", f"/api/w/{self.org.ws}/embedded/sessions/{self.org.session_id}", expect=204)
        r = self.h.client.get("/api/embedded/agents", headers=self.org.page())
        self.assertEqual(r.status_code, 401)

    def test_error_bodies_do_not_distinguish_reason(self) -> None:
        h, org = self.h, self.org
        bodies = []
        bodies.append(h.client.get("/api/embedded/agents", headers=org.page(token="oes_" + "x" * 43)).json())
        h.engine.db.execute("UPDATE embedded_sessions SET expires_at = ?", (now() - 1,))
        bodies.append(h.client.get("/api/embedded/agents", headers=org.page()).json())
        h.engine.db.execute("UPDATE embedded_sessions SET expires_at = ?, revoked_at = ?", (now() + 600, now()))
        bodies.append(h.client.get("/api/embedded/agents", headers=org.page()).json())
        self.assertEqual(len({b["message"] for b in bodies}), 1, bodies)

    def test_integration_key_not_accepted_on_run(self) -> None:
        r = self.h.client.post("/api/embedded/agents/finance-copilot/run", headers=self.org.page(token=self.org.key),
                               json=run_input("hi"))
        self.assertEqual(r.status_code, 401)
        # and a session token cannot mint sessions
        r = self.h.client.post("/api/embedded/sessions", headers={"authorization": f"Bearer {self.org.session_token}"},
                               json={"subject": "x"})
        self.assertEqual(r.status_code, 401)

    def test_suspended_integration_refuses_sessions(self) -> None:
        org = self.org
        org.admin_api("PATCH", f"/api/w/{org.ws}/embedded/integrations/{org.integration['id']}", json={"status": "suspended"})
        r = self.h.client.post("/api/embedded/sessions", headers={"authorization": f"Bearer {org.key}"}, json={"subject": "u"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "integration_suspended")
        # sessions issued before the suspension are closed
        r = self.h.client.get("/api/embedded/agents", headers=org.page())
        self.assertEqual(r.status_code, 401)
        org.admin_api("PATCH", f"/api/w/{org.ws}/embedded/integrations/{org.integration['id']}", json={"status": "active"})
        issue_session(org, "u")
        self.assertEqual(self.h.client.get("/api/embedded/agents", headers=org.page()).status_code, 200)

    def test_rotated_key_invalidates_old(self) -> None:
        org = self.org
        old = org.key
        rotated = org.admin_api("POST", f"/api/w/{org.ws}/embedded/integrations/{org.integration['id']}/rotate")
        self.assertNotEqual(rotated["key"], old)
        r = self.h.client.post("/api/embedded/sessions", headers={"authorization": f"Bearer {old}"}, json={"subject": "u"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.h.client.get("/api/embedded/agents", headers=org.page()).status_code, 401)
        org.key = rotated["key"]
        issue_session(org, "u")
        self.assertEqual(self.h.client.get("/api/embedded/agents", headers=org.page()).status_code, 200)

    def test_revoked_integration_removes_service_user_access(self) -> None:
        org = self.org
        svc = org.integration["service_user_id"]
        org.admin_api("DELETE", f"/api/w/{org.ws}/embedded/integrations/{org.integration['id']}", expect=204)
        self.assertIsNone(self.h.engine.db.one("SELECT 1 FROM members WHERE user_id = ?", (svc,)))
        self.assertEqual(self.h.client.get("/api/embedded/agents", headers=org.page()).status_code, 401)
        self.assertEqual(org.admin_api("GET", f"/api/w/{org.ws}/embedded/integrations"), [])


class Origins(unittest.TestCase):
    def setUp(self) -> None:
        self.h = new_harness(script=["Fine."])
        self.org = make_org(self.h, email="o@acme.test")

    def tearDown(self) -> None:
        self.h.close()

    def test_origin_not_allowed(self) -> None:
        r = self.h.client.get("/api/embedded/agents", headers=self.org.page(origin="https://evil.test"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "origin_not_allowed")
        self.assertIsNone(r.headers.get("access-control-allow-origin"))
        # a browser request without an Origin is refused too (the session token is for web pages)
        r = self.h.client.get("/api/embedded/agents", headers={"authorization": f"Bearer {self.org.session_token}"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "origin_required")
        # the allowed origin gets exactly itself back, never a wildcard
        r = self.h.client.get("/api/embedded/agents", headers=self.org.page())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("access-control-allow-origin"), ORIGIN)
        self.assertEqual(r.headers.get("vary"), "Origin")

    def test_preflight_for_unknown_origin_refused(self) -> None:
        r = self.h.client.options("/api/embedded/agents/finance-copilot/run", headers={"origin": "https://evil.test"})
        self.assertEqual(r.status_code, 403)
        r = self.h.client.options("/api/embedded/agents/finance-copilot/run", headers={"origin": ORIGIN})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers["access-control-allow-origin"], ORIGIN)
        self.assertIn("authorization", r.headers["access-control-allow-headers"])


class RateLimits(unittest.TestCase):
    def setUp(self) -> None:
        self.h = new_harness(script=["Fine."] * 50)
        self.org = make_org(self.h, email="o@acme.test", limits={"runs_per_minute": 3, "runs_per_minute_session": 2})

    def tearDown(self) -> None:
        self.h.close()

    def test_unauthenticated_rate_limit(self) -> None:
        codes = [self.h.client.get("/api/embedded/agents", headers=self.org.page(token="oes_bad")).status_code for _ in range(70)]
        self.assertIn(401, codes)
        self.assertIn(429, codes)
        self.assertEqual(codes[-1], 429)

    def test_integration_rate_limit(self) -> None:
        org = self.org
        # session limit (2/min) trips first on one session
        stream(org, run_input("one", run="r1"))
        stream(org, run_input("two", run="r2"))
        with self.assertRaises(AssertionError) as ctx:
            stream(org, run_input("three", run="r3"))
        self.assertIn("429", str(ctx.exception))
        # a second session of the same integration hits the integration limit (3/min) after one more run
        issue_session(org, "user-2")
        stream(org, run_input("four", run="r4"))
        with self.assertRaises(AssertionError) as ctx:
            stream(org, run_input("five", run="r5"))
        self.assertIn("429", str(ctx.exception))


class Audit(unittest.TestCase):
    def setUp(self) -> None:
        self.h = new_harness(script=["Fine."])
        self.org = make_org(self.h, email="o@acme.test")

    def tearDown(self) -> None:
        self.h.close()

    def test_every_refusal_is_audited(self) -> None:
        h, org = self.h, self.org
        before = len(audit_actions(h, "embedded.denied"))
        h.client.get("/api/embedded/agents", headers=org.page(token="oes_" + "x" * 43))           # bad token
        h.client.get("/api/embedded/agents", headers=org.page(origin="https://evil.test"))        # bad origin
        h.client.post("/api/embedded/sessions", headers={"authorization": "Bearer oei_nope"}, json={"subject": "u"})
        h.client.post("/api/embedded/agents/financial-intelligence/run", headers=org.page(), json=run_input("x"))  # has it, ok
        org.admin_api("PATCH", f"/api/w/{org.ws}/embedded/integrations/{org.integration['id']}", json={"capabilities": ["ask_finance"]})
        issue_session(org, "u")
        h.client.post("/api/embedded/agents/financial-intelligence/run", headers=org.page(), json=run_input("x"))  # capability missing
        denied = audit_actions(h, "embedded.denied")[before:]
        reasons = [a["detail"] for a in denied]
        self.assertEqual(len(denied), 4, reasons)
        for needle in ("bad_session_token", "origin_not_allowed", "bad_integration_key", "capability_missing"):
            self.assertTrue(any(needle in r for r in reasons), needle)
        # the audit log holds a hash prefix of the token, never the token
        self.assertFalse(any("oes_" in r for r in reasons))
        # audit entries cannot be altered
        with self.assertRaises(Exception):
            h.engine.db.execute("DELETE FROM audit_log WHERE action = 'embedded.denied'")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
