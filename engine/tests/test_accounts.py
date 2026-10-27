"""Accounts on the Team Server: e-mail invitations, adding existing users, password reset, sign-in with Google."""

from __future__ import annotations

import re
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
from starlette.testclient import TestClient

from desk_engine.mail import Mailer

from .helpers import Harness

PW = "Ledger-Balance-2026!"
ORIGIN = {"origin": "https://desk.example.com"}


class FakeMailer(Mailer):
    def __init__(self) -> None:
        super().__init__(host="smtp.test", sender="OfinTech Desk <desk@acme.test>")

    def _send_sync(self, msg: object) -> None:  # no network
        return None


class Accounts(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(mode="server")
        self.h.engine.mailer = FakeMailer()
        self.c = self.h.client
        r = self.c.post("/api/auth/signup", json={"email": "owner@acme.test", "name": "Olivia", "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        self.ws = self.c.post("/api/workspaces", json={"name": "Acme"}, headers=ORIGIN).json()["id"]

    def tearDown(self) -> None:
        self.h.close()

    def client(self) -> TestClient:
        return TestClient(self.h.app, base_url="https://desk.example.com")

    def mails(self) -> list:
        return self.h.engine.mailer.sent

    def test_invitation_email_and_direct_add(self) -> None:
        c, ws = self.c, self.ws
        r = c.post(f"/api/w/{ws}/invites", json={"email": "ann@acme.test", "role": "editor"}, headers=ORIGIN).json()
        self.assertTrue(r["emailed"])
        msg = self.mails()[-1]
        self.assertEqual(msg["To"], "ann@acme.test")
        self.assertIn("Olivia invited you to Acme", msg["Subject"])
        self.assertIn(r["link"], msg.get_content())
        # someone who already has an account is added straight away
        other = self.client()
        token = r["link"].rsplit("/", 1)[1]
        other.post("/api/auth/signup", json={"email": "ann@acme.test", "name": "Ann", "password": PW,
                                             "invite_token": token})
        ws2 = c.post("/api/workspaces", json={"name": "Acme Holdings"}, headers=ORIGIN).json()["id"]
        r = c.post(f"/api/w/{ws2}/invites", json={"email": "ann@acme.test", "role": "viewer"}, headers=ORIGIN).json()
        self.assertTrue(r["added"])
        self.assertIn("You were added to Acme Holdings", self.mails()[-1]["Subject"])
        roles = {w["name"]: w["role"] for w in other.get("/api/session").json()["workspaces"]}
        self.assertEqual(roles, {"Acme": "editor", "Acme Holdings": "viewer"})
        self.assertEqual(c.post(f"/api/w/{ws2}/invites", json={"email": "ann@acme.test"}, headers=ORIGIN).status_code, 409)

    def test_password_reset(self) -> None:
        anon = self.client()
        r = anon.post("/api/auth/forgot", json={"email": "nobody@acme.test"})
        self.assertEqual(r.json()["message"], "If an account exists for this e-mail, a reset link is on its way.")
        self.assertEqual(len(self.mails()), 0)  # unknown address: same answer, no mail
        anon.post("/api/auth/forgot", json={"email": "OWNER@acme.test"})
        link = re.search(r"https://\S+/#/reset/(\S+)", self.mails()[-1].get_content())
        assert link
        token = link.group(1)
        self.assertEqual(anon.post("/api/auth/reset", json={"token": token, "password": "short"}).status_code, 422)
        r = anon.post("/api/auth/reset", json={"token": token, "password": "New-Ledger-Pass-2026!"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(self.c.get("/api/session").json()["user"])  # old sessions ended
        anon = self.client()
        self.assertEqual(anon.post("/api/auth/reset", json={"token": token, "password": "Another-Pass-2026!"}).status_code,
                         422)  # single use
        self.assertEqual(anon.post("/api/auth/login", json={"email": "owner@acme.test", "password": PW}).status_code, 401)
        self.assertEqual(anon.post("/api/auth/login", json={"email": "owner@acme.test",
                                                            "password": "New-Ledger-Pass-2026!"}).status_code, 200)
        anon = self.client()
        for _ in range(3):
            anon.post("/api/auth/forgot", json={"email": "owner@acme.test"})
        self.assertEqual(anon.post("/api/auth/forgot", json={"email": "owner@acme.test"}).status_code, 429)

    def test_google_sign_in(self) -> None:
        h, c, ws = self.h, self.c, self.ws
        self.assertFalse(c.get("/api/session").json()["features"]["google_login"])
        c.put("/api/oauth-apps/google", json={"client_id": "g-id", "client_secret": "g-secret"}, headers=ORIGIN)
        self.assertTrue(c.get("/api/session").json()["features"]["google_login"])
        profiles = {"ok-code": {"sub": "g-111", "email": "Bob@Acme.test", "email_verified": True, "name": "Bob"},
                    "stranger": {"sub": "g-222", "email": "eve@evil.test", "email_verified": True, "name": "Eve"},
                    "unverified": {"sub": "g-333", "email": "bob@acme.test", "email_verified": False}}
        exchanged: list[dict] = []

        def token(req: httpx.Request) -> httpx.Response:
            body = parse_qs(req.content.decode())
            exchanged.append(body)
            return httpx.Response(200, json={"access_token": "at-" + body["code"][0], "expires_in": 3600})

        h.router.add("POST", "https://oauth2.googleapis.com/token", token)
        h.router.add("GET", "https://openidconnect.googleapis.com/v1/userinfo",
                     lambda req: httpx.Response(200, json=profiles[req.headers["authorization"].split("at-")[1]]))

        def sign_in(code: str, invite: str | None = None) -> httpx.Response:
            browser = self.client()
            url = browser.post("/api/auth/google/start", json={"invite_token": invite} if invite else {}).json()["url"]
            q = parse_qs(urlsplit(url).query)
            self.assertEqual(q["scope"], ["openid email profile"])
            self.assertEqual(q["code_challenge_method"], ["S256"])
            self.assertEqual(q["redirect_uri"], ["https://desk.example.com/oauth/callback"])
            r = browser.get("/oauth/callback", params={"code": code, "state": q["state"][0]}, follow_redirects=False)
            r.browser = browser  # type: ignore[attr-defined]
            return r

        r = sign_in("stranger")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Ask a workspace administrator to invite you", r.text)
        self.assertIn("code_verifier", exchanged[-1])
        self.assertEqual(sign_in("unverified").status_code, 400)
        c.post(f"/api/w/{ws}/invites", json={"email": "bob@acme.test", "role": "editor"}, headers=ORIGIN)
        r = sign_in("ok-code")
        self.assertEqual(r.status_code, 303)
        self.assertIn("samesite=strict", r.headers["set-cookie"].lower())
        s = r.browser.get("/api/session").json()  # type: ignore[attr-defined]
        self.assertEqual(s["user"]["email"], "bob@acme.test")
        self.assertEqual(s["workspaces"][0]["role"], "editor")  # the pending invitation was accepted
        self.assertIsNone(h.engine.db.scalar("SELECT password_hash FROM users WHERE email = 'bob@acme.test'"))
        # the same Google account signs in again; a replayed state is refused
        self.assertEqual(sign_in("ok-code").status_code, 303)
        self.assertEqual(h.engine.db.scalar("SELECT COUNT(*) FROM users WHERE google_sub = 'g-111'"), 1)
        browser = self.client()
        r = browser.get("/oauth/callback", params={"code": "ok-code", "state": "made-up"}, follow_redirects=False)
        self.assertNotEqual(r.status_code, 303)
        # limited to the organisation's domains: even an existing account from another domain is refused
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"DESK_GOOGLE_ALLOWED_DOMAINS": "other.test, example.sa"}):
            r = sign_in("ok-code")
            self.assertEqual(r.status_code, 400)
            self.assertIn("limited to the organisation", r.text)
        with mock.patch.dict(os.environ, {"DESK_GOOGLE_ALLOWED_DOMAINS": "acme.test"}):
            self.assertEqual(sign_in("ok-code").status_code, 303)
        # the desktop app: Google runs in the system browser, which hands the sign-in back to the app
        import base64
        import hashlib

        verifier = "v" * 50
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        system_browser = self.client()
        url = system_browser.post("/api/auth/google/start", json={"desktop_challenge": challenge}).json()["url"]
        state = parse_qs(urlsplit(url).query)["state"][0]
        r = system_browser.get("/oauth/callback", params={"code": "ok-code", "state": state}, follow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("set-cookie", r.headers)  # no session in the browser
        code = re.search(r"ofintech-desk://open/auth\?handoff=([A-Za-z0-9_-]+)", r.text).group(1)
        app_window = self.client()
        bad = app_window.get("/api/auth/desktop-handoff", params={"code": code, "verifier": "w" * 50},
                             follow_redirects=False)
        self.assertEqual(bad.status_code, 401)  # a link not started by this app is refused
        ok = app_window.get("/api/auth/desktop-handoff", params={"code": code, "verifier": verifier},
                            follow_redirects=False)
        self.assertEqual(ok.status_code, 303)
        self.assertEqual(app_window.get("/api/session").json()["user"]["email"], "bob@acme.test")
        again = self.client().get("/api/auth/desktop-handoff", params={"code": code, "verifier": verifier},
                                  follow_redirects=False)
        self.assertEqual(again.status_code, 401)  # single use
        self.assertEqual(self.client().post("/api/auth/google/start", json={"desktop_challenge": "short"}).status_code,
                         422)


if __name__ == "__main__":
    unittest.main()
