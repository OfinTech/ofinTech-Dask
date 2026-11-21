"""Settings → Models → Test: the model that is asked is the one the request names, and a provider that has no
such model is reported with the model's name in the message."""

from __future__ import annotations

import json
import unittest

import httpx2 as httpx

from .helpers import FakeLLM, Harness, sse


class ProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.asked: list[str] = []

        def chat(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            self.asked.append(model)
            if model != "fake-large":      # a provider that lists many models and has only one deployed
                return httpx.Response(404, json={"error": {"code": "DeploymentNotFound"}})
            return sse([{"choices": [{"delta": {"content": "OK"}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 1}}])

        self.h.router.add("POST", f"{FakeLLM.BASE}/chat/completions", chat)
        self.ws = self.h.setup_workspace()
        self.pid = self.h.api("GET", f"/api/w/{self.ws}/providers")["providers"][0]["id"]

    def tearDown(self) -> None:
        self.h.close()

    def ask(self, model: object, expect: int = 200) -> dict:
        return self.h.api("POST", f"/api/w/{self.ws}/providers/{self.pid}/test", json={"model": model}, expect=expect)

    def test_the_named_model_is_the_one_asked(self) -> None:
        self.assertEqual(self.ask("fake-large")["reply"], "OK")
        self.assertEqual(self.asked, ["fake-large"])

    def test_a_model_the_provider_does_not_have_is_named_in_the_error(self) -> None:
        body = self.ask("fake-model", expect=502)
        self.assertEqual(body["error"], "llm_not_found")
        self.assertIn("fake-model", body["message"])
        self.assertIn("Fake", body["message"])
        self.assertEqual(self.asked, ["fake-model"])      # asked once: a missing model is not retried

    def test_no_model_is_refused_before_the_provider_is_asked(self) -> None:
        for nothing in (None, ""):
            self.ask(nothing, expect=422)
        self.assertEqual(self.asked, [])


if __name__ == "__main__":
    unittest.main()
