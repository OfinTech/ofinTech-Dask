"""Interface language: saved per person, used by the assistant, and applied to workflow templates."""

from __future__ import annotations

import unittest

from .helpers import Harness


class Language(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()

    def tearDown(self) -> None:
        self.h.close()

    def test_language_follows_the_person(self) -> None:
        h, ws = self.h, self.ws
        self.assertIsNone(h.api("GET", "/api/session")["user"]["language"])
        h.llm.script = ["ok"]
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "hello"})
        h.wait_run(t["id"])
        system = h.llm.calls[-1]["messages"][0]["content"]
        self.assertIn("Reply in the language of the user's latest message", system)
        self.assertNotIn("interface language is", system)
        self.assertEqual(h.api("PATCH", "/api/me", json={"language": "ar"})["language"], "ar")
        self.assertEqual(h.api("GET", "/api/session")["user"]["language"], "ar")
        h.api("PATCH", "/api/me", json={"language": "de"}, expect=422)
        h.llm.script = ["تم"]
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "ما هو رصيد النقد؟"})
        h.wait_run(t["id"])
        self.assertIn("The user's interface language is Arabic.", h.llm.calls[-1]["messages"][0]["content"])
        tpl = h.api("GET", f"/api/w/{ws}/workflow-templates")  # the person's language by default
        self.assertEqual(tpl[0]["name"], "حزمة نهاية الشهر")
        fr = h.api("GET", f"/api/w/{ws}/workflow-templates", params={"lang": "fr"})
        self.assertEqual(fr[0]["definition"]["steps"][0]["name"], "Construire le dossier")
        wf = h.api("POST", f"/api/w/{ws}/workflows/from-template", json={"key": "weekly_ar", "lang": "es"})
        self.assertEqual(wf["name"], "Seguimiento semanal de cobros")


if __name__ == "__main__":
    unittest.main()


class ProductionConfig(unittest.TestCase):
    def test_server_refuses_unsafe_settings(self) -> None:
        import os
        import secrets
        import base64
        from unittest import mock

        from desk_engine.config import load_settings
        from desk_engine.errors import ConfigError

        key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        base = {"DESK_MODE": "server", "DESK_MASTER_KEY": key, "DESK_PUBLIC_URL": "https://desk.example.com",
                "DESK_DATA_DIR": "/tmp/desk-config-test"}
        with mock.patch.dict(os.environ, base, clear=False):
            os.environ.pop("DESK_DEV", None)
            os.environ.pop("DESK_ALLOW_HTTP", None)
            self.assertEqual(load_settings().mode, "server")
            with mock.patch.dict(os.environ, {"DESK_DEV": "1"}):
                with self.assertRaises(ConfigError):
                    load_settings()
            with mock.patch.dict(os.environ, {"DESK_PUBLIC_URL": "http://desk.example.com"}):
                with self.assertRaises(ConfigError):
                    load_settings()
            with mock.patch.dict(os.environ, {"DESK_PUBLIC_URL": "http://10.0.0.5", "DESK_ALLOW_HTTP": "true"}):
                self.assertEqual(load_settings().public_url, "http://10.0.0.5")
            with mock.patch.dict(os.environ, {"DESK_MASTER_KEY": ""}):
                with self.assertRaises(ConfigError):
                    load_settings()
