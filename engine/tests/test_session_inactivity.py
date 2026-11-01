"""Automatic sign-out after inactivity, on a Team Server, with a clock the test moves instead of waiting.

The engine's `now()` (as seen by the session code) is replaced by a controllable clock; `last_seen` and the
session's end are compared against it. The UI's half of the policy (the 2-minute warning, "Stay signed in", tabs,
sleep and reconnection) is a function of these same numbers and is tested in ui/test/idle.test.mjs."""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from starlette.testclient import TestClient

from .helpers import Harness

PW = "Correct-Horse-9"
ORIGIN = {"origin": "https://desk.example.com"}
MIN = 60


class Clock:
    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Sessions(unittest.TestCase):
    idle_minutes = 30

    def setUp(self) -> None:
        self.clock = Clock()
        for target in ("desk_engine.web.now", "desk_engine.routes.auth.now"):
            patcher = mock.patch(target, self.clock)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.h = Harness("server", session_idle_minutes=self.idle_minutes)
        self.c = self.h.client
        self.assertEqual(self.c.post("/api/auth/signup", json={"email": "owner@acme.test", "name": "O", "password": PW}).status_code, 200)
        self.uid = self.c.get("/api/session").json()["user"]["id"]
        self.ws = self.c.post("/api/workspaces", json={"name": "Acme", "base_currency": "USD"}, headers=ORIGIN).json()["id"]
        up = self.c.post(f"/api/w/{self.ws}/imports", files={"file": ("gl.csv", b"account,amount\n4000,10.5\n")}, headers=ORIGIN).json()
        self.ds = self.c.post(f"/api/w/{self.ws}/imports/{up['upload_id']}/commit", json={"name": "GL", "sheet": None, "header_row": 0}, headers=ORIGIN).json()["id"]

    def tearDown(self) -> None:
        self.h.close()

    def login(self, client: TestClient | None = None) -> str:
        c = client or self.c
        r = c.post("/api/auth/login", json={"email": "owner@acme.test", "password": PW}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["token"]

    def status(self, client: TestClient | None = None) -> dict:
        return (client or self.c).get("/api/session/status").json()

    def protected(self, client: TestClient | None = None) -> int:
        return (client or self.c).get(f"/api/w/{self.ws}/datasets/{self.ds}").status_code

    def audit(self, action: str) -> list[dict]:
        return self.h.engine.db.all("SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,))

    # -- the policy ----------------------------------------------------------------------------------------------
    def test_defaults_and_configuration(self) -> None:
        s = self.c.get("/api/session").json()
        self.assertEqual((s["idle_minutes"], s["warn_seconds"], s["lock_minutes"]), (self.idle_minutes, 120, 0))
        self.assertEqual(self.h.settings.session_hours, 12)
        st = self.status()
        self.assertLessEqual(st["idle_seconds_left"], self.idle_minutes * MIN)
        self.assertGreater(st["idle_seconds_left"], self.idle_minutes * MIN - 10)
        self.assertLessEqual(st["lifetime_seconds_left"], 8 * 3600)          # the owner of a workspace: 8 hours

    def test_an_active_person_stays_signed_in(self) -> None:
        """Three idle windows pass, with a real action every ten minutes: never signed out."""
        for _ in range(9):
            self.clock.advance(10 * MIN)
            self.assertEqual(self.c.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).status_code, 200)  # a POST: the person did something
        self.clock.advance(self.idle_minutes * MIN - 60)
        self.assertEqual(self.protected(), 200)

    def test_inactivity_signs_out_and_nothing_revives_the_session(self) -> None:
        token = self.login()
        self.clock.advance(self.idle_minutes * MIN - 5)
        self.assertEqual(self.protected(), 200)                              # 29:55 — still in
        self.assertEqual(self.status()["idle_seconds_left"], 5)
        self.clock.advance(6)
        r = self.c.get(f"/api/w/{self.ws}/datasets/{self.ds}")              # 30:01 — out
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"], "session_idle")
        self.assertNotIn("GL", r.text)
        # the session row is gone, so the old cookie, the old bearer token and a ping can do nothing
        from desk_engine.security import token_hash
        self.assertIsNone(self.h.engine.db.one("SELECT 1 FROM sessions WHERE id = ?", (token_hash(token),)))
        self.assertEqual(self.c.post("/api/session/ping", headers=ORIGIN).status_code, 401)
        other = TestClient(self.h.app, base_url="https://desk.example.com", headers={"authorization": f"Bearer {token}"})
        other.portal = self.h.client.portal
        self.assertEqual(other.get("/api/session").json()["user"], None)
        self.assertEqual(self.protected(other), 401)
        # protected data, AI tools and the event stream are all closed
        self.assertEqual(self.c.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).status_code, 401)
        self.assertEqual(self.c.get(f"/api/w/{self.ws}/tools").status_code, 401)
        self.assertEqual(self.c.post(f"/api/w/{self.ws}/query", json={"dataset": self.ds, "measures": [{"column": "amount", "agg": "sum"}]}, headers=ORIGIN).status_code, 401)
        # the sign-out is on the audit log, attributed to the person, with the reason
        expired = self.audit("session.expired")
        self.assertEqual(expired[-1]["user_id"], self.uid)
        self.assertEqual(json.loads(expired[-1]["detail"]), {"reason": "idle", "idle_minutes": self.idle_minutes})
        self.assertEqual(expired[-1]["decision"], "denied")
        # signing in again issues a new session with a new token (no fixation, no reuse)
        new = self.login()
        self.assertNotEqual(new, token)
        self.assertEqual(self.protected(), 200)

    def test_background_traffic_does_not_extend_the_session(self) -> None:
        """Polling, the status check, the notification refresh, the event stream and a workspace page reload are all
        GETs: none of them moves last_seen, so a tab left open still reaches its sign-out."""
        t = self.c.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).json()
        self.clock.advance(self.idle_minutes * MIN - 2 * MIN)
        for _ in range(3):
            self.assertEqual(self.c.get(f"/api/w/{self.ws}/notifications").status_code, 200)   # the page's 20-second poll
            self.assertEqual(self.c.get("/api/session/status").status_code, 200)               # the status check itself
            self.assertEqual(self.c.get(f"/api/w/{self.ws}/threads/{t['id']}").status_code, 200)   # a chat page refreshing
            self.assertEqual(self.c.get("/api/session").status_code, 200)
        self.assertEqual(self.status()["idle_seconds_left"], 2 * MIN)       # unchanged by all of that
        self.clock.advance(2 * MIN + 1)
        self.assertEqual(self.protected(), 401)

    def test_ai_streaming_does_not_extend_the_session(self) -> None:
        """An open event stream (the assistant's answer arriving) is a GET; the engine re-authorises it on every
        keep-alive and closes it when the session ends — it never keeps the session alive. Driven as a raw ASGI
        request because the test client buffers streams."""
        import asyncio

        from desk_engine.routes import threads as threads_mod

        token = self.login()
        t = self.c.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).json()
        self.clock.advance(self.idle_minutes * MIN - 30)
        sent: list[bytes] = []
        clock = self.clock

        async def run() -> None:
            scope = {"type": "http", "method": "GET", "scheme": "https", "server": ("desk.example.com", 443),
                     "path": f"/api/w/{self.ws}/threads/{t['id']}/events", "raw_path": b"/", "query_string": b"",
                     "headers": [(b"host", b"desk.example.com"), (b"authorization", f"Bearer {token}".encode())],
                     "app": self.h.app, "client": ("127.0.0.1", 1)}

            async def receive():
                await asyncio.sleep(3600)

            async def send(message):
                if message["type"] == "http.response.body":
                    sent.append(message.get("body", b""))
                    if len(sent) == 3:
                        clock.advance(31)        # the session's idle window ends while the stream is open

            with mock.patch.object(threads_mod, "KEEP_ALIVE_SECONDS", 0.05):
                await asyncio.wait_for(self.h.app(scope, receive, send), 5)

        asyncio.run(run())
        self.assertTrue(b"".join(sent).startswith(b": connected"))
        self.assertEqual(self.protected(), 401)                              # the stream kept nothing alive
        from desk_engine.security import token_hash
        self.assertIsNone(self.h.engine.db.one("SELECT 1 FROM sessions WHERE id = ?", (token_hash(token),)))

    def test_stay_signed_in_refreshes_only_a_live_session(self) -> None:
        self.clock.advance(self.idle_minutes * MIN - 100)                   # inside the 2-minute warning
        r = self.c.post("/api/session/ping", headers=ORIGIN)                 # "Stay signed in"
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["idle_seconds_left"], self.idle_minutes * MIN)
        self.clock.advance(self.idle_minutes * MIN + 1)                      # … and then nothing, for the whole window
        self.assertEqual(self.c.post("/api/session/ping", headers=ORIGIN).status_code, 401)   # too late: not revived
        self.assertEqual(self.protected(), 401)

    def test_client_cannot_extend_the_session_by_lying(self) -> None:
        """Nothing the page sends carries a timestamp: last_seen is set by the engine from its own clock, so a forged
        'last activity' header or body is ignored, and a ping from a dead session is refused."""
        self.clock.advance(self.idle_minutes * MIN + 1)
        r = self.c.post("/api/session/ping", json={"last_activity": self.clock.t + 999999}, headers={**ORIGIN, "x-last-activity": "9999999999"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.protected(), 401)

    def test_concurrent_sessions_and_logout_are_independent(self) -> None:
        """Two devices: one goes idle and is signed out, the other keeps working; logging one out leaves the other."""
        phone = TestClient(self.h.app, base_url="https://desk.example.com")
        phone.portal = self.h.client.portal
        self.login(phone)
        self.assertEqual(self.h.engine.db.scalar("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (self.uid,)), 2)
        for _ in range(5):                                                  # the phone stays active, the laptop does not
            self.clock.advance(10 * MIN)
            self.assertEqual(phone.post(f"/api/w/{self.ws}/threads", json={}, headers=ORIGIN).status_code, 200)
        self.assertEqual(self.protected(), 401)                              # laptop: idle for 50 minutes
        self.assertEqual(self.protected(phone), 200)                         # phone: fine
        # a fresh laptop session, then the phone logs out: the laptop is untouched
        self.login()
        self.assertEqual(phone.post("/api/auth/logout", headers=ORIGIN).status_code, 200)
        self.assertEqual(self.protected(phone), 401)
        self.assertEqual(self.protected(), 200)
        logout = self.audit("session.logout")[-1]
        self.assertEqual((logout["user_id"], json.loads(logout["detail"])["reason"]), (self.uid, "user"))
        # a logout the page attributes to idling is recorded with that reason
        self.assertEqual(self.c.post("/api/auth/logout?reason=idle", headers=ORIGIN).status_code, 200)
        self.assertEqual(json.loads(self.audit("session.logout")[-1]["detail"])["reason"], "idle")

    def test_absolute_lifetime_ends_an_active_session(self) -> None:
        """Beside the idle window there is an absolute end (12 hours by default, 8 for admins and owners; 1 hour
        here so the test stays short): a person active the whole time is still signed out when it comes."""
        h = Harness("server", session_idle_minutes=self.idle_minutes, session_hours=1)
        try:
            c = h.client
            self.assertEqual(c.post("/api/auth/signup", json={"email": "o2@acme.test", "name": "O", "password": PW}).status_code, 200)
            uid = c.get("/api/session").json()["user"]["id"]
            step = (self.idle_minutes - 1) * MIN
            elapsed = 0
            while elapsed + step < 3600:
                self.clock.advance(step)
                elapsed += step
                self.assertEqual(c.post("/api/workspaces", json={"name": f"W{elapsed}", "base_currency": "USD"}, headers=ORIGIN).status_code, 200)
            self.clock.advance(3600 - elapsed + 1)                           # one hour and one second
            self.assertIsNone(c.get("/api/session").json()["user"])
            expired = h.engine.db.all("SELECT * FROM audit_log WHERE action = 'session.expired' AND user_id = ?", (uid,))
            self.assertEqual(json.loads(expired[-1]["detail"])["reason"], "lifetime")
        finally:
            h.close()


class FortyFiveMinutes(Sessions):
    """The longest window an operator may choose."""
    idle_minutes = 45


if __name__ == "__main__":
    unittest.main()
