"""Closing the partly resolved audit findings AI-10, AI-11 and AI-12.

AI-10  a tool that reads the database runs on the event loop: a slow query stalls every conversation and its timeout
       cannot fire.
AI-11  the history sent to the model has a message window but no size budget, and nothing of the earlier answers
       survives once the window moves on.
AI-12  there is no metrics endpoint and no per-run trace of model and tool steps.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from unittest import mock

from desk_engine.agent.tools import ToolContext, ToolOutput, registry
from desk_engine.db import dumps, new_id

from .helpers import Harness


def _ctx(h: Harness, ws: str) -> ToolContext:
    workspace = h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,))
    user = h.engine.db.scalar("SELECT id FROM users WHERE is_local = 1")
    return ToolContext(h.engine, workspace, user)


async def _run_with_ticker(coro) -> tuple[object, float]:
    """Runs ``coro`` while a 10 ms ticker measures the longest stall of the event loop."""
    worst = 0.0
    done = False

    async def tick() -> None:
        nonlocal worst
        last = time.perf_counter()
        while not done:
            await asyncio.sleep(0.01)
            t = time.perf_counter()
            worst = max(worst, t - last - 0.01)
            last = t
    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.05)                   # the ticker is running before the tool starts
    try:
        result = await coro
    finally:
        done = True
        await ticker
    return result, worst


class DatabaseToolsOffTheLoop(unittest.TestCase):
    """AI-10."""

    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()

    def slow_list(self, seconds: float):
        def slow(engine, ws_id):            # a slow SQLite read (a big table, a lock, a cold disk)
            time.sleep(seconds)
            return []
        return mock.patch("desk_engine.data.store.list_datasets", slow)

    def test_a_slow_database_read_does_not_stall_the_loop(self) -> None:
        tool = registry.get_static("list_datasets")
        with self.slow_list(0.8):
            out, worst = asyncio.run(_run_with_ticker(tool.run(_ctx(self.h, self.ws), {})))
        self.assertIn("no datasets", out.text)
        self.assertLess(worst, 0.3, f"the event loop stalled for {worst:.2f}s")

    def test_the_timeout_of_a_database_tool_holds(self) -> None:
        tool = registry.get_static("list_datasets")
        old = tool.timeout
        tool.timeout = 0.3
        self.addCleanup(setattr, tool, "timeout", old)
        async def timed():
            started = time.perf_counter()
            out = await tool.run(_ctx(self.h, self.ws), {})
            return out, time.perf_counter() - started          # when the conversation gets its answer back
        with self.slow_list(1.5):
            out, took = asyncio.run(timed())
        self.assertIn("did not finish within", out.text)
        self.assertLess(took, 1.0)

    def test_offloaded_tools_are_read_only_and_local(self) -> None:
        offloaded = [t for t in registry.static_tools() if t.offload]
        self.assertGreaterEqual(len(offloaded), 15)
        for t in offloaded:
            self.assertFalse(t.needs_approval(), t.name)               # writes stay on the loop, approval-bound
            self.assertNotIn(t.category, {"mcp", "google", "accounting"}, t.name)          # no network clients
            self.assertNotIn(t.name, {"search_documents", "search_workspace", "inspect_file"})  # embeddings, storage


class HistoryBudget(unittest.TestCase):
    """AI-11."""

    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()
        self.tid = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]

    def seed(self, pairs: int, user_chars: int, answer_chars: int) -> None:
        ts = time.time() - 10_000
        rows = []
        for i in range(pairs):
            fact = "Our fiscal year ends on 31 March." if i == 0 else ""
            rows.append(("user", (fact + f" Question {i}. " + "u" * user_chars)[:max(user_chars, 60)], {}))
            rows.append(("assistant", f"Answer {i}: revenue line {i} reviewed. " + "a" * answer_chars, {}))
        for n, (role, content, payload) in enumerate(rows):
            self.h.engine.db.insert("messages", {"id": new_id("m_"), "thread_id": self.tid, "role": role,
                                                 "content": content, "payload": dumps(payload), "created_at": ts + n})

    def last_request(self) -> list[dict]:
        self.h.llm.script = ["Noted."]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{self.tid}/messages", json={"text": "And now?"})
        self.h.wait_run(self.tid, 20)
        return self.h.llm.calls[-1]["messages"]

    def test_history_stays_within_the_budget(self) -> None:
        self.seed(pairs=29, user_chars=20_000, answer_chars=8_000)      # 60-message window of maximum-size messages
        msgs = self.last_request()
        history_chars = sum(len(m.get("content") or "") for m in msgs if m["role"] != "system")
        from desk_engine.agent.harness import history_budget_chars
        self.assertLessEqual(history_chars, history_budget_chars() + 6_000)   # + the notes and the workspace context
        self.assertEqual(msgs[-1]["content"], "And now?")

    def test_dropped_turns_keep_the_users_facts_and_a_trace_of_the_answers(self) -> None:
        self.seed(pairs=29, user_chars=20_000, answer_chars=8_000)
        msgs = self.last_request()
        notes = "\n".join(m["content"] for m in msgs if m["role"] == "user" and m["content"].startswith("[Earlier"))
        self.assertIn("fiscal year ends on 31 March", notes)
        self.assertIn("Answer 0", notes)                             # the assistant's earlier answers, shortened
        self.assertIn("not re-checked", notes)

    def test_a_short_conversation_is_sent_whole(self) -> None:
        self.seed(pairs=3, user_chars=100, answer_chars=100)
        msgs = self.last_request()
        self.assertFalse(any(m["role"] == "user" and m["content"].startswith("[Earlier") for m in msgs))
        self.assertEqual(sum(1 for m in msgs if m["role"] == "user" and m["content"].startswith("Our fiscal")), 1)


class MetricsAndTrace(unittest.TestCase):
    """AI-12."""

    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()

    def turn(self) -> str:
        tid = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.llm.script = [[("calc_growth", {"values": [100, 120]})], "Growth was 20.00%."]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{tid}/messages", json={"text": "growth?"})
        self.h.wait_run(tid, 20)
        return tid

    def test_metrics_endpoint(self) -> None:
        self.turn()
        text = self.h.api("GET", "/api/admin/metrics")
        for line in ('desk_llm_calls_total{outcome="ok"} 2',
                     'desk_tool_calls_total{status="ok",tool="calc_growth"} 1',
                     'desk_agent_runs_total{outcome="completed"} 1'):
            self.assertIn(line, text)
        for name in ("desk_llm_call_seconds_bucket", "desk_llm_ttfb_seconds_count", "desk_tool_seconds_sum",
                     "desk_event_loop_lag_seconds"):
            self.assertIn(name, text)
        self.assertNotIn(self.ws, text)                               # nothing that names a tenant

    def test_metrics_need_the_instance_administrator(self) -> None:
        self.h.engine.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('instance_admin', ?)",
                                 (json.dumps("u_someone_else"),))
        self.h.api("GET", "/api/admin/metrics", expect=403)

    def test_failures_are_counted_by_class(self) -> None:
        tid = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.llm.script = [""]                                      # an empty reply
        self.h.api("POST", f"/api/w/{self.ws}/threads/{tid}/messages", json={"text": "hi"})
        self.h.wait_run(tid, 20)
        self.assertIn('desk_agent_runs_total{outcome="failed"} 1', self.h.api("GET", "/api/admin/metrics"))

    def test_run_trace(self) -> None:
        tid = self.turn()
        trace = self.h.api("GET", f"/api/w/{self.ws}/threads/{tid}/trace")
        kinds = [s["kind"] for s in trace["steps"]]
        self.assertEqual(kinds, ["model", "tool", "model"])
        model, tool = trace["steps"][0], trace["steps"][1]
        self.assertEqual(model["tool_calls"], ["calc_growth"])
        for k in ("ttfb_ms", "total_ms", "attempts", "finish"):
            self.assertIn(k, model)
        self.assertEqual((tool["tool"], tool["status"]), ("calc_growth", "ok"))
        self.assertIsInstance(tool["ms"], float)
        self.assertEqual(trace["run"]["outcome"], "completed")

    def test_cached_repeat_calls_are_counted(self) -> None:
        async def fast(ctx, a):
            return ToolOutput("Period changes: 20.00%", [], {})
        tool = registry.get_static("calc_growth")
        old = tool.handler
        tool.handler = fast
        self.addCleanup(setattr, tool, "handler", old)
        tid = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.llm.script = [[("calc_growth", {"values": [1, 2]})]] * 3 + ["Done."]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{tid}/messages", json={"text": "g"})
        self.h.wait_run(tid, 20)
        self.assertIn('desk_tool_calls_total{status="reused",tool="calc_growth"} 1',
                      self.h.api("GET", "/api/admin/metrics"))


if __name__ == "__main__":
    unittest.main()


class EveryModelReplyIsCheckedForCompleteness(unittest.TestCase):
    """AI-03 beyond the agent loop: the documents answer, the dashboard insights and the file reviewer also call the
    model, and a reply cut by the output limit or an empty one must not reach anyone as a complete answer."""

    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()

    def cut(self, text: str, finish: str = "length"):
        from desk_engine.llm.base import LLMResult, StreamEvent, Usage

        class Cut:
            async def stream(self, messages, tools, **kw):
                yield StreamEvent("text", text)
                yield StreamEvent("done", result=LLMResult(text, [], Usage(), finish_reason=finish))
        return Cut()

    def test_a_cut_dashboard_insight_is_refused(self) -> None:
        model = self.cut("- Revenue rose 12% to 1.2m, mainly because")
        with mock.patch("desk_engine.llm.registry.build_model", return_value=model), \
                mock.patch.object(type(self.h.engine.overview), "get", new=mock.AsyncMock(return_value={
                    "month": "2026-10", "currency": "GBP", "health": {}, "headline": {}, "kpis": [], "risks": [],
                    "insights": [], "provenance": {"kind": "ledger"}})):
            err = self.h.api("POST", f"/api/w/{self.ws}/overview/insights?month=2026-10&lang=en", json={}, expect=502)
        self.assertEqual(err["error"], "answer_incomplete")

    def test_a_cut_documents_answer_is_refused(self) -> None:
        model = self.cut("According to the policy [1], the limit is")
        with mock.patch("desk_engine.llm.registry.build_model", return_value=model):
            from desk_engine.knowledge.search import answer
            ws = self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,))
            with self.assertRaises(Exception) as caught:
                asyncio.run(answer(self.h.engine, ws, "u", "What is the limit?",
                                   {"documents": [{"title": "Policy", "snippet": "The limit is 5,000.", "page": 1}]}))
        self.assertEqual(getattr(caught.exception, "code", None), "answer_incomplete")

    def test_an_empty_documents_answer_is_refused(self) -> None:
        with mock.patch("desk_engine.llm.registry.build_model", return_value=self.cut("", "stop")):
            from desk_engine.knowledge.search import answer
            ws = self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,))
            with self.assertRaises(Exception) as caught:
                asyncio.run(answer(self.h.engine, ws, "u", "What is the limit?",
                                   {"documents": [{"title": "Policy", "snippet": "The limit is 5,000.", "page": 1}]}))
        self.assertEqual(getattr(caught.exception, "code", None), "answer_empty")

    def test_the_search_route_keeps_its_results_when_the_answer_is_cut(self) -> None:
        with mock.patch("desk_engine.llm.registry.build_model", return_value=self.cut("The limit is")), \
                mock.patch("desk_engine.knowledge.search.search_all", new=mock.AsyncMock(return_value={
                    "documents": [{"title": "Policy", "snippet": "The limit is 5,000.", "page": 1}]})):
            out = self.h.api("POST", f"/api/w/{self.ws}/search", json={"query": "limit", "answer": True})
        self.assertEqual(out["answer"]["code"], "answer_incomplete")
        self.assertEqual(out["answer"]["text"], "")
        self.assertTrue(out["results"]["documents"])

    def test_a_cut_review_is_not_recorded_as_done(self) -> None:
        from desk_engine.files.review import llm_review
        ws = self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,))
        with mock.patch("desk_engine.files.review.build_model", return_value=self.cut("The workbook looks")), \
                mock.patch("desk_engine.files.review.review_text", return_value="FILE: Budget (workbook)"):
            with self.assertRaises(Exception) as caught:
                asyncio.run(llm_review(self.h.engine, ws, {"sheets": []}, [], "u"))
        self.assertEqual(getattr(caught.exception, "code", None), "answer_incomplete")


class RedTeamProbes(unittest.TestCase):
    """Phase 6 probes for what this change set adds, and gaps the existing security suites do not cover. The model
    is scripted to do whatever an attacker wants (a fully compromised model): the controls must hold anyway."""

    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()

    def test_the_audit_log_is_append_only(self) -> None:
        import sqlite3
        self.h.api("GET", f"/api/w/{self.ws}/threads")
        self.h.engine.audit(self.ws, None, "probe", None, {})
        for sql in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.h.engine.db.execute(sql)

    def test_trace_of_another_workspace_is_not_found(self) -> None:
        tid = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.llm.script = ["Hello."]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{tid}/messages", json={"text": "hi"})
        self.h.wait_run(tid, 20)
        other = self.h.api("POST", "/api/workspaces", json={"name": "Other Ltd", "base_currency": "USD"})["id"]
        self.h.api("GET", f"/api/w/{other}/threads/{tid}/trace", expect=404)
        self.h.api("GET", f"/api/w/{self.ws}/threads/{tid}/trace?run=r_not_mine", expect=404)

    def test_no_read_tool_reveals_a_provider_key(self) -> None:
        secret = "sk-test"                                    # the provider key set by setup_workspace
        ctx = _ctx(self.h, self.ws)
        tools = asyncio.run(self.h.engine.tools.for_context(ctx))
        seen = []
        for t in tools.values():
            if t.needs_approval():
                continue
            out = asyncio.run(t.run(ctx, {}))
            seen.append(t.name)
            self.assertNotIn(secret, out.text + json.dumps(out.data, default=str), t.name)
        self.assertGreaterEqual(len(seen), 15)

    def test_offloaded_tools_never_mix_tenants_under_concurrency(self) -> None:
        from tests.test_financial_analysis import ledger_xlsx
        other = self.h.api("POST", "/api/workspaces", json={"name": "Beta Ltd", "base_currency": "USD"})["id"]
        for ws, name in ((self.ws, "alpha-ledger"), (other, "beta-ledger")):
            self.h.api("POST", f"/api/w/{ws}/cost-centres/ledger/upload",
                       files={"file": (f"{name}.xlsx", ledger_xlsx())})
        tool = registry.get_static("list_datasets")
        ctxs = {self.ws: _ctx(self.h, self.ws), other: _ctx(self.h, other)}

        async def many():
            calls = [(ws, tool.run(ctxs[ws], {})) for _ in range(20) for ws in ctxs]
            outs = await asyncio.gather(*(c for _, c in calls))
            return list(zip([w for w, _ in calls], outs, strict=True))
        for ws, out in asyncio.run(many()):
            mine, theirs = ("alpha", "beta") if ws == self.ws else ("beta", "alpha")
            self.assertIn(mine, out.text)
            self.assertNotIn(theirs, out.text)

    def test_metric_labels_stay_bounded(self) -> None:
        from desk_engine.metrics import tool_label
        self.assertEqual(tool_label("acme_crm__export_all_customers", "mcp"), "mcp")
        self.assertEqual(tool_label("calc_growth", "finance"), "calc_growth")


class BankMatching(unittest.TestCase):
    """The close's bank reconciliation: every pair that can be matched under the rule (same amount to the cent, dates
    at most five days apart) is matched, so no false exceptions are reported."""

    @staticmethod
    def rec(stmt, ledger):
        from desk_engine.close.statement import reconcile
        lines = [{"row": i, "date": d, "debit": max(a, 0), "credit": max(-a, 0), "entry": f"E{i}", "description": ""}
                 for i, d, a in ledger]
        st = {"lines": [{"id": f"s{i}", "date": d, "amount": a, "description": ""} for i, d, a in stmt]}
        return reconcile(st, lines, month="2026-10", month_end="2026-10-31", account={"code": "1000"},
                         book_balance=0.0, closing_balance=None)

    def test_nearest_first_does_not_cost_a_match(self) -> None:
        r = self.rec([(1, "2026-10-03", -100.0), (2, "2026-10-08", -100.0)],
                     [(1, "2026-10-01", -100.0), (2, "2026-10-04", -100.0)])
        self.assertEqual(r["matched"], 2)
        self.assertEqual((r["statement_only"], r["ledger_only"]), ([], []))
        pairs = {m["statement"]["id"]: m["ledger"]["id"] for m in r["matched_pairs"]}
        self.assertEqual(pairs, {"s1": "l1", "s2": "l2"})

    def test_the_rule_itself_is_unchanged(self) -> None:
        r = self.rec([(1, "2026-10-10", -50.0), (2, "2026-10-10", -70.0), (3, "2026-10-20", 30.0)],
                     [(1, "2026-10-12", -50.0), (2, "2026-10-03", -70.0), (3, "2026-10-20", 30.01),
                      (4, "2026-10-11", -50.0)])
        pairs = {m["statement"]["id"]: m["ledger"]["id"] for m in r["matched_pairs"]}
        self.assertEqual(pairs, {"s1": "l4"})          # nearest date wins; 7 days and one cent never match
        self.assertEqual(sorted(s["id"] for s in r["statement_only"]), ["s2", "s3"])
        self.assertEqual(sorted(b["id"] for b in r["ledger_only"]), ["l1", "l2", "l3"])

    def test_many_equal_amounts(self) -> None:
        stmt = [(i, f"2026-10-{1 + i % 28:02d}", -9.99) for i in range(3000)]
        ledger = [(i, f"2026-10-{1 + (i + 1) % 28:02d}", -9.99) for i in range(3000)]
        started = time.perf_counter()
        r = self.rec(stmt, ledger)
        self.assertEqual(r["matched"], 3000)
        self.assertLess(time.perf_counter() - started, 20)
