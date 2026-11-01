"""Shared fixture for the Owner Control Center tests: a hosted Team Server with a provisioned platform owner, a
delegated administrator, ordinary accounts, a fake mailer and a mock Stripe reached through the real client."""

from __future__ import annotations

import socket
import unittest
from typing import Any
from unittest import mock

from starlette.testclient import TestClient

from desk_engine.db import now
from desk_engine.mail import Mailer
from desk_engine.platform.settings import set_setting

from .helpers import Harness
from .stripe_mock import SECRET_KEY, WEBHOOK_SECRET, MockStripe

PW = "Correct-Horse-Battery-9"
ORIGIN = {"origin": "https://desk.example.com"}
OWNER = "owner@platform.test"


class FakeMailer(Mailer):
    def __init__(self) -> None:
        super().__init__(host="smtp.test", sender="OfinTech Desk <desk@platform.test>")
        self.fail = False

    def _send_sync(self, msg: Any) -> None:
        if self.fail:
            raise ConnectionError("smtp down")


class OwnerBase(unittest.TestCase):
    hosted = True

    def setUp(self) -> None:
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        for target in ("desk_engine.security.socket.getaddrinfo", "desk_engine.egress.socket.getaddrinfo"):
            patcher = mock.patch(target, return_value=public)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.h = Harness("server", script=[], hosted=self.hosted, platform_owners=(OWNER,))
        self.engine = self.h.engine
        self.engine.mailer = FakeMailer()
        self.stripe = MockStripe(self.h.router)
        self.owner = self.h.client   # the first account on the server; also listed in DESK_PLATFORM_OWNERS
        r = self.owner.post("/api/auth/signup", json={"email": OWNER, "name": "Olivia Owner", "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        self.owner_id = r.json()["user"]["id"]

    def tearDown(self) -> None:
        self.h.close()

    # --- helpers ----------------------------------------------------------------------------------------------
    def client(self) -> TestClient:
        c = TestClient(self.h.app, base_url="https://desk.example.com")
        c.portal = self.h.client.portal   # every client runs on the engine's own loop
        return c

    def invite_user(self, email: str, name: str = "Person", *, inviter: TestClient | None = None, ws: str | None = None) -> TestClient:
        """Sign-up is by invitation on this server: the owner invites into a scratch workspace, the person leaves it."""
        inviter = inviter or self.owner
        if ws is None:
            ws = inviter.post("/api/workspaces", json={"name": f"Scratch for {email}"}, headers=ORIGIN).json()["id"]
        inv = inviter.post(f"/api/w/{ws}/invites", json={"email": email, "role": "viewer"}, headers=ORIGIN).json()
        c = self.client()
        token = inv["link"].rsplit("/", 1)[1]
        r = c.post("/api/auth/signup", json={"email": email, "name": name, "password": PW, "invite_token": token})
        self.assertEqual(r.status_code, 200, r.text)
        uid = r.json()["user"]["id"]
        self.engine.db.execute("DELETE FROM members WHERE workspace_id = ? AND user_id = ?", (ws, uid))
        c.user_id = uid  # type: ignore[attr-defined]
        return c

    def open_signup(self) -> None:
        set_setting(self.engine, "registration.enabled", True, by=None)

    def signup(self, email: str, name: str = "Person") -> TestClient:
        self.open_signup()
        c = self.client()
        r = c.post("/api/auth/signup", json={"email": email, "name": name, "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        c.user_id = r.json()["user"]["id"]  # type: ignore[attr-defined]
        return c

    def login(self, email: str) -> TestClient:
        c = self.client()
        r = c.post("/api/auth/login", json={"email": email, "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        c.user_id = self.user_id(email)  # type: ignore[attr-defined]
        return c

    def user_id(self, email: str) -> str:
        return self.engine.db.scalar("SELECT id FROM users WHERE email = ?", (email,))

    def stale_reauth(self, c: TestClient) -> None:
        """Puts the session's step-up proof outside the window."""
        from desk_engine.security import token_hash

        token = c.cookies.get("desk_session")
        self.engine.db.execute("UPDATE sessions SET reauth_at = ? WHERE id = ?", (now() - 3600, token_hash(token)))

    def configure_stripe(self) -> None:
        r = self.owner.put("/api/owner/billing", json={"enabled": True, "mode": "test", "publishable_key": "pk_test_mock",
                                                       "secret_key": SECRET_KEY, "webhook_secret": WEBHOOK_SECRET}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)

    def webhook(self, kind: str, obj: dict[str, Any], **kw: Any) -> Any:
        body, sig = self.stripe.event(kind, obj, **kw)
        return self.client().post("/api/billing/webhook", content=body, headers={"stripe-signature": sig, "content-type": "application/json"})

    def plan_id(self, code: str) -> str:
        return self.engine.db.scalar("SELECT id FROM plans WHERE code = ?", (code,))

    def mails(self) -> list[Any]:
        return list(self.engine.mailer.sent)

    def deliver_mail(self) -> int:
        from desk_engine.platform.email import deliver_pending

        return self.h.client.portal.call(deliver_pending, self.engine)
