"""Embedded gateway: approvals as AG-UI interrupts, and why the partner application cannot decide them
(threats T5, T6). A write-capable test agent is registered only inside these tests; shipped agents are read-only."""

from __future__ import annotations

import unittest
from unittest import mock

from desk_engine.db import dumps, loads, now
from desk_engine.embedded import agents
from desk_engine.embedded.scope import ToolScope
from tests.embedded_helpers import assert_valid_sequence, last, make_org, new_harness, run_input, stream

WRITE_AGENT = agents.EmbeddedAgent(
    id="test-writer", name={"en": "Test writer"}, description={"en": "test only"}, capability="ask_finance",
    scope=ToolScope(frozenset({"collaboration", "finance", "meta"}), read_only=False, no_egress=False), instructions="test",
    read_only=False)

SEND = [("message_colleague", {"to": "cfo@acme.test", "text": "The Q3 pack is ready."})]


class Interrupts(unittest.TestCase):
    def setUp(self) -> None:
        self.h = new_harness(script=[SEND, "Sent.", SEND, "Sent.", SEND, "Sent."])
        self.org = make_org(self.h, email="o@acme.test")
        self.patch = mock.patch.dict(agents.AGENTS, {"test-writer": WRITE_AGENT})
        self.patch.start()
        self.patch_denied = mock.patch("desk_engine.embedded.scope.ALWAYS_DENIED", frozenset())
        self.patch_denied.start()

    def tearDown(self) -> None:
        self.patch_denied.stop()
        self.patch.stop()
        self.h.close()

    def _interrupted(self, run="r1", thread="t1"):
        events = stream(self.org, run_input("Tell the CFO the pack is ready", run=run, thread=thread), agent="test-writer")
        assert_valid_sequence(events)
        fin = last(events)
        self.assertEqual(fin["type"], "RUN_FINISHED")
        self.assertEqual(fin["outcome"]["type"], "interrupt", fin)
        return fin["outcome"]["interrupts"]

    def test_waiting_approval_becomes_interrupt_outcome(self) -> None:
        interrupts = self._interrupted()
        self.assertEqual(len(interrupts), 1)
        i = interrupts[0]
        appr = self.h.engine.db.one("SELECT * FROM approvals WHERE id = ?", (i["id"],))
        self.assertIsNotNone(appr)
        self.assertEqual(appr["status"], "pending")
        self.assertEqual(i["reason"], "tool_call")
        self.assertTrue(i["toolCallId"])
        self.assertIn("expiresAt", i)
        self.assertEqual(i["responseSchema"]["required"], ["approved"])
        meta = i["metadata"]["ofintech"]
        self.assertEqual(meta["version"], 1)
        self.assertEqual(meta["tool"], "message_colleague")
        self.assertEqual(meta["decided_in"], "desk")
        self.assertIn(f"/w/{self.org.ws}/approvals/{i['id']}", meta["approval_url"])
        # the approval is visible to Desk editors through the normal approvals API
        pending = self.org.admin_api("GET", f"/api/w/{self.org.ws}/approvals")
        self.assertIn(i["id"], [p["id"] for p in pending])

    def test_resume_approve_refused_for_service_user(self) -> None:
        [i] = self._interrupted()
        for approved in (True, False):
            events = stream(self.org, {"threadId": "t1", "runId": "r2", "messages": [],
                                       "resume": [{"interruptId": i["id"], "status": "resolved", "payload": {"approved": approved}}]},
                            agent="test-writer")
            err = last(events)
            self.assertEqual(err["type"], "RUN_ERROR", err)
            self.assertEqual(err["code"], "approval_requires_desk_editor")
            self.assertIn("approval_url", err["metadata"]["ofintech"])
        appr = self.h.engine.db.one("SELECT status FROM approvals WHERE id = ?", (i["id"],))
        self.assertEqual(appr["status"], "pending")   # untouched: the engine refused the viewer
        denied = self.h.engine.db.all("SELECT * FROM audit_log WHERE action = 'approval.denied' AND approval_id = ?", (i["id"],))
        self.assertEqual(len(denied), 2)
        self.assertTrue(all(d["user_id"] == self.org.integration["service_user_id"] for d in denied))

    def test_unknown_interrupt_id(self) -> None:
        self._interrupted()
        events = stream(self.org, {"threadId": "t1", "runId": "r2", "messages": [],
                                   "resume": [{"interruptId": "appr_doesnotexist", "status": "resolved", "payload": {"approved": True}}]},
                        agent="test-writer")
        self.assertEqual(last(events)["code"], "interrupt_unknown")
        # a new message while an interrupt is open must carry resume
        events = stream(self.org, run_input("Anything new?", run="r3", thread="t1"), agent="test-writer")
        self.assertEqual(last(events)["code"], "resume_required")
        # resume on a thread without interrupts
        events = stream(self.org, {"threadId": "fresh", "runId": "r4", "messages": [],
                                   "resume": [{"interruptId": "x", "status": "resolved", "payload": {"approved": True}}]},
                        agent="test-writer")
        self.assertEqual(last(events)["code"], "interrupt_unknown")

    def test_resume_payload_validated(self) -> None:
        [i] = self._interrupted()
        events = stream(self.org, {"threadId": "t1", "runId": "r2", "messages": [],
                                   "resume": [{"interruptId": i["id"], "status": "resolved", "payload": {"approved": "yes"}}]},
                        agent="test-writer")
        self.assertEqual(last(events)["code"], "resume_invalid")

    def test_expired_approval(self) -> None:
        [i] = self._interrupted()
        self.h.engine.db.execute("UPDATE approvals SET expires_at = ? WHERE id = ?", (now() - 1, i["id"]))
        events = stream(self.org, {"threadId": "t1", "runId": "r2", "messages": [],
                                   "resume": [{"interruptId": i["id"], "status": "resolved", "payload": {"approved": True}}]},
                        agent="test-writer")
        self.assertEqual(last(events)["code"], "interrupt_expired")

    def test_cancelled_resume_stops_the_waiting_run(self) -> None:
        [i] = self._interrupted()
        events = stream(self.org, {**run_input("Never mind.", run="r2", thread="t1"),
                                   "resume": [{"interruptId": i["id"], "status": "cancelled"}]}, agent="test-writer")
        assert_valid_sequence(events)
        self.assertEqual(self.h.engine.db.one("SELECT status FROM approvals WHERE id = ?", (i["id"],))["status"], "expired")
        self.assertEqual(last(events)["type"], "RUN_FINISHED")   # the new turn ran (and asked again, per the script)

    def test_desk_editor_decides_and_embedded_client_reattaches(self) -> None:
        [i] = self._interrupted()
        desk_run = self.h.engine.db.one("SELECT id FROM agent_runs WHERE status = 'waiting_approval'")["id"]
        # the owner approves inside Desk
        self.org.admin_api("POST", f"/api/w/{self.org.ws}/approvals/{i['id']}/decide", json={"approve": True})
        import time
        for _ in range(100):
            if self.h.engine.db.one("SELECT status FROM agent_runs WHERE id = ?", (desk_run,))["status"] in {"done", "error"}:
                break
            time.sleep(0.05)
        # re-attach replays the finished run with the tool result and a success outcome
        with self.h.client.stream("GET", f"/api/embedded/agents/test-writer/runs/{desk_run}/events?threadId=t1&runId=r1",
                                  headers=self.org.page()) as resp:
            self.assertEqual(resp.status_code, 200)
            import json
            events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
        assert_valid_sequence(events)
        self.assertEqual(last(events)["outcome"]["type"], "success")
        self.assertTrue(any(e["type"] == "TOOL_CALL_RESULT" for e in events))

    def test_args_change_detected(self) -> None:
        [i] = self._interrupted()
        run = self.h.engine.db.one("SELECT * FROM agent_runs WHERE status = 'waiting_approval'")
        # the pending call's arguments change after the approval was requested (a tampered run state)
        state = loads(run["state"], {})
        state["pending"][0]["call"]["arguments"]["to"] = "attacker@evil.test"
        self.h.engine.db.execute("UPDATE agent_runs SET state = ? WHERE id = ?", (dumps(state), run["id"]))
        self.org.admin_api("POST", f"/api/w/{self.org.ws}/approvals/{i['id']}/decide", json={"approve": True})
        import time
        for _ in range(100):
            if self.h.engine.db.one("SELECT status FROM agent_runs WHERE id = ?", (run["id"],))["status"] in {"done", "error"}:
                break
            time.sleep(0.05)
        tool_msgs = self.h.engine.db.all("SELECT content FROM messages WHERE thread_id = ? AND role = 'tool'", (run["thread_id"],))
        self.assertTrue(any("changed" in m["content"] for m in tool_msgs), tool_msgs)
        self.assertFalse(any("attacker@evil.test" in m["content"] and "Refused" not in m["content"] for m in tool_msgs))
        refused = self.h.engine.db.all("SELECT * FROM audit_log WHERE approval_id = ? AND action = 'approval.changed'", (i["id"],))
        self.assertTrue(refused)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
