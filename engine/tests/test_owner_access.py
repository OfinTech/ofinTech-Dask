"""Owner Control Center: who gets in, what each role may do, and that platform administration never reaches
customer data. Also the account-level controls (suspension, forced reset, restrictions, support access, MFA)."""

from __future__ import annotations

import time

from desk_engine.platform import mfa
from desk_engine.platform.settings import set_setting

from .helpers import Harness
from .owner_base import ORIGIN, OWNER, PW, OwnerBase


class Access(OwnerBase):
    def test_only_platform_administrators_reach_the_owner_api(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        anon = self.client()
        self.assertEqual(anon.get("/api/owner/overview").status_code, 401)
        r = alice.get("/api/owner/overview")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "owner_required")
        # the refusal is a security event
        row = self.engine.db.one("SELECT * FROM audit_log WHERE action = 'access.denied' AND user_id = ? ORDER BY id DESC", (alice.user_id,))
        self.assertIsNotNone(row)
        self.assertEqual(row["decision"], "denied")
        # the owner is in, with the full permission list
        r = self.owner.get("/api/owner/overview")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["role"], "owner")
        self.assertIn("billing.configure", r.json()["permissions"])
        # the session tells the page who is a platform administrator (and nothing lets the page decide that)
        s = alice.get("/api/session").json()
        self.assertIsNone(s["user"]["platform_role"])
        self.assertEqual(s["user"]["permissions"], [])

    def test_owner_role_cannot_be_spoofed_or_self_assigned(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        # the profile route ignores unknown fields; the platform role is not a profile attribute
        r = alice.patch("/api/me", json={"name": "Alice", "platform_role": "owner", "instance_admin": True}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(alice.get("/api/session").json()["user"]["platform_role"])
        # the delegation route is itself owner-only
        self.assertEqual(alice.post("/api/owner/admins", json={"email": "alice@acme.test", "role": "owner"}, headers=ORIGIN).status_code, 403)
        # and the public sign-up form never creates a platform role, whatever the address claims
        self.open_signup()
        c = self.client()
        c.post("/api/auth/signup", json={"email": "root@platform.test", "name": "Root", "password": PW})
        self.assertIsNone(c.get("/api/session").json()["user"]["platform_role"])
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM platform_admins"), 1)

    def test_delegated_administrator_has_a_subset(self) -> None:
        dana = self.invite_user("dana@platform.test", "Dana")
        r = self.owner.post("/api/owner/admins", json={"email": "dana@platform.test", "role": "admin"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(dana.get("/api/owner/users").status_code, 200)
        self.assertEqual(dana.get("/api/owner/billing").status_code, 200)        # may read
        r = dana.put("/api/owner/billing", json={"enabled": True}, headers=ORIGIN)   # may not configure
        self.assertEqual(r.status_code, 403)
        self.assertEqual(dana.patch(f"/api/owner/plans/{self.plan_id('pro')}", json={"monthly_price": 1}, headers=ORIGIN).status_code, 403)
        self.assertEqual(dana.put("/api/owner/security", json={"mfa_policy": "all"}, headers=ORIGIN).status_code, 403)
        self.assertEqual(dana.post("/api/owner/admins", json={"email": OWNER, "role": "admin"}, headers=ORIGIN).status_code, 403)
        # revoking works and is audited; the owner cannot remove themself
        self.assertEqual(self.owner.delete(f"/api/owner/admins/{self.owner_id}", headers=ORIGIN).status_code, 422)
        self.assertEqual(self.owner.delete(f"/api/owner/admins/{dana.user_id}", headers=ORIGIN).status_code, 200)
        self.assertEqual(dana.get("/api/owner/users").status_code, 403)
        self.assertTrue(self.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'platform.admin.revoke' AND target = ?", (dana.user_id,)))

    def test_mutations_need_recent_authentication(self) -> None:
        self.stale_reauth(self.owner)
        r = self.owner.put("/api/owner/settings", json={"values": {"platform.name": "Acme Desk"}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "reauth_required")
        self.assertEqual(self.owner.post("/api/auth/reauth", json={"password": PW}, headers=ORIGIN).status_code, 200)
        r = self.owner.put("/api/owner/settings", json={"values": {"platform.name": "Acme Desk"}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.owner.get("/api/session").json()["platform"]["platform.name"], "Acme Desk")

    def test_desktop_has_no_owner_area(self) -> None:
        h = Harness("desktop")
        try:
            self.assertEqual(h.client.get("/api/owner/overview").status_code, 404)
        finally:
            h.close()

    def test_secrets_are_write_only_and_settings_are_validated_and_audited(self) -> None:
        self.configure_stripe()
        r = self.owner.get("/api/owner/billing").json()
        self.assertTrue(r["secret_key"]["configured"])
        self.assertEqual(r["secret_key"]["last4"], "0000")
        for page in ("/api/owner/billing", "/api/owner/integrations", "/api/owner/settings", "/api/owner/overview", "/api/owner/audit"):
            self.assertNotIn("sk_test_", self.owner.get(page).text, page)
            self.assertNotIn("whsec_", self.owner.get(page).text, page)
        # a secret is never echoed in the audit log either
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM audit_log WHERE detail LIKE '%sk_test_%'"), 0)
        self.assertTrue(self.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'platform.secret.update' AND target = 'stripe.secret_key'"))
        # validation: out-of-range and unknown settings are refused, feature flags cannot touch security
        self.assertEqual(self.owner.put("/api/owner/security", json={"lockout_threshold": 1}, headers=ORIGIN).status_code, 422)
        self.assertEqual(self.owner.put("/api/owner/settings", json={"values": {"security.mfa_policy": "none"}}, headers=ORIGIN).status_code, 422)
        self.assertEqual(self.owner.put("/api/owner/settings", json={"values": {"auth.disabled": True}}, headers=ORIGIN).status_code, 422)
        r = self.owner.put("/api/owner/security", json={"session_idle_minutes": 10}, headers=ORIGIN)
        self.assertEqual(r.status_code, 422)   # the dashboard cannot loosen the inactivity window below the engine's floor
        r = self.owner.put("/api/owner/security", json={"session_idle_minutes": 40, "lockout_threshold": 5}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.engine.settings.session_idle_minutes, 40)   # applied at once
        self.assertEqual(self.owner.get("/api/session").json()["idle_minutes"], 40)

    def test_dual_approval_for_critical_changes(self) -> None:
        second = self.invite_user("second@platform.test", "Second Owner")
        self.owner.post("/api/owner/admins", json={"email": "second@platform.test", "role": "owner"}, headers=ORIGIN)
        self.assertEqual(self.owner.put("/api/owner/security", json={"dual_approval": True}, headers=ORIGIN).status_code, 200)
        r = self.owner.patch(f"/api/owner/plans/{self.plan_id('pro')}", json={"monthly_price": 3900}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["pending_approval"])
        cid = r.json()["change_request"]
        self.assertEqual(self.engine.db.scalar("SELECT monthly_price FROM plans WHERE code = 'pro'"), 2900)   # nothing applied yet
        # the requester cannot approve their own change
        r = self.owner.post(f"/api/owner/changes/{cid}/decide", json={"approve": True}, headers=ORIGIN)
        self.assertEqual(r.status_code, 403)
        r = second.post(f"/api/owner/changes/{cid}/decide", json={"approve": True}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.engine.db.scalar("SELECT monthly_price FROM plans WHERE code = 'pro'"), 3900)
        self.assertTrue(self.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'platform.change.approved'"))

    def test_platform_administration_never_reads_workspace_content(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        ws = alice.post("/api/workspaces", json={"name": "Alice Ltd"}, headers=ORIGIN).json()["id"]
        alice.post(f"/api/w/{ws}/documents", files=[("files", ("secret.md", b"# Secret\nrevenue 9,999,999"))], headers=ORIGIN)
        self.h.ws = ws
        self.h.settle()
        # the owner sees the account and usage figures, never the content
        detail = self.owner.get(f"/api/owner/workspaces/{ws}").json()
        self.assertEqual(detail["counts"]["documents"], 1)
        self.assertNotIn("9,999,999", self.owner.get(f"/api/owner/workspaces/{ws}").text)
        self.assertNotIn("secret.md", self.owner.get(f"/api/owner/workspaces/{ws}").text)
        # and has no membership: the workspace routes answer 404 (not a member), audited as a cross-tenant attempt
        self.assertEqual(self.owner.get(f"/api/w/{ws}/documents").status_code, 404)
        self.assertTrue(self.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'access.denied' AND workspace_id = ? AND user_id = ?", (ws, self.owner_id)))

    def test_support_access_is_bounded_read_only_and_visible(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        ws = alice.post("/api/workspaces", json={"name": "Alice Ltd"}, headers=ORIGIN).json()["id"]
        self.assertEqual(self.owner.post(f"/api/owner/workspaces/{ws}/support-access", json={"reason": "x", "hours": 2}, headers=ORIGIN).status_code, 422)
        r = self.owner.post(f"/api/owner/workspaces/{ws}/support-access", json={"reason": "Ticket 4411: import fails", "hours": 2}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        gid = r.json()["id"]
        self.assertEqual(self.owner.get(f"/api/w/{ws}/documents").status_code, 200)            # reads work
        r = self.owner.post(f"/api/w/{ws}/threads", json={"title": "x"}, headers=ORIGIN)       # writes never do
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "support_read_only")
        # every use is audited against the workspace, where its own administrators see it
        self.assertTrue(self.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'support.access.used' AND workspace_id = ?", (ws,)))
        self.assertTrue(self.engine.db.one("SELECT 1 FROM notifications WHERE workspace_id = ? AND kind = 'security'", (ws,)))
        self.assertEqual(self.owner.delete(f"/api/owner/support-access/{gid}", headers=ORIGIN).status_code, 204)
        self.assertEqual(self.owner.get(f"/api/w/{ws}/documents").status_code, 404)
        # a grant expires on its own
        r = self.owner.post(f"/api/owner/workspaces/{ws}/support-access", json={"reason": "Ticket 4412: follow-up", "hours": 1}, headers=ORIGIN).json()
        self.engine.db.execute("UPDATE support_access SET expires_at = ? WHERE id = ?", (time.time() - 1, r["id"]))
        self.assertEqual(self.owner.get(f"/api/w/{ws}/documents").status_code, 404)


class Accounts(OwnerBase):
    def test_suspend_reactivate_sign_out_and_restrictions(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        ws = alice.post("/api/workspaces", json={"name": "Alice Ltd"}, headers=ORIGIN).json()["id"]
        r = self.owner.post(f"/api/owner/users/{alice.user_id}/suspend", json={"reason": "Payment dispute"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(alice.get("/api/session").json()["user"], None)                  # sessions ended
        r = self.client().post("/api/auth/login", json={"email": "alice@acme.test", "password": PW})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "disabled")
        self.assertEqual(self.owner.post(f"/api/owner/users/{alice.user_id}/reactivate", headers=ORIGIN).status_code, 200)
        alice = self.login("alice@acme.test")
        # restrictions: read-only account and no new workspaces
        r = self.owner.put(f"/api/owner/users/{alice.user_id}/restrictions", json={"read_only": True}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(alice.get(f"/api/w/{ws}").status_code, 200)
        r = alice.post(f"/api/w/{ws}/threads", json={"title": "x"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "account_read_only")
        self.owner.put(f"/api/owner/users/{alice.user_id}/restrictions", json={"read_only": False, "no_new_workspaces": True}, headers=ORIGIN)
        self.assertEqual(alice.post("/api/workspaces", json={"name": "Second"}, headers=ORIGIN).json()["error"], "account_restricted")
        # sign-out everywhere by the platform
        n = self.owner.post(f"/api/owner/users/{alice.user_id}/sign-out", headers=ORIGIN).json()["revoked"]
        self.assertEqual(n, 1)
        self.assertEqual(alice.get(f"/api/w/{ws}").status_code, 401)
        # the owner cannot suspend themself
        self.assertEqual(self.owner.post(f"/api/owner/users/{self.owner_id}/suspend", json={"reason": "oops"}, headers=ORIGIN).status_code, 422)
        # the directory shows it all, without hashes or tokens
        page = self.owner.get("/api/owner/users?q=alice").json()
        self.assertEqual(page["total"], 1)
        self.assertNotIn("password_hash", self.owner.get("/api/owner/users?q=alice").text)
        self.assertEqual(page["items"][0]["restrictions"], {"no_new_workspaces": True})
        detail = self.owner.get(f"/api/owner/users/{alice.user_id}").json()
        self.assertTrue(any(e["action"] == "user.suspend" or e["action"] == "session.revoked" for e in detail["auth_history"]))

    def test_forced_password_reset_gates_everything_but_the_fix(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        ws = alice.post("/api/workspaces", json={"name": "Alice Ltd"}, headers=ORIGIN).json()["id"]
        r = self.owner.post(f"/api/owner/users/{alice.user_id}/require-password-reset", headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["emailed"])
        self.assertIsNone(r.json()["link"])                 # the link went to the person, not to the administrator
        alice = self.login("alice@acme.test")               # the password still works…
        r = alice.get(f"/api/w/{ws}")                       # …but nothing else does until it is changed
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "password_reset_required")
        self.assertTrue(alice.get("/api/session").json()["user"]["must_reset_password"])
        r = alice.post("/api/auth/password", json={"current": PW, "new": "Another-Strong-Pass-7"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(alice.get(f"/api/w/{ws}").status_code, 200)
        self.deliver_mail()
        self.assertTrue(any("new password" in m.get_content() for m in self.mails()))

    def test_maintenance_mode_keeps_administrators_in(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        ws = alice.post("/api/workspaces", json={"name": "Alice Ltd"}, headers=ORIGIN).json()["id"]
        self.owner.put("/api/owner/settings", json={"values": {"platform.maintenance_mode": True, "platform.maintenance_message": "Back at 09:00"}}, headers=ORIGIN)
        r = alice.get(f"/api/w/{ws}")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["message"], "Back at 09:00")
        self.assertTrue(alice.get("/api/session").json()["platform"]["platform.maintenance_mode"])   # the page can explain
        self.assertEqual(self.owner.get("/api/owner/overview").status_code, 200)
        self.owner.put("/api/owner/settings", json={"values": {"platform.maintenance_mode": False}}, headers=ORIGIN)
        self.assertEqual(alice.get(f"/api/w/{ws}").status_code, 200)

    def test_workspace_state_suspend_archive_restore_delete(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        ws = alice.post("/api/workspaces", json={"name": "Alice Ltd"}, headers=ORIGIN).json()["id"]
        self.owner.post(f"/api/owner/workspaces/{ws}/state", json={"status": "read_only", "reason": "dispute"}, headers=ORIGIN)
        self.assertEqual(alice.get(f"/api/w/{ws}").status_code, 200)
        self.assertEqual(alice.post(f"/api/w/{ws}/threads", json={"title": "x"}, headers=ORIGIN).json()["error"], "workspace_read_only")
        self.owner.post(f"/api/owner/workspaces/{ws}/state", json={"status": "suspended", "reason": "dispute"}, headers=ORIGIN)
        self.assertEqual(alice.get(f"/api/w/{ws}").json()["error"], "workspace_suspended")
        # deletion needs archiving first, then removes everything
        self.assertEqual(self.owner.delete(f"/api/owner/workspaces/{ws}", headers=ORIGIN).json()["error"], "archive_first")
        self.owner.post(f"/api/owner/workspaces/{ws}/state", json={"status": "active"}, headers=ORIGIN)
        self.assertEqual(alice.get(f"/api/w/{ws}").status_code, 200)   # restored
        self.owner.post(f"/api/owner/workspaces/{ws}/state", json={"status": "archived"}, headers=ORIGIN)
        self.assertEqual(self.owner.delete(f"/api/owner/workspaces/{ws}", headers=ORIGIN).status_code, 204)
        self.assertIsNone(self.engine.db.one("SELECT 1 FROM workspaces WHERE id = ?", (ws,)))
        self.assertTrue(self.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'workspace.delete' AND target = ?", (ws,)))


class TwoFactor(OwnerBase):
    def enroll(self, c: Any) -> bytes:
        import base64

        r = c.post("/api/auth/mfa/begin", headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        key = r.json()["key"]
        self.assertTrue(r.json()["otpauth"].startswith("otpauth://totp/"))
        secret = base64.b32decode(key + "=" * (-len(key) % 8))
        self.assertEqual(c.post("/api/auth/mfa/confirm", json={"code": "000000"}, headers=ORIGIN).status_code, 422)   # wrong first code: not enabled
        self.assertFalse(c.get("/api/session").json()["user"]["mfa_enabled"])
        self.assertEqual(c.post("/api/auth/mfa/confirm", json={"code": mfa.totp(secret)}, headers=ORIGIN).status_code, 200)
        self.assertTrue(c.get("/api/session").json()["user"]["mfa_enabled"])
        return secret

    def test_enrolment_login_challenge_and_admin_reset(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        secret = self.enroll(alice)
        # the password alone opens no session
        c = self.client()
        r = c.post("/api/auth/login", json={"email": "alice@acme.test", "password": PW})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["mfa_required"])
        self.assertNotIn("token", r.json())
        self.assertIsNone(c.get("/api/session").json()["user"])
        challenge = r.json()["challenge"]
        r = c.post("/api/auth/mfa/verify", json={"challenge": challenge, "code": "123456"})
        self.assertEqual(r.status_code, 401)
        r = c.post("/api/auth/mfa/verify", json={"challenge": challenge, "code": mfa.totp(secret)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get("/api/session").json()["user"]["email"], "alice@acme.test")
        # the challenge was single use
        self.assertEqual(c.post("/api/auth/mfa/verify", json={"challenge": challenge, "code": mfa.totp(secret)}, headers=ORIGIN).status_code, 401)
        # the owner can reset a lost authenticator; the person's sessions end
        self.assertEqual(self.owner.post(f"/api/owner/users/{alice.user_id}/reset-mfa", headers=ORIGIN).status_code, 200)
        self.assertEqual(c.get("/api/session").json()["user"], None)
        self.assertEqual(self.login("alice@acme.test").get("/api/session").json()["user"]["mfa_enabled"], False)

    def test_policy_requires_enrolment_before_anything_else(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        ws = alice.post("/api/workspaces", json={"name": "Alice Ltd"}, headers=ORIGIN).json()["id"]
        set_setting(self.engine, "security.mfa_policy", "all", by=None)
        r = alice.get(f"/api/w/{ws}")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "mfa_enrollment_required")
        self.assertTrue(alice.get("/api/session").json()["user"]["must_enroll_mfa"])
        self.enroll(alice)
        self.assertEqual(alice.get(f"/api/w/{ws}").status_code, 200)
        # and cannot be turned off while the policy requires it
        r = alice.post("/api/auth/mfa/disable", json={"code": "000000"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 403)
        # per-account requirement works the same way
        set_setting(self.engine, "security.mfa_policy", "none", by=None)
        bob = self.invite_user("bob@acme.test", "Bob")
        self.owner.put(f"/api/owner/users/{bob.user_id}/restrictions", json={"mfa_required": True}, headers=ORIGIN)
        self.assertEqual(bob.post("/api/workspaces", json={"name": "Bob Ltd"}, headers=ORIGIN).json()["error"], "mfa_enrollment_required")

    def test_totp_vectors(self) -> None:
        # RFC 6238 appendix B (SHA-1, T=59 → 94287082; the 6-digit code is its last six digits)
        secret = b"12345678901234567890"
        self.assertEqual(mfa.hotp(secret, 59 // 30), "287082")
        self.assertTrue(mfa.verify_code(secret, mfa.totp(secret, 1111111109), at=1111111109))
        self.assertTrue(mfa.verify_code(secret, mfa.totp(secret, 1111111109), at=1111111109 + 29))   # one step of drift
        self.assertFalse(mfa.verify_code(secret, mfa.totp(secret, 1111111109), at=1111111109 + 120))
        self.assertFalse(mfa.verify_code(secret, "12345", at=1111111109))
