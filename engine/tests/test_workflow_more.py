"""Workflow improvements: webhook trigger, file steps, retries, run timeline, templates, schedule preview."""

from __future__ import annotations

import io
import unittest

from tests.helpers import Harness

from .test_user_files import template_docx


class WorkflowMore(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()

    def tearDown(self) -> None:
        self.h.close()

    def create(self, steps: list, trigger: dict | None = None, enabled: bool = True) -> dict:
        return self.h.api("POST", f"/api/w/{self.ws}/workflows", json={
            "name": "Flow", "definition": {"trigger": trigger or {"type": "manual"}, "steps": steps}, "enabled": enabled})

    def run_wf(self, wf: dict) -> dict:
        rid = self.h.api("POST", f"/api/w/{self.ws}/workflows/{wf['id']}/run", json={})["run_id"]
        return self.h.wait_workflow(rid)

    def test_webhook_trigger(self) -> None:
        h, ws = self.h, self.ws
        wf = self.create([{"id": "tell", "name": "Tell", "type": "notify", "recipients": "me",
                           "message": "Invoice {{trigger.data.invoice}} for {{trigger.data.amount}}"}],
                         {"type": "webhook"})
        url = f"/api/hooks/workflows/{wf['id']}"
        self.assertEqual(h.client.post(url, json={"invoice": "X"}).status_code, 401)  # no secret yet
        hook = h.api("POST", f"/api/w/{ws}/workflows/{wf['id']}/webhook")
        self.assertTrue(hook["secret"].startswith("dwh_"))
        self.assertTrue(hook["url"].endswith(url))
        self.assertTrue(h.api("GET", f"/api/w/{ws}/workflows/{wf['id']}")["workflow"]["webhook"])
        anon = h.client.__class__(h.app, base_url="http://localhost")  # another system: no session, no cookies
        self.assertEqual(anon.post(url, json={}, headers={"authorization": "Bearer dwh_wrong"}).status_code, 401)
        r = anon.post(url, json={"invoice": "INV-77", "amount": 1250}, headers={"authorization": f"Bearer {hook['secret']}"})
        self.assertEqual(r.status_code, 202, r.text)
        run = h.wait_workflow(r.json()["run_id"])
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["trigger"]["type"], "webhook")
        notes = h.api("GET", f"/api/w/{ws}/notifications")["items"]
        self.assertEqual(notes[0]["title"], "Flow: Invoice INV-77 for 1250")
        self.assertEqual(anon.post(url, content=b"x" * (300 * 1024),
                                   headers={"authorization": f"Bearer {hook['secret']}"}).status_code, 413)
        # switching it off refuses events; removing the secret stops them entirely
        h.api("PUT", f"/api/w/{ws}/workflows/{wf['id']}", json={
            "name": "Flow", "definition": wf["definition"], "enabled": False})
        self.assertEqual(anon.post(url, json={}, headers={"authorization": f"Bearer {hook['secret']}"}).status_code, 409)
        h.api("DELETE", f"/api/w/{ws}/workflows/{wf['id']}/webhook", expect=None)
        self.assertEqual(anon.post(url, json={}, headers={"authorization": f"Bearer {hook['secret']}"}).status_code, 401)
        # a manual workflow cannot get a webhook secret
        manual = self.create([{"id": "t", "name": "T", "type": "notify", "message": "m"}])
        h.api("POST", f"/api/w/{ws}/workflows/{manual['id']}/webhook", expect=422)

    def test_file_steps_retries_and_timeline(self) -> None:
        h, ws = self.h, self.ws
        f = h.api("POST", f"/api/w/{ws}/files", json={"title": "Fees", "sheets": [{"name": "Fees", "blocks": [
            {"type": "table", "id": "fees", "rows": [["Audit fee", 7250.5]],
             "columns": [{"label": "Item"}, {"label": "Amount", "format": "money"}]}]}]})
        up = h.client.post(f"/api/w/{ws}/files/upload", files={"file": ("Letter.docx", template_docx())}).json()
        wf = self.create([
            {"id": "refresh", "name": "Refresh fees", "type": "file", "action": "refresh", "file_id": f["file"]["id"]},
            {"id": "letter", "name": "Fill letter", "type": "file", "action": "edit", "file_id": up["file"]["id"],
             "edits": [{"op": "fill", "values": {"client_name": "{{workspace.name}}"}}], "note": "Filled by workflow"},
            {"id": "bad", "name": "Broken tool", "type": "tool", "tool": "calc_npv_irr",
             "arguments": {"cashflows": "not a list", "discount_rate": 0.05}, "retries": 2, "retry_delay": 0,
             "on_error": "continue"},
            {"id": "tell", "name": "Tell", "type": "notify", "message": "{{steps.refresh.output}} {{steps.letter.data.link}}"}])
        run = self.run_wf(wf)
        self.assertEqual(run["status"], "done", run)
        self.assertEqual(h.api("GET", f"/api/w/{ws}/files/{f['file']['id']}")["version"], 2)
        from docx import Document

        letter = h.client.get(f"/api/w/{ws}/files/{up['file']['id']}/download", params={"format": "docx"}).content
        self.assertIn("Dear Acme Ltd,", "\n".join(p.text for p in Document(io.BytesIO(letter)).paragraphs))
        statuses = [(x["step"], x["status"]) for x in run["log"]]
        self.assertEqual(statuses.count(("bad", "retry")), 2)
        self.assertIn(("bad", "error"), statuses)
        tl = {x["id"]: x for x in run["timeline"]}
        self.assertEqual([x["status"] for x in run["timeline"]], ["done", "done", "failed", "done"])
        self.assertEqual(tl["bad"]["attempts"], 3)
        self.assertIsNotNone(tl["refresh"]["ms"])
        self.assertIn("version 2", tl["refresh"]["output"])
        self.assertEqual(tl["letter"]["data"]["version"], 2)
        # without on_error the run fails at the broken step and later steps are "not run"
        wf2 = self.create([{"id": "bad", "name": "Broken", "type": "tool", "tool": "calc_npv_irr",
                            "arguments": {"cashflows": "x", "discount_rate": 0.05}},
                           {"id": "tell", "name": "Tell", "type": "notify", "message": "never"}])
        run = self.run_wf(wf2)
        self.assertEqual(run["status"], "failed")
        self.assertEqual([x["status"] for x in run["timeline"]], ["failed", "not run"])

    def test_templates_and_schedule_preview(self) -> None:
        h, ws = self.h, self.ws
        tpls = h.api("GET", f"/api/w/{ws}/workflow-templates")
        self.assertGreaterEqual(len(tpls), 6)
        for t in tpls:  # every template is a valid definition and saves as a draft
            wf = h.api("POST", f"/api/w/{ws}/workflows/from-template", json={"key": t["key"]})
            self.assertFalse(wf["enabled"])
        refresh = next(w for w in h.api("GET", f"/api/w/{ws}/workflows") if w["name"] == "Refresh a workbook every morning")
        full = h.api("GET", f"/api/w/{ws}/workflows/{refresh['id']}")["workflow"]
        e = h.api("PUT", f"/api/w/{ws}/workflows/{refresh['id']}", expect=422,
                  json={"name": full["name"], "definition": full["definition"], "enabled": True})
        self.assertIn("choose the file", e["message"])
        p = h.api("POST", f"/api/w/{ws}/workflows/schedule-preview", json={"cron": "0 7 2 * *"})
        self.assertEqual((p["timezone"], len(p["next"])), ("Europe/London", 5))
        self.assertTrue(all("T07:00" in x for x in p["next"]))
        h.api("POST", f"/api/w/{ws}/workflows/schedule-preview", json={"cron": "99 * * * *"}, expect=422)


if __name__ == "__main__":
    unittest.main()
