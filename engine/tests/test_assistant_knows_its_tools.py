"""What the assistant can do is read from the registry, not recalled (list_capabilities).

What this guards. On 24 Nov 2026 the owner asked the assistant how many tools it had. A step of a conversation is
sent only the tool groups it needs, so the model listed what it had been shown, filled in the rest, and counted
its own list with the calculator: "53", among them adding a bank transfer, a deposit and a withdrawal, in a
product that moves no money. The count and the list are facts the registry holds for the workspace and the person
asking, so a tool returns them and the rules send the model to it.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from desk_engine.agent.context import CORE, choose_tools, select_groups
from desk_engine.agent.prompts import RULES
from desk_engine.agent.tools import ToolContext, registry
from desk_engine.llm.base import ChatMessage

from .helpers import Harness


class KnowsItsTools(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)

    def context(self, demo: bool) -> ToolContext:
        ws = self.h.setup_workspace(with_model=False)
        if demo:
            self.h.api("POST", f"/api/w/{ws}/demo")
        workspace = dict(self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,)))
        user = self.h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (ws,))["user_id"]
        return ToolContext(self.h.engine, workspace, user)

    def listed(self, ctx: ToolContext) -> tuple[dict[str, Any], Any]:
        async def go() -> tuple[dict[str, Any], Any]:
            tools = await registry.for_context(ctx)
            return tools, await tools["list_capabilities"].handler(ctx, {})
        return asyncio.run(go())

    def test_the_count_and_the_names_are_the_registrys_for_this_workspace(self) -> None:
        tools, out = self.listed(self.context(demo=True))
        text = out.for_model()
        self.assertEqual(out.data["count"], len(tools))
        self.assertIn(f"Tools available to you in this workspace: {len(tools)}, in {len(out.data['groups'])} groups", text)
        self.assertEqual(sorted(n for names in out.data["groups"].values() for n in names), sorted(tools))
        for name in tools:
            self.assertIn(f"- {name}: ", text)
        self.assertEqual(len(out.blocks[0]["rows"]), len(tools))
        for name in ("financial_statements", "bank_reconciliation", "calc_what_if", "more_tools", "list_capabilities"):
            self.assertIn(name, tools)

    def test_it_says_what_needs_a_person_and_what_nothing_does(self) -> None:
        tools, out = self.listed(self.context(demo=True))
        text = out.for_model()
        need = sorted(n for n, t in tools.items() if t.needs_approval())
        self.assertEqual(out.data["needs_approval"], need)
        self.assertTrue(need, "the demo workspace should offer a tool that needs approval, or this proves nothing")
        self.assertIn(f"Run only after a person approves them in the app ({len(need)}): {', '.join(need)}.", text)
        self.assertIn("No tool pays, transfers money, or adds or changes a beneficiary or bank details.", text)
        for invented in ("transfer", "deposit", "withdrawal", "add_invoice", "add_entry"):
            self.assertFalse(any(invented in n for n in tools), f"no tool is called anything like “{invented}”")

    def test_a_workspace_with_no_ledger_is_told_of_fewer_tools(self) -> None:
        with_ledger, _ = self.listed(self.context(demo=True))
        tools, out = self.listed(self.context(demo=False))
        self.assertLess(out.data["count"], len(with_ledger))
        self.assertNotIn("financial_statements", out.for_model())
        self.assertNotIn("bank_reconciliation", out.for_model())
        self.assertEqual(out.data["count"], len(tools))

    def test_it_is_sent_with_every_step_and_the_rules_send_the_model_to_it(self) -> None:
        ctx = self.context(demo=False)
        tools = asyncio.run(registry.for_context(ctx))
        self.assertIn(tools["list_capabilities"].category, CORE)
        groups = select_groups([ChatMessage("user", "كم أداة عندك؟")], tools, has_datasets=False)
        self.assertIn("list_capabilities", {t.name for t in choose_tools(tools, groups)})
        rules = " ".join(RULES.split())
        self.assertIn("call list_capabilities and answer from its result", rules)
        self.assertIn("never list or count them from memory, and never name a tool or an action that is not in that "
                      "result", rules)


if __name__ == "__main__":
    unittest.main()
