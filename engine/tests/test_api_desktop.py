import unittest

from tests.helpers import LOCAL_TOKEN, Harness


class DesktopApi(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(script=[
            [("calc_npv_irr", {"cashflows": [-1000, 300, 400, 500], "discount_rate": 0.1})],
            "The project's NPV at 10% is -21.04 and its IRR is 8.90%, so it falls short of a 10% hurdle.",
        ])

    def tearDown(self) -> None:
        self.h.close()

    def test_auth_and_host_guard(self) -> None:
        h = self.h
        c = h.client
        self.assertEqual(c.get("/api/session").json()["user"]["email"], "me@this-computer.local")
        r = c.get("/api/w/x/threads", headers={"authorization": "Bearer wrong"})
        self.assertEqual(r.status_code, 401)
        r = c.get("/health", headers={"host": "evil.example.com"})
        self.assertEqual(r.status_code, 403)  # DNS-rebinding guard
        r = c.get("/health")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])
        self.assertEqual(len(LOCAL_TOKEN), 40)

    def test_workspace_provider_and_chat_with_tool(self) -> None:
        h = self.h
        ws = h.setup_workspace()
        prov = h.api("GET", f"/api/w/{ws}/providers")
        self.assertEqual(prov["providers"][0]["models"], ["fake-large", "fake-model"])
        self.assertEqual(prov["default_model"], "fake-large")
        self.assertTrue(prov["providers"][0]["has_key"])
        self.assertNotIn("sk-test", str(prov))
        thread = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{thread['id']}/messages", json={"text": "NPV and IRR of -1000, 300, 400, 500?"})
        data = h.wait_run(thread["id"])
        self.assertEqual(data["run"]["status"], "done")
        roles = [m["role"] for m in data["messages"]]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        tool_msg = data["messages"][2]
        self.assertIn("IRR: 8.90%", tool_msg["content"])
        self.assertEqual(tool_msg["payload"]["blocks"][0]["type"], "kpis")
        final = data["messages"][3]
        self.assertEqual(final["payload"]["unverified_figures"], [])  # -21.04 and 8.90% came from the tool
        # the model saw the system prompt with the workspace profile and the skill index
        system = h.llm.calls[0]["messages"][0]["content"]
        self.assertIn("Base currency: GBP", system)
        self.assertIn("Fiscal year starts in April", system)
        self.assertIn("month-end-close", system)
        self.assertIn("calc_npv_irr", [t["function"]["name"] for t in h.llm.calls[0]["tools"]])
        # the second call carries the tool result
        self.assertEqual(h.llm.calls[1]["messages"][-1]["role"], "tool")
        # title taken from the first message; usage recorded
        threads = h.api("GET", f"/api/w/{ws}/threads")
        self.assertTrue(threads[0]["title"].startswith("NPV and IRR"))
        usage = h.api("GET", f"/api/w/{ws}/usage")
        self.assertEqual(usage["by_model"][0]["calls"], 2)

    def test_unverified_figures_flagged(self) -> None:
        h = self.h
        h.llm.script = ["Revenue last quarter was 1,234,567 and margin 42.5%."]
        ws = h.setup_workspace()
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "How did we do?"})
        data = h.wait_run(t["id"])
        self.assertEqual(data["messages"][-1]["payload"]["unverified_figures"], ["1,234,567", "42.5%"])

    def test_no_model_configured(self) -> None:
        h = self.h
        ws = h.setup_workspace(with_model=False)
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "hi"})
        data = h.wait_run(t["id"])
        self.assertEqual(data["run"]["status"], "error")
        self.assertIn("No language model", data["messages"][-1]["content"])

    def test_write_tool_needs_approval_then_resumes(self) -> None:
        h = self.h
        h.llm.script = [
            [("message_colleague", {"member": "me@this-computer.local", "message": "Please review the March accruals."})],
            "I've sent the message.",
        ]
        ws = h.setup_workspace()
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "Ask me to review accruals"})
        data = h.wait_run(t["id"])
        self.assertEqual(data["run"]["status"], "waiting_approval")
        approvals = h.api("GET", f"/api/w/{ws}/approvals")
        self.assertEqual(len(approvals), 1)
        self.assertIn("Please review the March accruals", approvals[0]["summary"])
        # a second message while waiting is refused
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "again"}, expect=409)
        h.api("POST", f"/api/w/{ws}/approvals/{approvals[0]['id']}/decide", json={"approve": True})
        data = h.wait_run(t["id"])
        self.assertEqual(data["run"]["status"], "done")
        tool = [m for m in data["messages"] if m["role"] == "tool"][0]
        self.assertEqual(tool["payload"]["status"], "ok")
        self.assertIn("Sent to", tool["content"])
        notes = h.api("GET", f"/api/w/{ws}/notifications")
        self.assertTrue(any("review the March accruals" in n["title"] for n in notes["items"]))
        audit = h.api("GET", f"/api/w/{ws}/audit")
        self.assertIn("approval.approved", [a["action"] for a in audit])
        self.assertIn("tool.run", [a["action"] for a in audit])

    def test_rejected_approval(self) -> None:
        h = self.h
        h.llm.script = [[("message_colleague", {"member": "me@this-computer.local", "message": "x"})],
                        "Understood, I won't send it."]
        ws = h.setup_workspace()
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "send"})
        h.wait_run(t["id"])
        appr = h.api("GET", f"/api/w/{ws}/approvals")[0]
        h.api("POST", f"/api/w/{ws}/approvals/{appr['id']}/decide", json={"approve": False})
        data = h.wait_run(t["id"])
        tool = [m for m in data["messages"] if m["role"] == "tool"][0]
        self.assertEqual(tool["payload"]["status"], "rejected")
        h.api("POST", f"/api/w/{ws}/approvals/{appr['id']}/decide", json={"approve": True}, expect=409)

    def test_invites_need_team_server(self) -> None:
        ws = self.h.setup_workspace(with_model=False)
        r = self.h.api("POST", f"/api/w/{ws}/invites", json={"email": "a@b.co"}, expect=403)
        self.assertEqual(r["error"], "needs_team_server")


if __name__ == "__main__":
    unittest.main()
