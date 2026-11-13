"""Observability for the release gates: what the engine exports, who may read it, and that it carries no customer
data. Every metric name used by the alert rules and dashboards in deploy/monitoring must exist here."""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from .helpers import Harness

MONITORING = Path(__file__).resolve().parents[2] / "deploy" / "monitoring"
TOKEN = "t" * 40


def exported_names(text: str) -> set[str]:
    return {line.split()[2] for line in text.splitlines() if line.startswith("# TYPE ")}


class Exported(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()

    def workload(self) -> str:
        h, ws = self.h, self.ws
        tid = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        h.llm.script = [[("calc_growth", {"values": [100, 120]})], "Growth was 20.00% on revenue of 1,234,567.89.",
                        ""]
        h.api("POST", f"/api/w/{ws}/threads/{tid}/messages", json={"text": "Our secret project Bluebird: growth?"})
        h.wait_run(tid, 20)
        tid2 = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        h.api("POST", f"/api/w/{ws}/threads/{tid2}/messages", json={"text": "hello"})
        h.wait_run(tid2, 20)
        h.client.get("/api/session", headers={"authorization": "Bearer wrong"})
        h.client.get(f"/api/w/{ws}/threads", headers={"authorization": "Bearer wrong"})    # 401
        other = h.api("POST", "/api/workspaces", json={"name": "Other", "base_currency": "USD"})["id"]
        h.engine.db.execute("DELETE FROM members WHERE workspace_id = ?", (other,))
        h.client.get(f"/api/w/{other}/threads")                                             # not a member
        return h.api("GET", "/api/admin/metrics")

    def test_every_layer_is_exported(self) -> None:
        text = self.workload()
        for line in ('desk_agent_runs_started_total 2', 'desk_agent_run_errors_total{code="answer_empty"} 1',
                     'desk_llm_finish_total{reason="stop"}', 'desk_llm_finish_total{reason="tool_calls"} 1',
                     'desk_llm_tokens_total{kind="input"}', 'desk_llm_tokens_total{kind="output"}',
                     'desk_answers_total{figures="unsupported"} 1', 'desk_figures_total{provenance="tool"}',
                     'desk_figures_total{provenance="unsupported"} 1',
                     'desk_http_requests_total{code="2xx",method="POST",route="/api/w/{ws}/threads"}',
                     'desk_auth_failures_total{route="/api/w/{ws}/threads"} 1', 'desk_workspace_rejections_total 1',
                     'desk_up 1', 'desk_agent_runs_active 0'):
            self.assertIn(line, text)
        for name in ("desk_agent_run_seconds_bucket", "desk_llm_request_chars_bucket",
                     "desk_llm_history_budget_ratio_bucket", "desk_db_query_seconds_bucket{kind=\"read\"",
                     "desk_db_query_seconds_bucket{kind=\"write\"", "desk_http_request_seconds_bucket",
                     "desk_db_connections ", "desk_process_resident_memory_bytes ", "desk_process_cpu_seconds_total ",
                     "desk_process_start_time_seconds "):
            self.assertIn(name, text)

    def test_no_customer_data_in_metrics(self) -> None:
        text = self.workload()
        user = self.h.engine.db.one("SELECT id, email, name FROM users LIMIT 1")
        threads = [r["id"] for r in self.h.engine.db.all("SELECT id FROM threads")]
        for secret in [self.ws, *threads, user["id"], "Bluebird", "1,234,567", "1234567", "sk-test", "llm.test"]:
            self.assertNotIn(secret, text)
        if user.get("email"):
            self.assertNotIn(user["email"], text)
        # label values are route templates, codes and closed vocabularies only
        for value in re.findall(r'="([^"]*)"', text):
            self.assertRegex(value, r"^[A-Za-z0-9_{}/.:+\-]*$")
            self.assertLess(len(value), 120)

    def test_cardinality_is_capped(self) -> None:
        from desk_engine.metrics import MAX_SERIES
        m = self.h.engine.metrics
        for i in range(MAX_SERIES + 50):
            m.inc("desk_http_requests_total", route=f"/r{i}", method="GET", code="2xx")
        text = m.render()
        self.assertEqual(text.count("desk_http_requests_total{"), MAX_SERIES)
        self.assertRegex(text, r"desk_metrics_dropped_series_total \d+")


class ScrapeAccess(unittest.TestCase):
    """Prometheus authenticates with DESK_METRICS_TOKEN; nobody else gets in, and the token opens nothing else."""

    def setUp(self) -> None:
        self.h = Harness("server")
        self.addCleanup(self.h.close)
        self.c = self.h.client

    def get(self, token: str | None, path: str = "/api/admin/metrics"):
        return self.c.get(path, headers={"authorization": f"Bearer {token}"} if token else {})

    def test_token(self) -> None:
        with mock.patch.dict(os.environ, {"DESK_METRICS_TOKEN": TOKEN}):
            self.assertEqual(self.get(TOKEN).status_code, 200)
            self.assertIn("desk_up 1", self.get(TOKEN).text)
            self.assertEqual(self.get("x" * 40).status_code, 401)
            self.assertEqual(self.get(None).status_code, 401)
            self.assertEqual(self.get(TOKEN, "/api/session").json().get("user"), None)    # opens nothing else
            self.assertIn(self.get(TOKEN, "/api/admin/users/u1/sign-out").status_code, (401, 403, 405))

    def test_short_or_missing_token_is_never_accepted(self) -> None:
        with mock.patch.dict(os.environ, {"DESK_METRICS_TOKEN": "short"}):
            self.assertEqual(self.get("short").status_code, 401)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DESK_METRICS_TOKEN", None)
            self.assertEqual(self.get("").status_code, 401)


class MonitoringConfig(unittest.TestCase):
    """The rules and dashboards under deploy/monitoring use only metrics the engine exports."""

    @classmethod
    def setUpClass(cls) -> None:
        h = Harness()
        try:
            h.setup_workspace()
            cls.names = exported_names(h.api("GET", "/api/admin/metrics"))
        finally:
            h.close()

    def referenced(self, text: str) -> set[str]:
        found = set(re.findall(r"\b(desk_[a-z_]+)", text))
        return {re.sub(r"_(bucket|sum|count)$", "", n) for n in found}

    def test_rules_and_dashboards_reference_real_metrics(self) -> None:
        import yaml
        files = [*(MONITORING / "prometheus").glob("*.yml"), *(MONITORING / "grafana" / "dashboards").glob("*.json")]
        self.assertGreaterEqual(len(files), 7)
        for f in files:
            text = f.read_text()
            if f.suffix == ".json":
                json.loads(text)
            else:
                yaml.safe_load(text)
            recorded = set(re.findall(r"record:\s*(\S+)", text))
            unknown = {n for n in self.referenced(text) if n not in self.names and not n.startswith("desk_slo")}
            unknown -= {r.split(":")[0] for r in recorded}
            self.assertFalse(unknown, f"{f.name}: {sorted(unknown)}")

    def test_every_alert_is_complete(self) -> None:
        import yaml
        rules = yaml.safe_load((MONITORING / "prometheus" / "alerts.yml").read_text())
        alerts = [r for g in rules["groups"] for r in g["rules"] if "alert" in r]
        self.assertGreaterEqual(len(alerts), 12)
        for a in alerts:
            self.assertIn(a["labels"]["severity"], {"critical", "warning", "info"}, a["alert"])
            self.assertTrue(a["annotations"].get("summary") and a["annotations"].get("runbook"), a["alert"])
            # an alert message is built from labels the engine exports: no workspace, user or amount can appear
            self.assertNotRegex(json.dumps(a["annotations"]), r"\$labels\.(workspace|user|thread|email|account)")


if __name__ == "__main__":
    unittest.main()


class AlertSignals(unittest.TestCase):
    """Each alert's signal moves when its failure is injected (the rule evaluation itself needs Prometheus, which this
    test does not run; see reports/ai-agent-final-release/03_ALERTING_VALIDATION.md)."""

    URL = "https://llm.test/v1/chat/completions"

    def setUp(self) -> None:
        import httpx2 as httpx
        self.httpx = httpx
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace()
        self.env = mock.patch.dict(os.environ, {"DESK_LLM_RETRY_BASE_S": "0.01", "DESK_LLM_RETRY_MAX_WAIT_S": "0.02"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def turn(self) -> dict:
        t = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        self.h.api("POST", f"/api/w/{self.ws}/threads/{t}/messages", json={"text": "How did October go?"})
        return self.h.wait_run(t, 20)

    def metrics(self) -> str:
        return self.h.api("GET", "/api/admin/metrics")

    def test_model_unavailable(self) -> None:          # DeskModelUnavailable, DeskAgentErrorRate
        self.h.router.add("POST", self.URL, lambda r: self.httpx.Response(503, text="overloaded"))
        self.turn()
        text = self.metrics()
        self.assertIn('desk_llm_calls_total{outcome="llm_unavailable"} 1', text)
        self.assertIn('desk_llm_retries_total{code="llm_unavailable"} 2', text)
        self.assertRegex(text, r'desk_agent_runs_total\{outcome="failed"\} 1')

    def test_model_timeout(self) -> None:              # DeskModelTimeouts
        def slow(r):
            raise self.httpx.ReadTimeout("timed out", request=r)
        self.h.router.add("POST", self.URL, slow)
        self.turn()
        self.assertIn('desk_llm_calls_total{outcome="llm_timeout"} 1', self.metrics())

    def test_recovery_after_an_outage(self) -> None:   # the ratio falls back once calls succeed again
        self.h.router.add("POST", self.URL, lambda r: self.httpx.Response(503, text="down"))
        self.turn()
        from .helpers import FakeLLM
        self.h.router.add("POST", self.URL, FakeLLM(self.h.router, ["Back."]).handle)
        for _ in range(3):
            self.turn()
        text = self.metrics()
        self.assertIn('desk_llm_calls_total{outcome="ok"} 3', text)
        self.assertIn('desk_agent_runs_total{outcome="completed"} 3', text)

    def test_tool_and_financial_failures(self) -> None:   # DeskToolFailures, DeskFinancialCalculationFailures
        from desk_engine.agent.tools import registry

        async def broken(ctx, a):
            raise RuntimeError("database is locked")
        tool = registry.get_static("calc_growth")
        old = tool.handler
        tool.handler = broken
        self.addCleanup(setattr, tool, "handler", old)
        self.h.llm.script = [[("calc_growth", {"values": [1, 2]})], "It failed."]
        self.turn()
        self.assertIn('desk_tool_calls_total{status="error",tool="calc_growth"} 1', self.metrics())

    def test_api_server_errors(self) -> None:           # DeskApi5xxHigh, availability burn rate
        t = self.h.api("POST", f"/api/w/{self.ws}/threads", json={})["id"]
        with mock.patch.object(type(self.h.engine.agent), "approval_out", side_effect=RuntimeError("boom")), \
                mock.patch.object(type(self.h.engine.agent), "run_out", side_effect=RuntimeError("boom")):
            self.h.engine.db.execute("INSERT INTO agent_runs (id, workspace_id, thread_id, user_id, status, "
                                     "created_at, updated_at) VALUES ('r_x', ?, ?, 'u', 'done', 1, 1)", (self.ws, t))
            r = self.h.client.get(f"/api/w/{self.ws}/threads/{t}")
        self.assertEqual(r.status_code, 500)
        self.assertIn('desk_http_requests_total{code="5xx",method="GET",route="/api/w/{ws}/threads/{tid}"} 1',
                      self.metrics())

    def test_security_events(self) -> None:             # DeskPaymentOrSealedRefusals
        import asyncio

        from desk_engine.agent.tools import Tool, ToolContext, ToolOutput
        ws = self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,))
        ctx = ToolContext(self.h.engine, ws, self.h.engine.db.scalar("SELECT id FROM users LIMIT 1"))

        async def pay(c, a):
            return ToolOutput("paid")
        t = Tool("initiate_payment", "Send a bank transfer to a beneficiary", {"type": "object", "properties": {}},
                 pay, "write", "mcp")
        out = asyncio.run(t.run(ctx, {}))
        self.assertTrue(out.text.startswith("Refused"))
        self.assertIn('desk_tool_refusals_total{reason="payment"} 1', self.metrics())
