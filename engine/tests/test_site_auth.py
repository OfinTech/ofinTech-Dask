"""Sign-in and sign-up from the public website (DESK_SITE_URL): plain HTML forms post to the Team Server, which sets
the session cookie and opens the app, or sends the person back to the website with a short error code."""

from __future__ import annotations

import unittest

from .helpers import Harness

PW = "Str0ngPassw0rd!x"
SITE = "https://ofintech.example"
APP = "https://desk.example.com"


class SiteForms(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(mode="server", site_url=SITE)
        self.c = self.h.client

    def tearDown(self) -> None:
        self.h.close()

    def post(self, path: str, data: dict[str, str], origin: str | None = SITE):
        headers = {"origin": origin} if origin else {}
        return self.c.post(path, data=data, headers=headers, follow_redirects=False)

    def test_sign_up_then_sign_in_from_the_website(self) -> None:
        h, c = self.h, self.c
        self.assertEqual(c.get("/api/session").json()["site_url"], SITE)
        # a form from another site is refused; a bad address goes back with a code
        self.assertEqual(self.post("/auth/form/sign-up", {"email": "a@b.co", "name": "A", "password": PW}, origin="https://evil.example").status_code, 403)
        r = self.post("/auth/form/sign-up", {"email": "not-an-email", "name": "A", "password": PW})
        self.assertEqual((r.status_code, r.headers["location"]), (303, f"{SITE}/#signup-invalid"))
        r = self.post("/auth/form/sign-up", {"email": "olivia@acme.test", "name": "Olivia", "password": "short"})
        self.assertEqual(r.headers["location"], f"{SITE}/#signup-password")
        # the first account is created (it administers the server) and lands in the app, signed in
        r = self.post("/auth/form/sign-up", {"email": "olivia@acme.test", "name": "Olivia", "password": PW})
        self.assertEqual((r.status_code, r.headers["location"]), (303, f"{APP}/#/"))
        cookie = r.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Secure", cookie)
        self.assertEqual(c.get("/api/session").json()["user"]["email"], "olivia@acme.test")
        self.assertTrue(c.get("/api/session").json()["user"]["instance_admin"])
        # sign-up is by invitation after that: the website says so
        other = c.__class__(h.app, base_url=APP)
        r = other.post("/auth/form/sign-up", data={"email": "eve@x.test", "name": "Eve", "password": PW},
                       headers={"origin": SITE}, follow_redirects=False)
        self.assertEqual(r.headers["location"], f"{SITE}/#signup-invitation")
        self.assertIsNone(other.cookies.get("desk_session"))
        # an existing e-mail on an invitation-only server gets the same answer (it does not reveal which e-mails exist)
        r = other.post("/auth/form/sign-up", data={"email": "olivia@acme.test", "name": "O", "password": PW},
                       headers={"origin": SITE}, follow_redirects=False)
        self.assertEqual(r.headers["location"], f"{SITE}/#signup-invitation")
        # sign-in: wrong password goes back, the right one opens the app
        r = other.post("/auth/form/sign-in", data={"email": "olivia@acme.test", "password": "wrong"},
                       headers={"origin": SITE}, follow_redirects=False)
        self.assertEqual(r.headers["location"], f"{SITE}/#signin-error")
        r = other.post("/auth/form/sign-in", data={"email": "Olivia@acme.test", "password": PW},
                       headers={"origin": SITE}, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, f"{APP}/#/"))
        self.assertEqual(other.get("/api/session").json()["user"]["name"], "Olivia")
        # the app's own origin may post too (the referer is accepted when there is no origin header)
        third = c.__class__(h.app, base_url=APP)
        r = third.post("/auth/form/sign-in", data={"email": "olivia@acme.test", "password": PW},
                       headers={"referer": f"{APP}/#/login"}, follow_redirects=False)
        self.assertEqual(r.headers["location"], f"{APP}/#/")
        # too many wrong passwords
        for _ in range(9):
            r = other.post("/auth/form/sign-in", data={"email": "olivia@acme.test", "password": "wrong"},
                           headers={"origin": SITE}, follow_redirects=False)
        self.assertEqual(r.headers["location"], f"{SITE}/#signin-rate")
        # Google is not set up on this server
        r = c.get("/auth/form/google", follow_redirects=False)
        self.assertEqual(r.headers["location"], f"{SITE}/#google-off")

    def test_open_sign_up(self) -> None:
        srv = Harness(mode="server", site_url=SITE, open_signup=True)
        try:
            c = srv.client
            r = c.post("/auth/form/sign-up", data={"email": "first@acme.test", "name": "First", "password": PW},
                       headers={"origin": SITE}, follow_redirects=False)
            self.assertEqual(r.headers["location"], f"{APP}/#/")
            other = c.__class__(srv.app, base_url=APP)
            r = other.post("/auth/form/sign-up", data={"email": "second@acme.test", "name": "Second", "password": PW},
                           headers={"origin": SITE}, follow_redirects=False)
            self.assertEqual(r.headers["location"], f"{APP}/#/")
            self.assertFalse(other.get("/api/session").json()["user"]["instance_admin"])
            third = c.__class__(srv.app, base_url=APP)
            r = third.post("/auth/form/sign-up", data={"email": "second@acme.test", "name": "Again", "password": PW},
                           headers={"origin": SITE}, follow_redirects=False)
            self.assertEqual(r.headers["location"], f"{SITE}/#signup-exists")
        finally:
            srv.close()

    def test_without_a_website_failures_go_to_the_app(self) -> None:
        plain = Harness(mode="server")
        try:
            r = plain.client.post("/auth/form/sign-in", data={"email": "x@y.co", "password": "nope"},
                                  headers={"origin": APP}, follow_redirects=False)
            self.assertEqual(r.headers["location"], f"{APP}/#/login?error=signin-error")
            self.assertEqual(plain.client.get("/api/session").json()["site_url"], "")
        finally:
            plain.close()

    def test_desktop_has_no_forms(self) -> None:
        desk = Harness()
        try:
            r = desk.client.post("/auth/form/sign-in", data={"email": "x@y.co", "password": "x"},
                                 headers={"origin": "http://localhost:47821"}, follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            self.assertTrue(r.headers["location"].endswith("#/login?error=desktop"))
        finally:
            desk.close()


if __name__ == "__main__":
    unittest.main()
