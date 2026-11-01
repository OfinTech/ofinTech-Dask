"""Embedded gateway: AG-UI event sequence validity, input validation, component payload validation, cancellation,
re-attach, audit of a run (threats T11, T14, T17, T19)."""

from __future__ import annotations

import json
import threading
import time
import unittest

from desk_engine.embedded import agui, components
from tests.embedded_helpers import assert_valid_sequence, audit_actions, last, make_org, new_harness, run_input, stream, types


class EventSequence(unittest.TestCase):
    def setUp(self) -> None:
        self.h = new_harness(script=[[("calc_growth", {"values": [100, 120]})], "Revenue grew by 20%, from 100 to 120."])
        self.org = make_org(self.h, email="o@acme.test")

    def tearDown(self) -> None:
        self.h.close()

    def test_tool_run_sequence(self) -> None:
        events = stream(self.org, run_input("How did revenue grow?", run="r1"))
        assert_valid_sequence(events)
        t = types(events)
        self.assertEqual(t[:2], ["RUN_STARTED", "STATE_SNAPSHOT"])
        self.assertIn("TOOL_CALL_START", t)
        self.assertEqual(t.index("TOOL_CALL_START") + 1, t.index("TOOL_CALL_ARGS"))
        self.assertEqual(t.index("TOOL_CALL_ARGS") + 1, t.index("TOOL_CALL_END"))
        self.assertLess(t.index("TOOL_CALL_END"), t.index("TOOL_CALL_RESULT"))
        self.assertEqual(t.count("TOOL_CALL_START"), 1)   # announced once, not again on tool_start
        start = events[0]
        self.assertEqual((start["threadId"], start["runId"]), ("thread-1", "r1"))
        self.assertTrue(start["metadata"]["ofintech"]["desk_run_id"].startswith("run_"))
        self.assertEqual(start["metadata"]["ofintech"]["agent_id"], "finance-copilot")
        fin = last(events)
        self.assertEqual(fin["outcome"], {"type": "success"})
        self.assertIn("20%", fin["result"]["text"])
        self.assertEqual(set(fin["usage"]), {"input_tokens", "output_tokens"})
        # the args frame carries the full JSON arguments the model sent
        args = next(e for e in events if e["type"] == "TOOL_CALL_ARGS")
        self.assertEqual(json.loads(args["delta"]), {"values": [100, 120]})

    def test_plain_answer_and_state_echo(self) -> None:
        self.h.llm.script[:] = ["A plain answer with no tools."]
        events = stream(self.org, run_input("Hello", run="r1", state={"page": "dashboard", "filters": {"q": 1}}))
        assert_valid_sequence(events)
        self.assertEqual(events[1], {**events[1], "type": "STATE_SNAPSHOT", "snapshot": {"page": "dashboard", "filters": {"q": 1}}})
        content = "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
        self.assertEqual(content, "A plain answer with no tools.")
        self.assertEqual(last(events)["result"]["text"], content)

    def test_table_result_becomes_component(self) -> None:
        self.h.llm.script[:] = [[("calc_loan_schedule", {"principal": 1000, "annual_rate": 0.05, "periods": 2})], "Done."]
        events = stream(self.org, run_input("Loan schedule please", run="r1", thread="loan"))
        assert_valid_sequence(events)
        comps = [e["value"] for e in events if e["type"] == "CUSTOM" and e["name"] == "ofintech.component"]
        self.assertTrue(comps, types(events))
        names = {c["component"] for c in comps}
        self.assertTrue(names & {"FinancialDataTable", "FinancialKpiCard"}, names)
        for c in comps:
            self.assertEqual(c["version"], 1)
            self.assertEqual(components.validate(c["component"], c["props"]), [], c)
            self.assertTrue(c["toolCallId"])

    def test_input_validation(self) -> None:
        h, org = self.h, self.org
        bad = [
            ({"threadId": "t", "runId": "r", "messages": []}, "empty_message"),
            ({"threadId": "", "runId": "r", "messages": [{"role": "user", "content": "x"}]}, None),
            ({"threadId": "has space", "runId": "r", "messages": [{"role": "user", "content": "x"}]}, None),
            ({"threadId": "t", "runId": "r", "messages": [{"role": "user", "content": [{"type": "image", "url": "x"}]}]}, None),
            ({"threadId": "t", "runId": "r", "messages": [{"role": "user", "content": "x" * 9000}]}, "message_too_long"),
        ]
        for body, code in bad:
            r = h.client.post("/api/embedded/agents/finance-copilot/run", headers=org.page(), json=body)
            self.assertEqual(r.status_code, 422, (body.get("threadId"), r.text[:200]))
            if code:
                self.assertEqual(r.json()["error"], code)
        r = h.client.post("/api/embedded/agents/nope/run", headers=org.page(), json=run_input("x"))
        self.assertEqual(r.status_code, 404)
        r = h.client.post("/api/embedded/agents/finance-copilot/run", headers={**org.page(), "content-length": str(300 * 1024)},
                          content=b"{}")
        self.assertIn(r.status_code, (400, 413, 422))

    def test_run_audited_start_and_finish(self) -> None:
        stream(self.org, run_input("How did revenue grow?", run="r1"))
        rows = audit_actions(self.h, "embedded.run.")
        self.assertEqual([r["action"] for r in rows], ["embedded.run.start", "embedded.run.finish"])
        self.assertEqual(rows[1]["result"], "done")
        self.assertIn('"tool_calls":1', rows[1]["detail"].replace(" ", ""))
        # the request id correlates both entries and the x-request-id header
        req_ids = self.h.engine.db.all("SELECT request_id FROM audit_log WHERE action LIKE 'embedded.run.%'")
        self.assertEqual(len({r["request_id"] for r in req_ids}), 1)


