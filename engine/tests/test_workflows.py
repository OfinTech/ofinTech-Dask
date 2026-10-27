import unittest
from datetime import UTC, datetime

from desk_engine.errors import Invalid
from desk_engine.workflows.cron import Cron
from desk_engine.workflows.engine import compare
from desk_engine.workflows.template import render
from tests.helpers import Harness


class Units(unittest.TestCase):
    def test_cron(self) -> None:
        start = datetime(2026, 10, 26, 9, 30, tzinfo=UTC)  # a Monday
        self.assertEqual(Cron("0 8 * * MON").next_after(start, "UTC"), datetime(2026, 11, 2, 8, 0, tzinfo=UTC))
        self.assertEqual(Cron("*/15 * * * *").next_after(start, "UTC").minute, 45)
        self.assertEqual(Cron("0 9 1 * *").next_after(start, "UTC").date().isoformat(), "2026-11-01")
        nxt = Cron("0 8 * * *").next_after(start, "Asia/Riyadh")
        self.assertEqual((nxt.hour, nxt.utcoffset().total_seconds()), (8, 3 * 3600))
        self.assertEqual(Cron("0 0 L * *".replace("L", "31")).next_after(start, "UTC").day, 31)
        with self.assertRaises(Invalid):
            Cron("61 * * * *")
        with self.assertRaises(Invalid):
            Cron("* * *")

    def test_template_and_compare(self) -> None:
        ctx = {"steps": {"a": {"data": {"npv": 12.5, "rows": [1, 2]}, "output": "text"}}, "trigger": {"text": "hi"}}
        self.assertEqual(render("{{ steps.a.data.npv }}", ctx), 12.5)  # keeps the type
        self.assertEqual(render("NPV {{steps.a.data.npv}} for {{trigger.text}}", ctx), "NPV 12.5 for hi")
        self.assertEqual(render({"x": ["{{steps.a.data.rows}}"]}, ctx), {"x": [[1, 2]]})
        self.assertIsNone(render("{{__class__.__mro__}}", ctx))
        self.assertEqual(render("x{{__class__.__mro__}}", ctx), "x")
        self.assertTrue(compare("1,200", "gt", "1000"))
        self.assertFalse(compare("abc", "gt", "1"))
        self.assertTrue(compare("Overdue: 3", "contains", "overdue"))
        self.assertTrue(compare(None, "empty", ""))


