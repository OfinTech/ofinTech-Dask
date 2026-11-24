import json
import sys
import unittest
from pathlib import Path

from tests.helpers import Harness

SERVER = str(Path(__file__).with_name("mcp_ledger_server.py"))
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


class MCPClientSide(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(script=[
            [("mcp_ledger__fx_rate", {"pair": "EURUSD"})],
            [("mcp_ledger__post_journal", {"memo": "FX revaluation", "amount": 125.5})],
            "Posted the revaluation at EURUSD 1.0850.",
        ])
        self.ws = self.h.setup_workspace()

    def tearDown(self) -> None:
        self.h.close()

    def test_stdio_server_tools_policies_and_approval(self) -> None:
        h = self.h
        srv = h.api("POST", f"/api/w/{self.ws}/mcp-servers", json={
            "name": "Ledger", "transport": "stdio", "command": sys.executable, "args": [SERVER]})
        self.assertIsNone(srv["last_error"], srv)
        tools = {t["name"]: t for t in srv["tools"]}
        self.assertTrue(tools["fx_rate"]["read_only"])
        self.assertFalse(tools["post_journal"]["read_only"])
        # allow the read-only tool to run unattended; 'auto' on a write tool is downgraded to 'ask'
        h.api("PATCH", f"/api/w/{self.ws}/mcp-servers/{srv['id']}",
              json={"tool_policy": {"fx_rate": "auto", "post_journal": "auto"}})
        catalog = {t["name"]: t for t in h.api("GET", f"/api/w/{self.ws}/tools")}
        self.assertEqual(catalog["mcp_ledger__fx_rate"]["effect"], "read")
        self.assertEqual(catalog["mcp_ledger__post_journal"]["effect"], "write")
        t = h.api("POST", f"/api/w/{self.ws}/threads", json={})
        h.api("POST", f"/api/w/{self.ws}/threads/{t['id']}/messages", json={"text": "Revalue the EUR balance"})
        data = h.wait_run(t["id"], timeout=30)
        self.assertEqual(data["run"]["status"], "waiting_approval", data["messages"])
        fx_msg = [m for m in data["messages"] if m["role"] == "tool"][0]
        self.assertIn("EURUSD = 1.0850", fx_msg["content"])
        appr = h.api("GET", f"/api/w/{self.ws}/approvals")[0]
        self.assertIn("post_journal", appr["summary"])
        h.api("POST", f"/api/w/{self.ws}/approvals/{appr['id']}/decide", json={"approve": True})
        data = h.wait_run(t["id"], timeout=30)
        self.assertEqual(data["run"]["status"], "done")
        tools_out = [m["content"] for m in data["messages"] if m["role"] == "tool"]
        self.assertIn("Posted journal 'FX revaluation' for 125.50", tools_out[-1])
        # turning a tool off hides it from the agent
        h.api("PATCH", f"/api/w/{self.ws}/mcp-servers/{srv['id']}", json={"tool_policy": {"post_journal": "off"}})
        catalog = {t["name"] for t in h.api("GET", f"/api/w/{self.ws}/tools")}
        self.assertNotIn("mcp_ledger__post_journal", catalog)

    def test_bad_command(self) -> None:
        h = self.h
        h.api("POST", f"/api/w/{self.ws}/mcp-servers", json={"name": "x", "transport": "stdio",
                                                              "command": "npx; rm -rf /"}, expect=422)
        srv = h.api("POST", f"/api/w/{self.ws}/mcp-servers", json={"name": "missing", "transport": "stdio",
                                                                    "command": "definitely-not-a-program-xyz"})
        self.assertIn("failed to start", srv["last_error"] or "")


class MCPServerSide(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace(with_model=False)

    def tearDown(self) -> None:
        self.h.close()

    def rpc(self, key: str | None, method: str, params: dict, rid: int = 1) -> object:
        headers = dict(HEADERS)
        headers["mcp-protocol-version"] = "2025-06-18"
        if key:
            headers["authorization"] = f"Bearer {key}"
        c = self.h.client
        saved = c.headers.pop("authorization", None)
        try:
            return c.post("/mcp", content=json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}),
                          headers=headers)
        finally:
            if saved:
                c.headers["authorization"] = saved

    def test_access_key_and_read_only_tools(self) -> None:
        r = self.rpc(None, "tools/list", {})
        self.assertEqual(r.status_code, 401)
        key = self.h.api("POST", f"/api/w/{self.ws}/mcp-keys", json={"name": "Claude Desktop"})
        self.assertTrue(key["key"].startswith("odk_"))
        self.assertEqual(key["endpoint"], "http://localhost:47821/mcp")
        init = self.rpc(key["key"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                    "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(init.status_code, 200, init.text)
        r = self.rpc(key["key"], "tools/list", {}, 2)
        self.assertEqual(r.status_code, 200, r.text)
        names = {t["name"] for t in r.json()["result"]["tools"]}
        self.assertIn("calc_npv_irr", names)
        self.assertIn("query_dataset", names)
        self.assertNotIn("message_colleague", names)  # write tools are never exposed
        self.assertNotIn("load_skill", names)
        self.assertNotIn("create_dashboard", names)
        r = self.rpc(key["key"], "tools/call", {"name": "calc_npv_irr",
                                                "arguments": {"cashflows": [-100, 60, 60], "discount_rate": 0.1}}, 3)
        text = r.json()["result"]["content"][0]["text"]
        self.assertIn("NPV at 10.00%: 4.13", text)
        r = self.rpc(key["key"], "tools/call", {"name": "message_colleague", "arguments": {}}, 4)
        self.assertTrue(r.json()["result"]["isError"] if "isError" in r.json()["result"] else r.json()["result"]["is_error"])
        # deleting the key revokes access
        self.h.api("DELETE", f"/api/w/{self.ws}/mcp-keys/{key['id']}", expect=None)
        self.assertEqual(self.rpc(key["key"], "tools/list", {}).status_code, 401)

    def test_a_workspace_with_a_ledger_offers_what_the_pages_compute_and_nothing_that_writes(self) -> None:
        """What an outside assistant can read is what the pages show: the statements and the tools built on them,
        the scorecard and the forecast. The list is written out so that a new tool is exposed on purpose."""
        from .test_cash_runway import BURNING, as_csv
        up = self.h.api("POST", f"/api/w/{self.ws}/imports", files={"file": ("gl.csv", as_csv(BURNING))})
        self.h.api("POST", f"/api/w/{self.ws}/imports/{up['upload_id']}/commit",
                   json={"name": "General ledger", "sheet": None, "header_row": 0})
        key = self.h.api("POST", f"/api/w/{self.ws}/mcp-keys", json={"name": "Outside assistant"})["key"]
        self.rpc(key, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "test", "version": "1"}})
        listed = self.rpc(key, "tools/list", {}, 2).json()["result"]["tools"]
        names = {t["name"] for t in listed}
        # bank_reconciliation joins these once a bank statement is imported beside the ledger
        from_the_ledger = {"financial_statements", "ageing_report", "ledger_anomalies", "profit_drivers",
                           "account_activity", "ledger_entries", "cash_runway"}
        scorecard = {"financial_kpis", "kpi_detail", "forecast_report", "month_end_close", "weekly_cash_forecast"}
        data = {"list_datasets", "describe_dataset", "query_dataset", "search_workspace"}
        calculators = {n for n in names if n.startswith("calc_")}
        self.assertEqual(names - calculators, from_the_ledger | scorecard | data)
        self.assertGreaterEqual(len(calculators), 12)
        for n in ("prepare_month_end_close", "create_dashboard", "sync_accounting", "message_colleague",
                  "propose_memory", "edit_file", "create_workbook", "load_skill", "more_tools"):
            self.assertNotIn(n, names)
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in listed))
        # the figure an outside assistant gets is the one the page and the product's own assistant give
        r = self.rpc(key, "tools/call", {"name": "cash_runway", "arguments": {}}, 3).json()["result"]
        self.assertIn("burn 30,000.00 a month, cash 150,000.00, which lasts 5.0 months and runs out in 2027-03",
                      r["content"][0]["text"])
        r = self.rpc(key, "tools/call", {"name": "financial_kpis", "arguments": {}}, 4).json()["result"]
        self.assertFalse(r.get("isError", r.get("is_error", False)))
        self.assertIn("Cash runway", r["content"][0]["text"])
        # a tool that starts something is refused by name, not only left off the list
        r = self.rpc(key, "tools/call", {"name": "prepare_month_end_close", "arguments": {}}, 5).json()["result"]
        self.assertTrue(r.get("isError", r.get("is_error", False)))
        self.assertIn("Unknown tool", r["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