class Lifecycle(unittest.TestCase):
    def tearDown(self) -> None:
        self.h.close()

    def test_disconnect_cancels_run(self) -> None:
        # a model that never answers: the client gives up and the engine must stop the run
        import httpx2 as httpx
        from tests.helpers import Router

        gate = threading.Event()

        def slow(request: httpx.Request) -> httpx.Response:
            gate.wait(5)
            return httpx.Response(500, json={"error": "gave up"})

        router = Router()
        self.h = new_harness(script=[], router=router)
        router.add("POST", "https://llm.test/v1/chat/completions", slow)
        org = make_org(self.h, email="o@acme.test")
        with self.h.client.stream("POST", "/api/embedded/agents/finance-copilot/run", headers=org.page(), json=run_input("x")) as resp:
            self.assertEqual(resp.status_code, 200)
            first = next(resp.iter_lines())
            self.assertIn("RUN_STARTED", first)
        gate.set()
        for _ in range(100):
            run = self.h.engine.db.one("SELECT status, error FROM agent_runs")
            if run["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(run["status"], "error", run)
        # the usage row is written when the cancelled stream generator is closed, a moment after the disconnect
        usage = None
        for _ in range(100):
            usage = self.h.engine.db.one("SELECT status FROM embedded_usage")
            if usage:
                break
            time.sleep(0.05)
        self.assertIsNotNone(usage, "no usage row after the disconnect")
        self.assertIn(usage["status"], {"cancelled", "error"})

    def test_cancel_endpoint_and_reattach_replay(self) -> None:
        self.h = new_harness(script=[[("calc_growth", {"values": [1, 2]})], "Doubled."])
        org = make_org(self.h, email="o@acme.test")
        events = stream(org, run_input("Growth?", run="r1"))
        desk_run = events[0]["metadata"]["ofintech"]["desk_run_id"]
        # cancelling a finished run is a no-op that still answers 204 (idempotent for clients)
        r = self.h.client.post(f"/api/embedded/agents/finance-copilot/runs/{desk_run}/cancel", headers=org.page())
        self.assertEqual(r.status_code, 204)
        with self.h.client.stream("GET", f"/api/embedded/agents/finance-copilot/runs/{desk_run}/events?threadId=thread-1&runId=r1",
                                  headers=org.page()) as resp:
            self.assertEqual(resp.status_code, 200)
            replay = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
        assert_valid_sequence(replay)
        self.assertEqual(last(replay)["result"]["text"], "Doubled.")
        self.assertEqual([e for e in types(replay) if e.startswith("TOOL_CALL")],
                         ["TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"])
        # an unknown run id is 404, not 401/500
        r = self.h.client.get("/api/embedded/agents/finance-copilot/runs/run_nope/events", headers=org.page())
        self.assertEqual(r.status_code, 404)


class ComponentRegistry(unittest.TestCase):
    def test_component_payload_rejects_unknown_props(self) -> None:
        self.assertIsNone(components.payload("FinancialKpiCard", {"label": "x", "value": 1, "html": "<b>hi</b>"}))
        self.assertIsNone(components.payload("FinancialKpiCard", {"label": "x", "value": "not a number"}))
        self.assertIsNone(components.payload("FinancialSourceReference", {"items": [{"n": 1, "name": "a", "href": "javascript:alert(1)"}]}))
        self.assertIsNone(components.payload("Nope", {"a": 1}))
        ok = components.payload("FinancialKpiCard", {"label": "Revenue", "value": 10.5, "format": "money", "currency": "SAR"})
        self.assertEqual(ok["props"]["version"], 1)
        self.assertEqual(components.validate("FinancialKpiCard", {**ok["props"], "version": 2}), ["version must be one of: 1."])

    def test_block_mapping_by_structure(self) -> None:
        table = {"type": "table", "title": "Monthly", "columns": [{"key": "month", "label": "Month"}, {"key": "revenue", "label": "Revenue", "format": "money"},
                                                                    {"key": "expenses", "label": "Expenses", "format": "money"}],
                 "rows": [["2026-01", 100, 80], ["2026-02", 120, 90]]}
        out = components.from_block(table, currency="SAR")
        self.assertEqual([c["component"] for c in out], ["RevenueExpenseChart", "FinancialDataTable"])
        self.assertEqual(out[0]["props"]["points"][1], {"period": "2026-02", "revenue": 120.0, "expenses": 90.0})
        invoices = {"type": "table", "title": "Customer documents",
                    "columns": [{"key": "number", "label": "Number"}, {"key": "party", "label": "Customer"}, {"key": "due", "label": "Due"},
                                {"key": "currency", "label": "Cur."}, {"key": "balance", "label": "Balance"}, {"key": "days", "label": "Days overdue"}],
                    "rows": [["INV-1", "Acme", "2026-01-31", "SAR", 500, 12]]}
        out = components.from_block(invoices, tool_name="list_invoices")
        self.assertEqual(out[0]["component"], "UnmatchedTransactions")
        self.assertEqual(out[0]["props"]["items"][0]["days_overdue"], 12.0)
        sources = {"type": "sources", "items": [{"n": 1, "name": "Policy.pdf", "page": 3, "snippet": "x", "link": "https://evil.test"}]}
        out = components.from_block(sources)
        self.assertEqual(out[0]["component"], "FinancialSourceReference")
        self.assertNotIn("link", out[0]["props"]["items"][0])   # links are not forwarded to the host page
        self.assertEqual(components.from_block({"type": "html", "html": "<script>"}), [])
        self.assertEqual(components.from_block("garbage"), [])

    def test_schemas_match_sdk_copies(self) -> None:
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "sdk" / "embedded-agent" / "schemas" / "v1"
        if not root.exists():
            self.skipTest("SDK schemas not present in this checkout")
        for name, schema in components.SCHEMAS.items():
            on_disk = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk, schema, name)

    def test_event_type_strings_are_the_published_ones(self) -> None:
        published = {"RUN_STARTED", "RUN_FINISHED", "RUN_ERROR", "STEP_STARTED", "STEP_FINISHED", "TEXT_MESSAGE_START",
                     "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END",
                     "TOOL_CALL_RESULT", "STATE_SNAPSHOT", "CUSTOM"}
        used = {getattr(agui, n) for n in dir(agui) if n.isupper() and isinstance(getattr(agui, n), str)
                and getattr(agui, n).isupper() and "_" in getattr(agui, n)}
        self.assertTrue(used <= published, used - published)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
