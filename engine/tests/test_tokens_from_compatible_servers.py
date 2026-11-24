"""Token counts from a model served behind an OpenAI-compatible address (vLLM, Ollama, LM Studio, a custom endpoint).

What this guards. Such a server puts token counts in its stream only when the request asks for them
(``stream_options.include_usage``). Until 24 Nov 2026 the engine asked the named cloud providers and did not ask a
custom endpoint, Ollama or LM Studio, because a server that does not know the field may refuse the request. So a
model served that way answered, and every answer was recorded as zero tokens: the Usage page, a plan's allowance
and the cost of a run all read nothing. Now every server is asked. One that refuses the field is tried once more
without it and not asked again; a request that was wrong for another reason still fails and says why.

Every address below is invented and nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import json
import unittest

import httpx2 as httpx

from desk_engine.llm import openai_compat
from desk_engine.llm.base import ChatMessage, complete
from desk_engine.llm.openai_compat import OpenAICompatible
from desk_engine.llm.registry import _build_model

from .helpers import FakeLLM, Harness

ANSWER = b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\n'
COUNTS = b'data: {"choices":[],"usage":{"prompt_tokens":1200,"completion_tokens":80}}\n\n'
DONE = b"data: [DONE]\n\n"


def model(base: str, handler) -> OpenAICompatible:  # type: ignore[no-untyped-def]
    return OpenAICompatible(label="Own server", base_url=base, api_key=None, model="m",
                            transport=httpx.MockTransport(handler))


class Asked(unittest.TestCase):
    def test_a_server_that_counts_only_when_asked_is_asked_and_its_counts_are_kept(self) -> None:
        seen = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            seen.append(body)
            asked = (body.get("stream_options") or {}).get("include_usage")
            return httpx.Response(200, content=ANSWER + (COUNTS if asked else b"") + DONE)

        r = asyncio.run(complete(model("https://counts-when-asked.test/v1", handler), [ChatMessage("user", "hi")]))
        self.assertEqual(seen[0]["stream_options"], {"include_usage": True})
        self.assertEqual((r.text, r.usage.input_tokens, r.usage.output_tokens), ("OK", 1200, 80))
        # the same server, not asked: it answers, and the answer costs nothing on paper (what was happening)
        quiet = OpenAICompatible(label="Own server", base_url="https://counts-when-asked.test/v1", api_key=None,
                                 model="m", stream_usage=False, transport=httpx.MockTransport(handler))
        r = asyncio.run(complete(quiet, [ChatMessage("user", "hi")]))
        self.assertEqual((r.text, r.usage.input_tokens, r.usage.output_tokens), ("OK", 0, 0))

    def test_a_custom_endpoint_ollama_and_lm_studio_are_asked_by_default(self) -> None:
        h = Harness()
        self.addCleanup(h.close)
        ws = h.setup_workspace(with_model=False)
        for kind in ("openai_compatible", "ollama", "lmstudio"):
            p = h.api("POST", f"/api/w/{ws}/providers", json={"kind": kind, "label": kind, "base_url": FakeLLM.BASE,
                                                              "api_key": "sk-test"})
            self.assertTrue(_build_model(h.engine, ws, p["id"], "m").stream_usage, kind)


class Refused(unittest.TestCase):
    def test_a_server_that_refuses_the_field_without_naming_it_answers_and_is_not_asked_again(self) -> None:
        base = "https://refuses-unknown-fields.test/v1"
        seen = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            seen.append("stream_options" in body)
            if "stream_options" in body:
                return httpx.Response(422, json={"detail": [{"type": "extra_forbidden", "msg": "Extra inputs are not permitted"}]})
            return httpx.Response(200, content=ANSWER + DONE)

        r = asyncio.run(complete(model(base, handler), [ChatMessage("user", "hi")]))
        self.assertEqual((r.text, r.usage.input_tokens), ("OK", 0))
        self.assertEqual(seen, [True, False])
        self.assertIn(base, openai_compat._REFUSES_STREAM_OPTIONS)
        # the next request to that server, from a new model object, goes without the field: one request, not two
        r = asyncio.run(complete(model(base, handler), [ChatMessage("user", "again")]))
        self.assertEqual((r.text, seen), ("OK", [True, False, False]))

    def test_a_refusal_in_the_usual_words_for_an_unknown_field_is_understood(self) -> None:
        for n, (status, message) in enumerate(((400, "Unrecognized request argument supplied"),
                                               (400, "json: unknown field \"stream_options\""),
                                               (400, "Additional properties are not allowed"))):
            base = f"https://refusal-{n}.test/v1"

            def handler(req: httpx.Request, message: str = message, status: int = status) -> httpx.Response:
                if "stream_options" in json.loads(req.content):
                    return httpx.Response(status, json={"error": {"message": message}})
                return httpx.Response(200, content=ANSWER + DONE)

            self.assertEqual(asyncio.run(complete(model(base, handler), [ChatMessage("user", "hi")])).text, "OK", message)
            self.assertIn(base, openai_compat._REFUSES_STREAM_OPTIONS)

    def test_a_request_that_is_wrong_for_another_reason_fails_once_and_the_server_keeps_being_asked(self) -> None:
        base = "https://rejects-the-request.test/v1"
        seen = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append("stream_options" in json.loads(req.content))
            return httpx.Response(400, json={"error": {"message": "This model's maximum context length is 32768 tokens."}})

        m = model(base, handler)
        with self.assertRaises(Exception) as ctx:
            asyncio.run(complete(m, [ChatMessage("user", "hi")]))
        self.assertIn("maximum context length", str(ctx.exception))
        # a permanent error is not retried: one request, and the server is not taken for one that refuses the field
        self.assertEqual(seen, [True])
        self.assertNotIn(base, openai_compat._REFUSES_STREAM_OPTIONS)
        self.assertTrue(model(base, handler).stream_usage)

    def test_a_refusal_that_hides_another_fault_fails_with_that_fault_and_is_not_remembered(self) -> None:
        base = "https://refuses-then-fails.test/v1"

        def handler(req: httpx.Request) -> httpx.Response:
            if "stream_options" in json.loads(req.content):
                return httpx.Response(422, json={"detail": "Extra inputs are not permitted"})
            return httpx.Response(401, json={"error": "bad key"})

        m = model(base, handler)
        with self.assertRaises(Exception) as ctx:
            asyncio.run(complete(m, [ChatMessage("user", "hi")]))
        self.assertEqual(getattr(ctx.exception, "code", ""), "llm_auth")
        self.assertNotIn(base, openai_compat._REFUSES_STREAM_OPTIONS)
        self.assertTrue(m.stream_usage)


if __name__ == "__main__":
    unittest.main()
