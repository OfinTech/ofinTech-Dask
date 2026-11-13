"""Shared set-up for the AI agent audit evaluations.

The evaluations run the real engine in-process (the same `Harness` the engine's own test suite uses) with a
scripted OpenAI-compatible model on a mocked transport. Nothing here reaches a network service, and nothing is
written outside a temporary directory except the JSON results in ../results/.
"""
from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine"))

from tests.helpers import FakeLLM, Harness, Router, sse  # noqa: E402,F401

RESULTS = Path(__file__).resolve().parents[1] / "results"
RESULTS.mkdir(exist_ok=True)


def tool_ctx(h: Harness, ws: str, user_id: str | None = None):
    from desk_engine.agent.tools import ToolContext
    workspace = h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,))
    uid = user_id or h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ? AND role = 'owner'", (ws,))["user_id"]
    return ToolContext(h.engine, workspace, uid)


def run_tool(h: Harness, ctx, name: str, args: dict[str, Any]):
    """Runs one registered tool exactly as the agent loop does (Tool.run: guards, schema check, timeout)."""
    async def go():
        tools = await h.engine.tools.for_context(ctx)
        tool = tools.get(name)
        if tool is None:
            return None
        return await tool.run(ctx, json.loads(json.dumps(args)))
    return h.client.portal.call(go)


def environment() -> dict[str, Any]:
    import os
    return {"python": platform.python_version(), "platform": platform.platform(), "cpus": os.cpu_count(),
            "processor": platform.processor() or _cpu_name(), "when": time.strftime("%Y-%m-%d %H:%M:%S %z")}


def _cpu_name() -> str:
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def save(name: str, payload: dict[str, Any]) -> Path:
    payload = {"environment": environment(), **payload}
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(payload, indent=1, default=str, ensure_ascii=False))
    return path


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)
