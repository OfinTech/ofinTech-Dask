import io
import unittest
import zipfile

from starlette.testclient import TestClient

from desk_engine.errors import Invalid
from desk_engine.security import SecretBox, check_outbound_url
from tests.helpers import Harness

PW = "Correct-Horse-9-battery"


class TeamServer(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(mode="server")
        self.c = self.h.client
        self.origin = {"origin": "https://desk.example.com"}

    def tearDown(self) -> None:
        self.h.close()

    def other_client(self) -> TestClient:
        c = TestClient(self.h.app, base_url="https://desk.example.com")
        return c

    def test_first_user_invite_roles_and_isolation(self) -> None:
        h, c = self.h, self.c
        s = c.get("/api/session").json()
        self.assertTrue(s["needs_first_user"])
        r = c.post("/api/auth/signup", json={"email": "owner@acme.test", "name": "Olivia", "password": "short"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"], "weak_password")
        r = c.post("/api/auth/signup", json={"email": "owner@acme.test", "name": "Olivia", "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("httponly", r.headers["set-cookie"].lower())
        self.assertIn("samesite=strict", r.headers["set-cookie"].lower())
        self.assertTrue(c.get("/api/session").json()["user"]["instance_admin"])
        # cookie-authenticated writes need the app's origin (CSRF guard)
        r = c.post("/api/workspaces", json={"name": "Acme"})
        self.assertEqual(r.json()["error"], "bad_origin")
        ws = c.post("/api/workspaces", json={"name": "Acme", "base_currency": "USD"}, headers=self.origin).json()
        # second sign-up without an invitation is refused
        other = self.other_client()
        r = other.post("/api/auth/signup", json={"email": "eve@x.test", "name": "Eve", "password": PW})
        self.assertEqual(r.status_code, 403)
        # invite an accountant as viewer
        inv = c.post(f"/api/w/{ws['id']}/invites", json={"email": "ann@acme.test", "role": "viewer"},
                     headers=self.origin).json()
        token = inv["link"].rsplit("/", 1)[1]
        self.assertEqual(other.get(f"/api/invites/{token}").json()["workspace"], "Acme")
        r = other.post("/api/auth/signup", json={"email": "someone@else.test", "name": "Ann", "password": PW,
                                                 "invite_token": token})
        self.assertEqual(r.json()["error"], "invite_email")
        r = other.post("/api/auth/signup", json={"email": "ann@acme.test", "name": "Ann", "password": PW,
                                                 "invite_token": token})
        self.assertEqual(r.status_code, 200, r.text)
        ann_token = r.json()["token"]
        ann = TestClient(h.app, base_url="https://desk.example.com", headers={"authorization": f"Bearer {ann_token}"})
        self.assertEqual(ann.get("/api/session").json()["workspaces"][0]["role"], "viewer")
        # viewer cannot change settings or add providers; can read
        self.assertEqual(ann.patch(f"/api/w/{ws['id']}", json={"name": "x"}).status_code, 403)
        self.assertEqual(ann.post(f"/api/w/{ws['id']}/providers", json={"kind": "openai", "api_key": "k"}).status_code, 403)
        self.assertEqual(ann.get(f"/api/w/{ws['id']}/members").status_code, 200)
        # the invitation is single-use
        self.assertEqual(other.get(f"/api/invites/{token}").status_code, 404)
        # Ann's own workspace is invisible to Olivia, and the reverse for private threads
        ann_ws = ann.post("/api/workspaces", json={"name": "Ann Books"}).json()
        self.assertEqual(c.get(f"/api/w/{ann_ws['id']}/threads").status_code, 404)
        t = c.post(f"/api/w/{ws['id']}/threads", json={"visibility": "private"}, headers=self.origin).json()
        self.assertEqual(ann.get(f"/api/w/{ws['id']}/threads/{t['id']}").status_code, 404)
        self.assertNotIn(t["id"], [x["id"] for x in ann.get(f"/api/w/{ws['id']}/threads").json()])
        # promote, then only owners can create owners; the last owner cannot be demoted
        c.patch(f"/api/w/{ws['id']}/members/{ann.get('/api/session').json()['user']['id']}", json={"role": "admin"},
                headers=self.origin)
        me = c.get("/api/session").json()["user"]["id"]
        r = ann.patch(f"/api/w/{ws['id']}/members/{me}", json={"role": "viewer"})
        self.assertEqual(r.status_code, 403)
        r = c.patch(f"/api/w/{ws['id']}/members/{me}", json={"role": "admin"}, headers=self.origin)
        self.assertEqual(r.status_code, 409)
        # logout revokes the session
        ann.post("/api/auth/logout")
        self.assertEqual(ann.get(f"/api/w/{ws['id']}/members").status_code, 401)

    def test_login_rate_limit_and_wrong_password(self) -> None:
        c = self.c
        c.post("/api/auth/signup", json={"email": "o@a.test", "name": "O", "password": PW})
        fresh = self.other_client()
        codes = [fresh.post("/api/auth/login", json={"email": "o@a.test", "password": "Wrong-password-1"}).status_code
                 for _ in range(9)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8], 429)

    def test_server_refuses_private_urls_and_stdio(self) -> None:
        c = self.c
        c.post("/api/auth/signup", json={"email": "o@a.test", "name": "O", "password": PW})
        ws = c.post("/api/workspaces", json={"name": "W"}, headers=self.origin).json()["id"]
        r = c.post(f"/api/w/{ws}/providers", json={"kind": "openai_compatible", "base_url": "http://127.0.0.1:11434/v1"},
                   headers=self.origin)
        self.assertEqual(r.json()["error"], "bad_url")
        r = c.post(f"/api/w/{ws}/providers", json={"kind": "ollama"}, headers=self.origin)
        self.assertEqual(r.status_code, 422)
        r = c.post(f"/api/w/{ws}/mcp-servers", json={"name": "local", "transport": "stdio", "command": "npx"},
                   headers=self.origin)
        self.assertEqual(r.status_code, 403)
        r = c.post(f"/api/w/{ws}/mcp-servers", json={"name": "meta", "transport": "http",
                                                      "url": "https://169.254.169.254/latest"}, headers=self.origin)
        self.assertEqual(r.json()["error"], "bad_url")


class Skills(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace(with_model=False)

    def tearDown(self) -> None:
        self.h.close()

    def test_builtin_custom_import_export(self) -> None:
        h, ws = self.h, self.ws
        skills = h.api("GET", f"/api/w/{ws}/skills")
        self.assertGreaterEqual(len([s for s in skills if s["source"] == "builtin"]), 15)
        h.api("PUT", f"/api/w/{ws}/skills/builtin:fx-revaluation/enabled", json={"enabled": False})
        self.assertFalse(next(s for s in h.api("GET", f"/api/w/{ws}/skills") if s["name"] == "fx-revaluation")["enabled"])
        md = ("---\nname: expense-policy\ndescription: Our travel and expense policy limits; use for any expense "
              "question.\n---\nHotels up to 180 per night.\n")
        created = h.api("POST", f"/api/w/{ws}/skills", json={"markdown": md})
        self.assertEqual(created["name"], "expense-policy")
        h.api("POST", f"/api/w/{ws}/skills", json={"markdown": md}, expect=409)
        r = h.api("POST", f"/api/w/{ws}/skills", json={"markdown": "no front matter"}, expect=422)
        self.assertEqual(r["error"], "bad_skill")
        # archive: SKILL.md + reference file + a script that must be ignored
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("lease-ifrs16/SKILL.md", "---\nname: lease-ifrs16\ndescription: Lease accounting under IFRS 16 "
                                                "for lessees.\n---\nUse the reference table.\n")
            z.writestr("lease-ifrs16/reference.md", "| term | rate |")
            z.writestr("lease-ifrs16/scripts/calc.py", "print('x')")
        res = h.api("POST", f"/api/w/{ws}/skills/import", files={"file": ("lease.zip", buf.getvalue())})
        self.assertEqual(res["ignored_files"], ["scripts/calc.py"])
        full = h.api("GET", f"/api/w/{ws}/skills/lease-ifrs16")
        self.assertEqual(list(full["files"]), ["reference.md"])
        exported = h.client.get(f"/api/w/{ws}/skills/lease-ifrs16/export").text
        self.assertTrue(exported.startswith("---\nname: lease-ifrs16"))
        # path traversal in archives is refused
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("SKILL.md", "---\nname: evil-skill\ndescription: trying to escape the folder.\n---\nx\n")
            z.writestr("../../etc/passwd.md", "x")
        r = h.api("POST", f"/api/w/{ws}/skills/import", files={"file": ("e.zip", buf.getvalue())}, expect=422)
        self.assertIn("unsafe path", r["message"])
        h.api("PUT", "/api/w/%s/skills/builtin:month-end-close" % ws, json={"markdown": md}, expect=403)


class SecurityUnits(unittest.TestCase):
    def test_secret_box_binds_context(self) -> None:
        import os

        box = SecretBox(os.urandom(32))
        blob = box.seal(b"key", b"a|llm|ws1")
        self.assertEqual(box.open(blob, b"a|llm|ws1"), b"key")
        with self.assertRaises(Exception):
            box.open(blob, b"a|llm|ws2")

    def test_outbound_url_policy(self) -> None:
        with self.assertRaises(Invalid):
            check_outbound_url("http://example.com", allow_private=False)
        with self.assertRaises(Invalid):
            check_outbound_url("https://user:pw@example.com", allow_private=False)
        with self.assertRaises(Invalid):
            check_outbound_url("https://localhost/x", allow_private=False)
        self.assertEqual(check_outbound_url("http://localhost:11434/v1", allow_private=True), "http://localhost:11434/v1")


if __name__ == "__main__":
    unittest.main()
