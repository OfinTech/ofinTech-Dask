"""The two hardest promises in this product must leave evidence when they are kept.

OfinTech Desk refuses to touch payments, and a sealed workspace refuses to send anything out. Both are enforced in
code at the moment a tool is called, and both were silent: the guard returned a sentence to the model and wrote
nothing down. A workspace where something tried a hundred times to move money, or to push a ledger to an outside
service, looked exactly like a workspace where nothing had happened -- so the control worked and nobody could ever
show that it had, or notice that it was being tested.

A refusal at the HTTP layer is already audited (``web._audit_denied``). These tests hold the same standard one
layer down, where the model is the caller, and hold the privacy line that comes with it: the entry names the tool
and the reason, and never the arguments, which are written by the model out of the workspace's own data and can
carry account numbers and counterparties into a log that is read far more widely than the ledger is.
"""

from __future__ import annotations

import json
import unittest

from desk_engine.agent.tools import Tool, ToolContext, ToolOutput

from .helpers import Harness

IBAN = "SA5900999999999999999999"


async def _never_called(ctx: ToolContext, args: dict) -> ToolOutput:      # pragma: no cover - the guard comes first
    raise AssertionError("the guard let the call through")


def payment_tool() -> Tool:
    return Tool(name="create_payment_batch", description="Create a payment batch for approval.",
                parameters={"type": "object", "properties": {}}, handler=_never_called, effect="write",
                category="mcp")


def outbound_tool() -> Tool:
    return Tool(name="gmail_send", description="Send an e-mail.", parameters={"type": "object", "properties": {}},
                handler=_never_called, effect="write", category="email")


class GuardRefusalsAreRecorded(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)
        self.user = self.h.engine.db.scalar("SELECT user_id FROM members WHERE workspace_id = ?", (self.ws,))

    def ctx(self, **kw) -> ToolContext:
        workspace = self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,))
        return ToolContext(engine=self.h.engine, workspace=workspace, user_id=self.user, **kw)

    def refusals(self) -> list[dict]:
        rows = self.h.engine.db.all(
            "SELECT * FROM audit_log WHERE workspace_id = ? AND action = 'tool.refused' ORDER BY id", (self.ws,))
        for row in rows:
            row["detail"] = json.loads(row["detail"]) if row["detail"] else {}
        return rows

    def run_tool(self, tool: Tool, ctx: ToolContext, args: dict | None = None) -> ToolOutput:
        return self.h.client.portal.call(tool.run, ctx, args if args is not None else {})

    def test_a_refused_payment_is_recorded(self) -> None:
        out = self.run_tool(payment_tool(), self.ctx(run_id="run_1", source="chat"))
        self.assertIn("never initiates", out.text)
        entries = self.refusals()
        self.assertEqual(1, len(entries), "a refused payment must leave exactly one entry")
        self.assertEqual("create_payment_batch", entries[0]["target"])
        self.assertEqual("payment", entries[0]["detail"]["reason"])
        self.assertEqual("run_1", entries[0]["detail"]["run_id"])
        self.assertEqual(self.user, entries[0]["user_id"], "the entry names who the call was made for")

    def test_a_refused_send_from_a_sealed_workspace_is_recorded(self) -> None:
        self.h.engine.db.update("workspaces", self.ws, {"egress_policy": "sealed"})
        out = self.run_tool(outbound_tool(), self.ctx(source="workflow"))
        self.assertNotEqual("", out.text)
        entries = self.refusals()
        self.assertEqual(1, len(entries))
        self.assertEqual("sealed", entries[0]["detail"]["reason"])
        self.assertEqual("workflow", entries[0]["detail"]["source"], "where the call came from is part of the event")

    def test_an_open_workspace_sends_and_records_nothing(self) -> None:
        """The record is of refusals. A tool that is allowed through must not fill the log."""
        async def allowed(ctx: ToolContext, args: dict) -> ToolOutput:
            return ToolOutput("sent")

        tool = outbound_tool()
        tool.handler = allowed
        self.assertEqual("sent", self.run_tool(tool, self.ctx()).text)
        self.assertEqual([], self.refusals())

    def test_a_role_refusal_is_recorded(self) -> None:
        self.h.engine.db.execute("UPDATE members SET role = 'viewer' WHERE workspace_id = ?", (self.ws,))
        tool = Tool(name="build_dashboard", description="Build a dashboard.",
                    parameters={"type": "object", "properties": {}}, handler=_never_called, min_role="editor")
        out = self.run_tool(tool, self.ctx())
        self.assertIn("needs the editor role", out.text)
        self.assertEqual("role", self.refusals()[0]["detail"]["reason"])

    def test_the_entry_never_carries_the_arguments(self) -> None:
        """Tool arguments are model-written from workspace data; an IBAN must not travel into the audit log."""
        self.run_tool(payment_tool(), self.ctx(), {"iban": IBAN, "amount": 250000, "beneficiary": "Oasis Trading Co."})
        row = self.refusals()[0]
        for field in (json.dumps(row["detail"]), str(row["target"]), str(row.get("resource_type"))):
            self.assertNotIn(IBAN, field)
            self.assertNotIn("Oasis Trading Co.", field)
            self.assertNotIn("250000", field)

    def test_a_failure_to_record_does_not_let_the_call_through(self) -> None:
        """The guard is the control and the entry is evidence of it; losing the evidence must not lose the control."""
        tool = payment_tool()
        broken = self.ctx()
        original = self.h.engine.audit

        def explode(*args, **kw):
            raise RuntimeError("audit store unavailable")

        self.h.engine.audit = explode
        try:
            out = self.run_tool(tool, broken)
        finally:
            self.h.engine.audit = original
        self.assertIn("never initiates", out.text, "the refusal must stand even when it cannot be recorded")


if __name__ == "__main__":
    unittest.main()
