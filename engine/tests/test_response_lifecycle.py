"""An answer is stored as complete only with completion evidence (audit findings AI-03, AI-06, AI-07, AI-08).

Each case scripts the model's HTTP stream exactly, then checks what the run and the conversation say: a cut stream,
an answer stopped by the output limit or a filter, an empty or malformed reply, timeouts and provider errors
(retried only when that can help and only before anything was shown), a tool that crashes, and a stop by the user.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import httpx2 as httpx

from .helpers import FakeLLM, Harness, sse

URL = f"{FakeLLM.BASE}/chat/completions"
ANTHROPIC = "https://anthropic.test/v1"


def words(text: str, finish: str | None = "stop") -> list[dict]:
    out = [{"choices": [{"delta": {"content": w + " "}}]} for w in text.split()]
    if finish:
        out.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return out


def raw(chunks: list[dict], done: bool = True, extra: str = "") -> httpx.Response:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + extra + ("data: [DONE]\n\n" if done else "")
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


class Lifecycle(unittest.TestCase):
    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {"DESK_LLM_RETRY_BASE_S": "0", "DESK_LLM_RETRY_MAX_WAIT_S": "0.05"})
        env.start()
        self.addCleanup(env.stop)
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.h.setup_workspace()
        self.calls = 0

    def serve(self, *responses):
        """Answers model requests in order; the last one repeats."""
        seq = list(responses)

        def handler(request):
            self.calls += 1
            item = seq.pop(0) if len(seq) > 1 else seq[0]
            if isinstance(item, Exception):
                raise item
            return item(request) if callable(item) else item
        self.h.router.add("POST", URL, handler)

    def turn(self, text: str = "How did October go?") -> dict:
        t = self.h.api("POST", f"/api/w/{self.h.ws}/threads", json={})["id"]
        self.h.api("POST", f"/api/w/{self.h.ws}/threads/{t}/messages", json={"text": text})
        d = self.h.wait_run(t)
        d["thread_id"] = t
        return d

    def answers(self, d: dict) -> list[dict]:
        return [m for m in d["messages"] if m["role"] == "assistant"]

    def errors(self, d: dict) -> list[dict]:
        return [m for m in d["messages"] if m["role"] == "event" and (m["payload"] or {}).get("kind") == "error"]

    def assertNotComplete(self, d: dict, outcome: str, code: str) -> None:
        self.assertEqual(d["run"]["status"], "error")
        self.assertIsNone(d["run"]["final_text"])
        self.assertEqual(d["run"]["outcome"], outcome)
        self.assertEqual(d["run"]["error_code"], code)
        self.assertEqual(self.errors(d)[-1]["payload"]["code"], code)

    # -- incomplete answers are never final ------------------------------------------------------------------------
    def test_stream_cut_before_any_end_marker(self) -> None:
        self.serve(raw(words("Revenue for October was 120,450.00 and the margin", finish=None), done=False))
        d = self.turn()
        self.assertNotComplete(d, "failed", "llm_incomplete")
        self.assertEqual(self.answers(d), [])
        self.assertEqual(self.calls, 1)          # text was already shown: never replayed

    def test_stream_cut_before_anything_was_shown_is_retried(self) -> None:
        self.serve(raw([], done=False), sse(words("All good.")))
        d = self.turn()
        self.assertEqual(d["run"]["status"], "done")
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.answers(d)[-1]["payload"]["timing"]["attempts"], 2)

    def test_answer_cut_by_the_output_limit(self) -> None:
        self.serve(sse(words("The three largest variances are rent at", finish="length")))
        d = self.turn()
        self.assertNotComplete(d, "incomplete", "answer_incomplete")
        kept = self.answers(d)[-1]
        self.assertTrue(kept["payload"]["incomplete"])
        self.assertEqual(kept["payload"]["finish"], "length")
        self.assertNotIn("unverified_figures", kept["payload"])     # an incomplete answer is not checked as final
        self.assertIn("output limit", self.errors(d)[-1]["content"])

    def test_answer_stopped_by_a_content_filter(self) -> None:
        self.serve(sse(words("Here is", finish="content_filter")))
        d = self.turn()
        self.assertNotComplete(d, "incomplete", "answer_incomplete")

    def test_empty_reply(self) -> None:
        self.serve(sse([{"choices": [{"delta": {}, "finish_reason": "stop"}]}]))
        d = self.turn()
        self.assertNotComplete(d, "failed", "answer_empty")
        self.assertEqual(self.answers(d), [])

    def test_malformed_chunk(self) -> None:
        good = words("Net profit was 45,000.00 in Q3.")
        self.serve(raw(good[:2] + good[2:], extra='data: {"choices": [{"delta": {"content": "NOT 45"\n\n'))
        d = self.turn()
        self.assertNotComplete(d, "failed", "llm_malformed")

    # -- provider errors: classified, retried only when it can help ------------------------------------------------
    def test_timeout_is_classified_and_retried_within_a_bound(self) -> None:
        self.serve(httpx.ReadTimeout("read timed out"))
        d = self.turn()
        self.assertNotComplete(d, "timed_out", "llm_timeout")
        self.assertNotIn("Unexpected error", d["run"]["error"])
        self.assertEqual(self.calls, 3)          # 1 + DESK_LLM_RETRIES (2)

    def test_one_5xx_then_success(self) -> None:
        self.serve(httpx.Response(503, text="overloaded"), sse(words("Recovered.")))
        d = self.turn()
        self.assertEqual(d["run"]["status"], "done")
        self.assertEqual(d["run"]["outcome"], "completed")
        self.assertEqual(self.calls, 2)

    def test_429_honours_retry_after_and_stops_at_the_bound(self) -> None:
        self.serve(httpx.Response(429, json={"error": "slow down"}, headers={"retry-after": "0.01"}))
        with mock.patch.dict(os.environ, {"DESK_LLM_RETRIES": "3"}):
            d = self.turn()
        self.assertNotComplete(d, "failed", "llm_rate_limited")
        self.assertEqual(self.calls, 4)

    def test_permanent_errors_are_not_retried(self) -> None:
        for status, code in ((400, "llm_error"), (401, "llm_auth"), (404, "llm_not_found")):
            self.calls = 0
            self.serve(httpx.Response(status, text="no"))
            d = self.turn()
            self.assertNotComplete(d, "failed", code)
            self.assertEqual(self.calls, 1, status)

    def test_connection_dropped(self) -> None:
        self.serve(httpx.RemoteProtocolError("peer closed connection"), sse(words("Back.")))
        d = self.turn()
        self.assertEqual(d["run"]["status"], "done")
        self.assertEqual(self.calls, 2)

    def test_retries_can_be_switched_off(self) -> None:
        self.serve(httpx.Response(503, text="x"), sse(words("never")))
        with mock.patch.dict(os.environ, {"DESK_LLM_RETRIES": "0"}):
            d = self.turn()
        self.assertNotComplete(d, "failed", "llm_unavailable")
        self.assertEqual(self.calls, 1)

    # -- tools ------------------------------------------------------------------------------------------------------
    def test_a_tool_that_crashes_does_not_lose_the_turn(self) -> None:
        import sqlite3

        from desk_engine.agent.tools import registry

        async def boom(ctx, a):
            raise sqlite3.OperationalError("database is locked")
        tool = registry.get_static("calc_growth")
        old = tool.handler
        tool.handler = boom
        try:
            self.h.llm.script = [[("calc_growth", {"values": [1, 2]})], "That step could not be completed."]
            d = self.turn("growth")
        finally:
            tool.handler = old
        self.assertEqual(d["run"]["status"], "done")
        result = [m for m in d["messages"] if m["role"] == "tool"][0]
        self.assertIn("failed unexpectedly (OperationalError)", result["content"])
        self.assertNotIn("database is locked", result["content"])    # details stay in the log
        self.assertEqual(result["payload"]["status"], "error")

    def test_stop_by_the_user_is_recorded_as_cancelled(self) -> None:
        import asyncio
        import time

        from desk_engine.agent.tools import registry

        async def slow(ctx, a):
            await asyncio.sleep(10)
        tool = registry.get_static("calc_growth")
        old = tool.handler
        tool.handler = slow
        try:
            self.h.llm.script = [[("calc_growth", {"values": [1, 2]})], "never reached"]
            t = self.h.api("POST", f"/api/w/{self.h.ws}/threads", json={})["id"]
            self.h.api("POST", f"/api/w/{self.h.ws}/threads/{t}/messages", json={"text": "growth"})
            time.sleep(0.3)
            self.h.api("POST", f"/api/w/{self.h.ws}/threads/{t}/stop", expect=None)
            time.sleep(0.3)
            d = self.h.api("GET", f"/api/w/{self.h.ws}/threads/{t}")
        finally:
            tool.handler = old
        self.assertEqual(d["run"]["status"], "error")
        self.assertEqual(d["run"]["outcome"], "cancelled")
        self.assertIsNone(d["run"]["final_text"])
        self.assertFalse(self.h.engine.agent.thread_busy(t))

    def test_successful_answer_records_its_timing_and_finish(self) -> None:
        self.h.llm.script = ["Fine."]
        d = self.turn()
        p = self.answers(d)[-1]["payload"]
        self.assertEqual(p["finish"], "stop")
        self.assertEqual(p["timing"]["attempts"], 1)
        self.assertGreaterEqual(p["timing"]["total_ms"], p["timing"]["ttfb_ms"])
        self.assertEqual(d["run"]["outcome"], "completed")


class AnthropicLifecycle(unittest.TestCase):
    """The same rules for the Anthropic adapter (message_stop is its completion evidence; max_tokens is a cut)."""

    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {"DESK_LLM_RETRY_BASE_S": "0"})
        env.start()
        self.addCleanup(env.stop)
        self.h = Harness()
        self.addCleanup(self.h.close)
        ws = self.h.setup_workspace(with_model=False)
        self.h.router.add("GET", f"{ANTHROPIC}/models", {"data": [{"id": "claude-test"}]})
        self.h.api("POST", f"/api/w/{ws}/providers", json={"kind": "anthropic", "label": "Claude", "base_url": ANTHROPIC,
                                                            "api_key": "sk-ant"})

    def run_with(self, events: list[dict]) -> dict:
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        self.h.router.add("POST", f"{ANTHROPIC}/messages",
                          lambda r: httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"}))
        t = self.h.api("POST", f"/api/w/{self.h.ws}/threads", json={})["id"]
        self.h.api("POST", f"/api/w/{self.h.ws}/threads/{t}/messages", json={"text": "hi"})
        return self.h.wait_run(t)

    def events(self, text: str, stop: str | None, message_stop: bool) -> list[dict]:
        ev = [{"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
              {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
              {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}]
        if stop:
            ev.append({"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 3}})
        if message_stop:
            ev.append({"type": "message_stop"})
        return ev

    def test_complete(self) -> None:
        d = self.run_with(self.events("Done.", "end_turn", True))
        self.assertEqual(d["run"]["status"], "done")
        self.assertEqual([m for m in d["messages"] if m["role"] == "assistant"][-1]["payload"]["finish"], "stop")

    def test_cut_stream(self) -> None:
        d = self.run_with(self.events("Revenue was", None, False))
        self.assertEqual(d["run"]["status"], "error")
        self.assertEqual(d["run"]["error_code"], "llm_incomplete")

    def test_max_tokens(self) -> None:
        d = self.run_with(self.events("Revenue was", "max_tokens", True))
        self.assertEqual(d["run"]["status"], "error")
        self.assertEqual(d["run"]["error_code"], "answer_incomplete")


if __name__ == "__main__":
    unittest.main()