class WorkflowRuns(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness(script=["Collections look healthy: 2 invoices are overdue."])
        self.ws = self.h.setup_workspace()

    def tearDown(self) -> None:
        self.h.close()

    def create(self, steps: list, trigger: dict | None = None, enabled: bool = True) -> dict:
        return self.h.api("POST", f"/api/w/{self.ws}/workflows", json={
            "name": "Weekly check", "definition": {"trigger": trigger or {"type": "manual"}, "steps": steps},
            "enabled": enabled})

    def start_run(self, wf: dict, text: str = "") -> str:
        return self.h.api("POST", f"/api/w/{self.ws}/workflows/{wf['id']}/run", json={"input": text})["run_id"]

    def test_tool_condition_notify(self) -> None:
        wf = self.create([
            {"id": "calc", "name": "Project NPV", "type": "tool", "tool": "calc_npv_irr",
             "arguments": {"cashflows": [-1000, 600, 600], "discount_rate": 0.05}},
            {"id": "check", "name": "Positive?", "type": "condition", "left": "{{steps.calc.data.npv}}", "op": "gt",
             "right": "0"},
            {"id": "tell", "name": "Tell me", "type": "notify", "channels": ["in_app"], "recipients": "me",
             "message": "NPV is {{steps.calc.data.npv}} ({{trigger.text}})"}])
        run = self.h.wait_workflow(self.start_run(wf, "from test"))
        self.assertEqual(run["status"], "done", run)
        self.assertEqual([x["step"] for x in run["log"]], ["calc", "check", "tell"])
        notes = self.h.api("GET", f"/api/w/{self.ws}/notifications")["items"]
        self.assertTrue(notes[0]["title"].startswith("Weekly check: NPV is 115.6"))
        # condition false -> stopped
        wf2 = self.create([
            {"id": "calc", "name": "NPV", "type": "tool", "tool": "calc_npv_irr",
             "arguments": {"cashflows": [-1000, 100], "discount_rate": 0.05}},
            {"id": "check", "name": "Positive?", "type": "condition", "left": "{{steps.calc.data.npv}}", "op": "gt",
             "right": "0"},
            {"id": "tell", "name": "Tell", "type": "notify", "message": "never"}])
        run = self.h.wait_workflow(self.start_run(wf2))
        self.assertEqual(run["status"], "stopped")

    def test_approval_step_and_agent_step(self) -> None:
        wf = self.create([
            {"id": "ask", "name": "Review AR", "type": "agent", "prompt": "Review receivables for {{workspace.name}}.",
             "skills": ["ar-collections"]},
            {"id": "ok", "name": "Controller sign-off", "type": "approval", "message": "Send summary? {{steps.ask.output}}"},
            {"id": "tell", "name": "Notify", "type": "notify", "message": "{{steps.ask.output}}"}])
        rid = self.start_run(wf)
        run = self.h.wait_workflow(rid, {"waiting_approval", "failed", "done"})
        self.assertEqual(run["status"], "waiting_approval", run)
        self.assertEqual(run["context"]["steps"]["ask"]["output"], "Collections look healthy: 2 invoices are overdue.")
        # the agent got the rendered prompt and the pinned skill
        system = self.h.llm.calls[0]["messages"][0]["content"]
        self.assertIn("Pinned skill 'ar-collections'", system)
        self.assertEqual(self.h.llm.calls[0]["messages"][-1]["content"], "Review receivables for Acme Ltd.")
        appr = self.h.api("GET", f"/api/w/{self.ws}/approvals")[0]
        self.assertEqual(appr["source"], "workflow")
        self.assertIn("2 invoices are overdue", appr["summary"])
        self.h.api("POST", f"/api/w/{self.ws}/approvals/{appr['id']}/decide", json={"approve": True})
        run = self.h.wait_workflow(rid, {"done", "failed"})
        self.assertEqual(run["status"], "done", run)

    def test_write_tool_step_needs_approval_and_reject(self) -> None:
        wf = self.create([
            {"id": "msg", "name": "Ping", "type": "tool", "tool": "message_colleague",
             "arguments": {"member": "me@this-computer.local", "message": "Close starts tomorrow"}},
            {"id": "tell", "name": "Done", "type": "notify", "message": "sent"}])
        rid = self.start_run(wf)
        run = self.h.wait_workflow(rid)
        self.assertEqual(run["status"], "waiting_approval")
        appr = self.h.api("GET", f"/api/w/{self.ws}/approvals")[0]
        self.assertIn("Close starts tomorrow", appr["summary"])
        self.h.api("POST", f"/api/w/{self.ws}/approvals/{appr['id']}/decide", json={"approve": False})
        self.assertEqual(self.h.wait_workflow(rid, {"rejected"})["status"], "rejected")
        rid = self.start_run(wf)
        self.h.wait_workflow(rid)
        appr = self.h.api("GET", f"/api/w/{self.ws}/approvals")[0]
        self.h.api("POST", f"/api/w/{self.ws}/approvals/{appr['id']}/decide", json={"approve": True})
        run = self.h.wait_workflow(rid, {"done", "failed"})
        self.assertEqual(run["status"], "done", run)
        self.assertIn("Sent to", run["context"]["steps"]["msg"]["output"])

    def test_schedule_trigger_and_validation(self) -> None:
        h = self.h
        r = h.api("POST", f"/api/w/{self.ws}/workflows", json={"name": "x", "definition": {
            "trigger": {"type": "schedule", "cron": "99 * * * *"}, "steps": [
                {"id": "a", "name": "a", "type": "notify", "message": "m"}]}}, expect=422)
        self.assertEqual(r["error"], "bad_cron")
        h.api("POST", f"/api/w/{self.ws}/workflows", json={"name": "x", "definition": {"steps": [
            {"id": "a", "name": "a", "type": "tool", "tool": "rm_rf"}]}}, expect=422)
        wf = self.create([{"id": "a", "name": "Ping", "type": "notify", "message": "scheduled hello"}],
                         {"type": "schedule", "cron": "0 8 * * MON"})
        self.assertIsNotNone(wf["next_run_at"])
        h.engine.db.execute("UPDATE workflows SET next_run_at = 1 WHERE id = ?", (wf["id"],))
        h.client.portal.call(h.engine.workflows.tick)
        detail = h.api("GET", f"/api/w/{self.ws}/workflows/{wf['id']}")
        self.assertEqual(detail["runs"][0]["trigger"]["type"], "schedule")
        self.assertGreater(detail["workflow"]["next_run_at"], 1)
        h.wait_workflow(detail["runs"][0]["id"])

    def test_drafts_save_incomplete_but_cannot_run_or_switch_on(self) -> None:
        h, ws = self.h, self.ws
        # exactly what "New workflow" in the app sends
        draft = self.create([{"id": "step1", "name": "Ask the assistant", "type": "agent", "prompt": ""}], enabled=False)
        url = f"/api/w/{ws}/workflows/{draft['id']}"
        body = {"name": "Draft", "description": "", "enabled": False, "definition": {"trigger": {"type": "manual"}, "steps": [
            {"id": "step1", "name": "Ask the assistant", "type": "agent", "prompt": ""},
            {"id": "step2", "name": "", "type": "notify", "message": ""}]}}
        h.api("PUT", url, json=body)  # saving a draft works
        r = h.api("PUT", url, json={**body, "enabled": True}, expect=422)
        self.assertEqual(r["error"], "workflow_incomplete")
        self.assertIn("Step 1 “Ask the assistant”: write what the assistant should do.", r["message"])
        self.assertEqual([d["field"] for d in r["details"]], ["steps.0.prompt", "steps.1.name", "steps.1.message"])
        r = h.api("POST", f"{url}/run", json={"input": ""}, expect=422)
        self.assertIn("before you run it", r["message"])
        body["definition"]["steps"][0]["prompt"] = "Summarise overdue invoices."
        body["definition"]["steps"][1].update(name="Tell me", message="{{steps.step1.output}}")
        self.assertTrue(h.api("PUT", url, json={**body, "enabled": True})["enabled"])
        h.wait_workflow(h.api("POST", f"{url}/run", json={"input": ""})["run_id"])

    def test_tools_catalog(self) -> None:
        tools = self.h.api("GET", f"/api/w/{self.ws}/tools")
        names = {t["name"]: t for t in tools}
        self.assertEqual(names["message_colleague"]["effect"], "write")
        self.assertEqual(names["calc_npv_irr"]["effect"], "read")
        self.assertNotIn("gmail_search", names)  # only offered once Google is connected


if __name__ == "__main__":
    unittest.main()
