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


if __name__ == "__main__":
    unittest.main()
