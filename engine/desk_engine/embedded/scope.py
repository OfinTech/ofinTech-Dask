"""Per-run tool scope.

The agent harness offers a run every tool its user may use. An embedded run must be narrower than that: read tools
only, nothing that leaves the network, and only the categories the agent is for. The scope is stored in the run's
persisted state so it holds across approval pauses and engine restarts, and it is applied *after* the registry's
own filters (role, sealing, availability) — it can only remove tools, never add one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..agent.tools import Tool

# tools every scope keeps out, whatever its categories say
ALWAYS_DENIED = frozenset({"propose_memory", "run_workflow", "message_colleague", "edit_file", "open_file_for_editing",
                           "create_dashboard", "create_workbook", "create_report", "sync_accounting",
                           "prepare_month_end_close"})


@dataclass(frozen=True)
class ToolScope:
    categories: frozenset[str]
    read_only: bool = True
    no_egress: bool = True
    deny: frozenset[str] = field(default_factory=frozenset)

    def to_state(self) -> dict[str, Any]:
        return {"version": 1, "categories": sorted(self.categories), "read_only": self.read_only,
                "no_egress": self.no_egress, "deny": sorted(self.deny | ALWAYS_DENIED)}

    @classmethod
    def from_state(cls, raw: Any) -> ToolScope:
        if not isinstance(raw, dict):
            raise ValueError("tool_scope must be an object")
        cats = raw.get("categories")
        if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
            raise ValueError("tool_scope.categories must be a list of strings")
        # a damaged or tampered scope falls closed: read-only and no egress stay on unless explicitly False *and*
        # the state came from this module (version 1 never writes False for them)
        return cls(frozenset(cats), read_only=bool(raw.get("read_only", True)), no_egress=bool(raw.get("no_egress", True)),
                   deny=frozenset(str(d) for d in raw.get("deny", [])) | ALWAYS_DENIED)

    def allows(self, tool: Tool) -> bool:
        from ..egress import tool_sends_out

        if tool.name in self.deny or tool.name in ALWAYS_DENIED:
            return False
        if tool.category not in self.categories:
            return False
        if self.read_only and (tool.effect != "read" or tool.needs_approval()):
            return False
        if self.no_egress and tool_sends_out(tool):
            return False
        if tool.category == "mcp":   # tools from outside servers are never part of an embedded agent
            return False
        return True

    def apply(self, tools: dict[str, Tool]) -> dict[str, Tool]:
        return {name: t for name, t in tools.items() if self.allows(t)}


# categories of the read-only financial agent: analysis (KPIs, forecasts, close status), accounting reports and
# invoices, cost centres, datasets, calculators, the workspace's documents, and the meta tool that loads groups
FINANCIAL_READ_CATEGORIES = frozenset({"analysis", "accounting", "costcentres", "data", "finance", "knowledge", "meta"})

FINANCIAL_READ_SCOPE = ToolScope(FINANCIAL_READ_CATEGORIES)
