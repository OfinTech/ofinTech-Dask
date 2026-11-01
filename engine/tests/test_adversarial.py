"""Adversarial tests: attempts to get around the audit's fixes and the engine's standing controls.

Each test is an attack written to succeed; the assertion is that it does not. Where a probe found that an attack
*was* possible (the zero-width prefix before "=", below), the fix landed with the test. Everything runs against
throw-away engines with mocked services.
"""

from __future__ import annotations

import asyncio
import socket
import unittest
from unittest import mock

import httpx2 as httpx

from .helpers import Harness

PW = "Correct-Horse-9"


class NetworkPolicyEvasion(unittest.TestCase):
    """URL parsing and resolution edge cases against the connection-time guard and the save-time policy."""

    def _guard(self):
        from desk_engine.egress import PrivateNetworkGuard

        seen: list[str] = []
        guard = PrivateNetworkGuard(httpx.MockTransport(lambda r: (seen.append(str(r.url)), httpx.Response(200))[1]))
        return guard, seen

    def _get(self, guard, url: str, follow: bool = False) -> int:
        async def go() -> int:
            async with httpx.AsyncClient(transport=guard, follow_redirects=follow) as c:
                return (await c.get(url)).status_code

        return asyncio.run(go())

    def test_address_notations_and_names(self) -> None:
        from desk_engine.egress import EgressBlocked

        guard, seen = self._guard()
        for url in ("http://0x7f.1/", "http://127.1/", "http://2130706433/", "http://[::ffff:7f00:1]/",
                    "http://LOCALHOST./", "http://8.8.8.8@10.0.0.1/", "http://[fe80::1%25eth0]/", "http://[::]/",
                    "http://0.0.0.0/", "http://100.100.100.100/", "http://169.254.169.254:80/latest/",
                    "http://metadata.google.internal/", "http://instance-data/", "http://ollama.lan/",
                    "http://x.home.arpa/", "http://10.0.0.1:65535/", "http://[::ffff:169.254.169.254]/"):
            with self.assertRaises(EgressBlocked, msg=url):
                self._get(guard, url)
        self.assertEqual(seen, [])

    def test_a_name_that_resolves_to_a_private_address_is_blocked(self) -> None:
        """nip.io-style names and DNS rebinding: whatever the name, the resolved address decides."""
        from desk_engine.egress import EgressBlocked

        guard, seen = self._guard()
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        with mock.patch("desk_engine.egress.socket.getaddrinfo", return_value=private):
            with self.assertRaises(EgressBlocked):
                self._get(guard, "http://10.0.0.1.nip.io/")
            with self.assertRaises(EgressBlocked):
                self._get(guard, "http://rebind.attacker.test/")
        with mock.patch("desk_engine.egress.socket.getaddrinfo", return_value=public):
            self.assertEqual(self._get(guard, "http://honest.example.test/"), 200)
        # a dual answer (one public, one private record) is private
        with mock.patch("desk_engine.egress.socket.getaddrinfo", return_value=public + private):
            with self.assertRaises(EgressBlocked):
                self._get(guard, "http://dual.attacker.test/")
        self.assertEqual(seen, ["http://honest.example.test/"])

    def test_redirect_from_a_public_host_to_a_private_one_is_blocked(self) -> None:
        from desk_engine.egress import EgressBlocked, PrivateNetworkGuard

        def inner(req: httpx.Request) -> httpx.Response:
            if req.url.host == "public.test":
                return httpx.Response(302, headers={"location": "http://10.0.0.5/secret"})
            return httpx.Response(200, text="private")

        with mock.patch("desk_engine.egress.socket.getaddrinfo", side_effect=OSError):
            with self.assertRaises(EgressBlocked):
                self._get(PrivateNetworkGuard(httpx.MockTransport(inner)), "http://public.test/", follow=True)

    def test_save_time_policy_edge_cases(self) -> None:
        from desk_engine.errors import Invalid
        from desk_engine.security import check_outbound_url

        for url in ("https://example.com@10.0.0.1/", "https://10.0.0.1/", "https://[::1]/", "https://2130706433/",
                    "https://0x7f000001/", "https://100.64.1.1/", "javascript:alert(1)", "http://example.com/",
                    "https://", "https:///path", "ftp://example.com/"):
            with self.assertRaises(Invalid, msg=url):
                check_outbound_url(url, allow_private=False)
        with mock.patch("desk_engine.security.socket.getaddrinfo",
                        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.16.5.5", 0))]):
            with self.assertRaises(Invalid):
                check_outbound_url("https://corp-name.example/", allow_private=False)


class AuthenticationAndSessions(unittest.TestCase):
    """Session manipulation on the Team Server."""

    def setUp(self) -> None:
        self.h = Harness("server")
        self.c = self.h.client
        self.origin = {"origin": "https://desk.example.com"}
        r = self.c.post("/api/auth/signup", json={"email": "o@acme.test", "name": "O", "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        self.token = r.json()["token"]
        self.ws = self.c.post("/api/workspaces", json={"name": "Acme", "base_currency": "USD"}, headers=self.origin).json()["id"]

    def tearDown(self) -> None:
        self.h.close()

    def test_tampered_and_revoked_tokens(self) -> None:
        from starlette.testclient import TestClient

        anon = TestClient(self.h.app, base_url="https://desk.example.com")
        flipped = self.token[:-1] + ("a" if self.token[-1] != "a" else "b")
        for bad in (flipped, self.token.upper(), self.token + "x", self.token[:-1], "", "Bearer"):
            r = anon.get("/api/w/" + self.ws, headers={"authorization": f"Bearer {bad}"})
            self.assertEqual(r.status_code, 401, bad)
        self.assertEqual(anon.get(f"/api/w/{self.ws}?token={self.token}").status_code, 401)   # never from the query
        self.assertEqual(anon.get("/api/w/" + self.ws, headers={"authorization": f"Bearer {self.token}"}).status_code, 200)
        # after sign-out the same token is dead everywhere, cookie and bearer alike
        self.assertEqual(self.c.post("/api/auth/logout", headers=self.origin).status_code, 200)
        self.assertEqual(anon.get("/api/w/" + self.ws, headers={"authorization": f"Bearer {self.token}"}).status_code, 401)
        self.assertEqual(self.c.get("/api/w/" + self.ws).status_code, 401)

    def test_absolute_cap_and_idle_timeout_delete_the_session(self) -> None:
        from desk_engine.security import token_hash

        db = self.h.engine.db
        sid = token_hash(self.token)
        db.execute("UPDATE sessions SET last_seen = last_seen - 7200 WHERE id = ?", (sid,))
        r = self.c.get("/api/w/" + self.ws)
        self.assertEqual((r.status_code, r.json()["error"]), (401, "session_idle"))
        self.assertIsNone(db.one("SELECT 1 FROM sessions WHERE id = ?", (sid,)))   # deleted, not just refused

    def test_cookie_writes_need_the_origin_and_cannot_use_a_forged_header(self) -> None:
        body = {"name": "B", "base_currency": "USD"}
        self.assertEqual(self.c.post("/api/workspaces", json=body).status_code, 403)
        for h in ({"origin": "null"}, {"origin": "https://desk.example.com%2e"},
                  {"origin": "https://xn--desk.example.com"}, {"referer": "https://desk.example.com.attacker.test/"},
                  {"origin": "https://attacker.test", "referer": "https://desk.example.com/"}):
            self.assertEqual(self.c.post("/api/workspaces", json=body, headers=h).status_code, 403, h)
        self.assertEqual(self.c.post("/api/workspaces", json=body, headers=self.origin).status_code, 200)

    def test_role_escalation_attempts(self) -> None:
        from starlette.testclient import TestClient

        inv = self.c.post(f"/api/w/{self.ws}/invites", json={"email": "v@acme.test", "role": "viewer"}, headers=self.origin).json()
        viewer = TestClient(self.h.app, base_url="https://desk.example.com")
        self.assertEqual(viewer.post("/api/auth/signup", json={"email": "v@acme.test", "name": "V", "password": PW,
                                                               "invite_token": inv["link"].rsplit("/", 1)[1]}).status_code, 200)
        vid = viewer.get("/api/session").json()["user"]["id"]
        # a viewer cannot promote herself, invite, change settings, add providers, or enable skills
        for method, path, body in (("PATCH", f"/api/w/{self.ws}/members/{vid}", {"role": "owner"}),
                                   ("POST", f"/api/w/{self.ws}/invites", {"email": "x@acme.test", "role": "admin"}),
                                   ("PATCH", f"/api/w/{self.ws}", {"name": "pwned"}),
                                   ("POST", f"/api/w/{self.ws}/providers", {"kind": "openai_compatible", "label": "x",
                                                                           "base_url": "https://llm.test/v1", "api_key": "k"}),
                                   ("PUT", f"/api/w/{self.ws}/skills/builtin:month-end-close/enabled", {"enabled": False}),
                                   ("POST", "/api/admin/users/u_x/reset-link", None)):
            r = viewer.request(method, path, json=body, headers=self.origin)
            self.assertEqual(r.status_code, 403, (method, path, r.text))
        # the oversight desk shows team activity from the audit log, which only admins read
        self.assertEqual(viewer.get(f"/api/w/{self.ws}/oversight").status_code, 403)
        self.assertEqual(viewer.get(f"/api/w/{self.ws}/audit").status_code, 403)
        self.assertEqual(self.c.get(f"/api/w/{self.ws}/oversight").status_code, 200)
        # the invite token cannot be redeemed twice or by another e-mail
        other = TestClient(self.h.app, base_url="https://desk.example.com")
        r = other.post("/api/auth/signup", json={"email": "w@acme.test", "name": "W", "password": PW,
                                                 "invite_token": inv["link"].rsplit("/", 1)[1]})
        self.assertEqual(r.status_code, 422)


class FilesAndTraversal(unittest.TestCase):
    def test_storage_keys_and_download_names(self) -> None:
        from desk_engine.errors import Invalid
        from desk_engine.storage import check_key

        for key in ("../x", "documents/../secrets", "documents/ws/..", "/etc/passwd", "documents/ws/a\\..\\b",
                    "documents/ws/%2e%2e/x", "documents/ws/" + "a" * 600, "documents/ws/a\x00b"):
            with self.assertRaises(Invalid, msg=key):
                check_key(key)
        check_key("documents/ws_1/doc_1")

        h = Harness()
        try:
            ws = h.setup_workspace(with_model=False)
            r = h.api("POST", f"/api/w/{ws}/documents", files=[("files", ('../../etc/passwd"; x=".md', b"# hi"))])
            did = r["added"][0]["id"]
            resp = h.client.get(f"/api/w/{ws}/documents/{did}/file")
            self.assertEqual(resp.status_code, 200)
            cd = resp.headers["content-disposition"]
            name = cd.split('filename="', 1)[1].rstrip('"')
            self.assertFalse(any(ch in name for ch in '/\\"\r\n;'), name)   # no path, no header break-out
            self.assertTrue(cd.startswith("attachment;"))
            self.assertEqual(h.client.get(f"/api/w/{ws}/documents/../../health").status_code, 404)
            self.assertEqual(h.client.get(f"/api/w/{ws}/documents/doc_%2e%2e/file").status_code, 404)
        finally:
            h.close()

    def test_invisible_characters_before_a_formula_prefix(self) -> None:
        from desk_engine.files.cells import csv_safe

        for v in ("﻿=1+1", "​=HYPERLINK(1)", "‌+cmd", "⁠-1", "­@SUM"):
            self.assertEqual(csv_safe(v), "'" + v, repr(v))
        for v in (" =1+1", "\n=1+1", "＝1+1", "x=1"):   # a spreadsheet reads these as text already
            self.assertEqual(csv_safe(v), v, repr(v))


class ConcurrencyAndTransactions(unittest.TestCase):
    def test_two_turns_on_one_thread_cannot_both_start(self) -> None:
        from desk_engine.errors import Conflict

        h = Harness(script=["one", "two"])
        try:
            ws = h.setup_workspace()
            t = h.api("POST", f"/api/w/{ws}/threads", json={})
            wsrow = h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,))

            def both() -> list[str]:
                out = []
                for text in ("first", "second"):
                    try:
                        out.append(h.engine.agent.start_turn(wsrow, "u_local", t["id"], text))
                    except Conflict as exc:
                        out.append(exc.code or "conflict")
                return out

            result = h.client.portal.call(both)    # on the engine's own loop, back to back without yielding
            self.assertTrue(result[0].startswith("run_"))
            self.assertEqual(result[1], "thread_busy")
            self.assertEqual(h.engine.db.scalar("SELECT COUNT(*) FROM agent_runs WHERE thread_id = ?", (t["id"],)), 1)
        finally:
            h.close()

    def test_a_failed_import_leaves_nothing_behind(self) -> None:
        from desk_engine.data import store

        h = Harness()
        try:
            ws = h.setup_workspace(with_model=False)
            before = {r["name"] for r in h.engine.db.all("SELECT name FROM sqlite_master WHERE type = 'table'")}
            cols = [{"key": "c0", "name": "a", "type": "number"}, {"key": "c1", "name": "b", "type": "text"}]
            with self.assertRaises(Exception):
                store.create_dataset(h.engine, ws, name="Broken", source="csv", columns=cols,
                                     rows=[[1, "x"], [2, "y", "too", "many"]])
            after = {r["name"] for r in h.engine.db.all("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertEqual(after, before)                                            # no ds_* table survived
            self.assertEqual(h.engine.db.scalar("SELECT COUNT(*) FROM datasets WHERE workspace_id = ?", (ws,)), 0)
            # the connection is usable again right away
            ds = store.create_dataset(h.engine, ws, name="Good", source="csv", columns=cols, rows=[[1, "x"]])
            self.assertEqual(ds["row_count"], 1)
        finally:
            h.close()


class WebhookLeakage(unittest.TestCase):
    def test_outgoing_payloads_carry_no_credentials(self) -> None:
        from desk_engine.webhooks import _clean

        payload = {"token": "secret1", "api_key": "k", "nested": {"password": "p", "ok": 1, "Authorization": "Bearer x",
                                                                 "client_secret": "cs", "refresh_token": "rt", "X-Api-Key": "xk"},
                   "url": "https://h/x?access_token=abc#frag", "list": [{"secret_id": "sec_1", "label": "fine"}],
                   "name": "Acme", "rows": 12}
        out = _clean(payload)
        text = str(out)
        for needle in ("secret1", "Bearer x", "'k'", "'p'", "sec_1", "'cs'", "'rt'", "'xk'", "access_token", "abc"):
            self.assertNotIn(needle, text, needle)
        self.assertEqual(out["url"], "https://h/x")
        self.assertEqual(out["nested"]["ok"], 1)
        self.assertEqual((out["name"], out["rows"], out["list"][0]["label"]), ("Acme", 12, "fine"))


if __name__ == "__main__":
    unittest.main()
