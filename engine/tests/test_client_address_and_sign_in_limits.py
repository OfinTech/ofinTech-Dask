"""Whose address a request comes from, and how sign-in attempts are counted.

What this guards.

- **A client cannot choose its own address.** The server reads X-Forwarded-For only when the connection comes from
  a trusted proxy, and then takes the address that proxy added, not the ones the client wrote before it. Before,
  every sender was trusted and the first address in the header was taken, so a client that wrote a new address on
  each request was never counted by a per-address limit (sign-in, sign-up, password reset), and the logs recorded
  the address it chose.
- **Knowing someone's e-mail is not enough to lock them out.** The tight limit counts one account from one
  address; a looser one counts the account from everywhere. Before, eight wrong passwords from anywhere refused the
  right password from the owner's own address for five minutes.

Every address below is from the ranges set aside for documentation (RFC 5737) or a private network; every name is
invented.
"""

from __future__ import annotations

import base64
import dataclasses
import os
import unittest
from typing import Any
from unittest import mock

from starlette.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from desk_engine.__main__ import proxy_options
from desk_engine.config import Settings, load_settings
from desk_engine.routes import auth

from .helpers import Harness

PW = "Ledger-Balance-2026!"
BASE = "https://desk.example.com"
ORIGIN = {"origin": BASE}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(mode="server")
        self.addCleanup(self.h.close)
        r = self.h.client.post("/api/auth/signup", json={"email": "owner@acme.test", "name": "Olivia", "password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        self.h.engine.__dict__.pop("_auth_attempts", None)

    def through(self, peer: str) -> TestClient:
        """A client whose connection arrives from ``peer``, behind the middleware uvicorn runs with these settings."""
        app = ProxyHeadersMiddleware(self.h.app, trusted_hosts=proxy_options(self.h.settings)["forwarded_allow_ips"])
        return TestClient(app, base_url=BASE, client=(peer, 40000))

    def login(self, c: TestClient, password: str = "Wrong-pass-2026", email: str = "owner@acme.test",
              forwarded: str | None = None) -> int:
        headers = dict(ORIGIN) | ({"x-forwarded-for": forwarded} if forwarded else {})
        return c.post("/api/auth/login", json={"email": email, "password": password}, headers=headers).status_code

    def counted(self) -> set[str]:
        return {k.split(":", 1)[1] for k in self.h.engine.__dict__.get("_auth_attempts", {}) if k.startswith("login-ip:")}


class TheClientsAddress(Base):
    def test_a_server_never_trusts_every_sender_and_the_desktop_reads_no_header(self) -> None:
        opts = proxy_options(self.h.settings)
        self.assertTrue(opts["proxy_headers"])
        self.assertNotIn("*", opts["forwarded_allow_ips"])
        self.assertEqual(proxy_options(dataclasses.replace(self.h.settings, mode="desktop")), {"proxy_headers": False})

    def test_the_operator_names_the_proxies(self) -> None:
        env = {"DESK_MODE": "server", "DESK_MASTER_KEY": base64.b64encode(b"k" * 32).decode(),
               "DESK_PUBLIC_URL": BASE, "DESK_DATA_DIR": self.h.tmp.name, "DESK_TRUSTED_PROXIES": "173.245.48.0/20"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(proxy_options(load_settings())["forwarded_allow_ips"], "173.245.48.0/20")
        env.pop("DESK_TRUSTED_PROXIES")
        with mock.patch.dict(os.environ, env):
            os.environ.pop("DESK_TRUSTED_PROXIES", None)
            self.assertEqual(proxy_options(load_settings())["forwarded_allow_ips"], Settings.trusted_proxies)

    def test_a_client_that_connects_directly_is_counted_at_its_own_address_whatever_it_writes(self) -> None:
        c = self.through("203.0.113.9")
        codes = [self.login(c, email=f"guess{i}@acme.test", forwarded=f"198.51.100.{i}") for i in range(35)]
        self.assertEqual(self.counted(), {"203.0.113.9"})
        self.assertIn(429, codes)
        self.assertEqual(codes.index(429), auth.LOGIN_PER_ADDRESS)

    def test_behind_a_proxy_the_address_is_the_one_the_proxy_added(self) -> None:
        c = self.through("172.18.0.5")                          # Caddy, nginx or an ingress on the private network
        self.login(c, forwarded="198.51.100.1, 203.0.113.50")    # the client wrote the first; the proxy added the last
        self.assertEqual(self.counted(), {"203.0.113.50"})

    def test_through_two_proxies_the_address_is_the_last_one_that_is_not_a_proxy(self) -> None:
        c = self.through("10.0.0.7")
        self.login(c, forwarded="198.51.100.1, 203.0.113.51, 10.0.3.4")
        self.assertEqual(self.counted(), {"203.0.113.51"})


class SignInAttempts(Base):
    def test_wrong_passwords_from_elsewhere_do_not_lock_the_owner_out_of_their_own_address(self) -> None:
        guesser, owner = self.through("203.0.113.66"), self.through("198.51.100.20")
        codes = [self.login(guesser) for _ in range(auth.LOGIN_PER_ACCOUNT_AND_ADDRESS + 2)]
        self.assertEqual(codes.count(429), 2)                    # the guesser is stopped
        self.assertEqual(self.login(owner, PW), 200)             # the owner is not

    def test_an_account_has_a_ceiling_over_every_address_together(self) -> None:
        for i in range(auth.LOGIN_PER_ACCOUNT):
            self.assertEqual(self.login(self.through(f"203.0.113.{i + 1}")), 401)
        self.assertEqual(self.login(self.through("198.51.100.20"), PW), 429)

    def test_failed_sign_ins_do_not_stop_the_signed_in_owner_confirming_their_password(self) -> None:
        owner = TestClient(self.h.app, base_url=BASE, client=("198.51.100.20", 40000))
        self.assertEqual(self.login(owner, PW), 200)
        guesser = self.through("203.0.113.66")
        for _ in range(auth.LOGIN_PER_ACCOUNT_AND_ADDRESS + 2):
            self.login(guesser)
        r = owner.post("/api/auth/reauth", json={"password": PW}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)

    def test_the_counters_do_not_grow_without_end(self) -> None:
        attempts: dict[str, Any] = {}
        with mock.patch.object(auth, "MAX_TRACKED_KEYS", 50):
            with mock.patch("desk_engine.routes.auth.time.monotonic", return_value=1000.0):
                for i in range(60):
                    auth._rate_limit(self.h.engine, f"probe:{i}")
            with mock.patch("desk_engine.routes.auth.time.monotonic", return_value=2000.0):   # all of them stale
                auth._rate_limit(self.h.engine, "probe:new")
            attempts = self.h.engine.__dict__["_auth_attempts"]
        self.assertEqual(set(attempts), {"probe:new"})


if __name__ == "__main__":
    unittest.main()
