"""Regression tests for the findings of the enterprise audit (docs/audit/02-SECURITY-AUDIT.md).

One test per finding, named after it, so a reader of the audit can find the proof that each fix holds. The
tests repeat the attempt that was possible before the fix and expect it to be refused; where the fix is a
hardening with no earlier attack, they pin the new behaviour.
"""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from .helpers import Harness

PW = "Correct-Horse-9"


class OB01ZohoCallback(unittest.TestCase):
    """The accounts server and API domain named by the OAuth callback are accepted only when they are exactly one
    of Zoho's data centres: a look-alike host must receive neither the code nor the client secret."""

    def test_lookalike_accounts_server_gets_no_token_request(self) -> None:
        h = Harness()
        try:
            ws = h.setup_workspace(with_model=False)
            h.api("PUT", "/api/oauth-apps/zoho_books", json={"client_id": "zoho-cid", "client_secret": "zs"})
            for host in ("https://accounts.zoho.attacker.test", "https://accounts.zohocloud.attacker.test",
                         "https://accounts.zoho.com.attacker.test", "https://accounts.zoho.com@attacker.test"):
                url = h.api("POST", f"/api/w/{ws}/connections/zoho_books/authorize", json={})["url"]
                state = parse_qs(urlparse(url).query)["state"][0]
                r = h.client.get("/oauth/callback", params={"code": "c", "state": state, "accounts-server": host})
                self.assertEqual(r.status_code, 400, host)
            self.assertEqual([str(r.url) for r in h.router.requests if "attacker" in str(r.url)], [])
        finally:
            h.close()

    def test_api_domain_from_token_response_is_allow_listed(self) -> None:
        from desk_engine.connectors.flows import zoho_accounts_server, zoho_api_domain
        from desk_engine.errors import Invalid

        self.assertEqual(zoho_accounts_server(None, "eu"), "https://accounts.zoho.eu")
        self.assertEqual(zoho_accounts_server("https://accounts.zoho.sa/", None), "https://accounts.zoho.sa")
        self.assertEqual(zoho_api_domain("https://www.zohoapis.eu"), "https://www.zohoapis.eu")
        self.assertEqual(zoho_api_domain(None), "https://www.zohoapis.com")
        for bad in ("https://www.zohoapis.eu.attacker.test", "https://attacker.test", "http://www.zohoapis.eu"):
            with self.assertRaises(Invalid):
                zoho_api_domain(bad)
        with self.assertRaises(Invalid):
            zoho_accounts_server("https://accounts.zoho", None)


class OB02OriginCheck(unittest.TestCase):
    """Cookie-authenticated writes compare the whole origin, never a prefix."""

    def test_lookalike_origins_are_refused(self) -> None:
        h = Harness("server")
        try:
            c = h.client
            r = c.post("/api/auth/signup", json={"email": "owner@acme.test", "name": "Olivia", "password": PW})
            self.assertEqual(r.status_code, 200, r.text)
            body = {"name": "Acme", "base_currency": "USD"}
            refused = ["https://desk.example.com.attacker.test", "https://desk.example.community",
                       "http://desk.example.com", "https://desk.example.com:8443", "https://attacker.test", "null"]
            for origin in refused:
                r = c.post("/api/workspaces", json=body, headers={"origin": origin})
                self.assertEqual((r.status_code, r.json()["error"]), (403, "bad_origin"), origin)
                r = c.post("/api/workspaces", json=body, headers={"referer": origin + "/#/x"})
                self.assertEqual((r.status_code, r.json()["error"]), (403, "bad_origin"), origin)
            for ok in ("https://desk.example.com", "HTTPS://DESK.EXAMPLE.COM"):
                r = c.post("/api/workspaces", json=body, headers={"origin": ok})
                self.assertEqual(r.status_code, 200, (ok, r.text))
            r = c.post("/api/workspaces", json=body, headers={"referer": "https://desk.example.com/#/settings"})
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            h.close()

    def test_website_forms_use_the_same_exact_match(self) -> None:
        h = Harness("server", site_url="https://ofintech.example")
        try:
            data = {"email": "owner@acme.test", "name": "Olivia", "password": PW}
            r = h.client.post("/auth/form/sign-up", data=data, headers={"origin": "https://ofintech.example.attacker.test"},
                              follow_redirects=False)
            self.assertEqual(r.status_code, 403)
            r = h.client.post("/auth/form/sign-up", data=data, headers={"origin": "https://ofintech.example"},
                              follow_redirects=False)
            self.assertEqual(r.status_code, 303, r.text)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
