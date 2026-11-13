"""Process metrics for the agent, in the Prometheus text format (audit finding AI-12).

What is measured: model calls (outcome, attempts, time to first event, total time), retries by error class, tool calls
(status, duration), agent runs by outcome, and event-loop lag. Labels come only from closed vocabularies (outcome
codes, built-in tool names; every MCP tool is ``mcp``), never from workspace, user or thread identifiers, so the
endpoint shows nothing about any tenant. Values live in memory and restart with the process, as Prometheus expects.

Read them at ``GET /api/admin/metrics`` (the instance administrator only).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict

SECONDS_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
LAG_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5)

HELP = {
    "desk_llm_calls_total": ("counter", "Model calls by outcome (ok or the error code after retries)."),
    "desk_llm_retries_total": ("counter", "Model calls retried, by the error class that caused the retry."),
    "desk_llm_call_seconds": ("histogram", "Model call duration, all attempts included."),
    "desk_llm_ttfb_seconds": ("histogram", "Time from the model call to its first streamed event."),
    "desk_tool_calls_total": ("counter", "Tool calls by tool and status (ok, error, refused, reused)."),
    "desk_tool_seconds": ("histogram", "Tool call duration."),
    "desk_agent_runs_total": ("counter", "Agent runs by how they ended."),
    "desk_event_loop_lag_seconds": ("histogram", "How late a 0.25 s timer fired on the event loop."),
}


Labels = tuple[tuple[str, str], ...]


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[Labels, float]] = defaultdict(dict)
        self._hist: dict[str, dict[Labels, list[float]]] = defaultdict(dict)   # bucket counts..., sum, count
        self._buckets: dict[str, tuple[float, ...]] = {}

    def inc(self, name: str, value: float = 1, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._counters[name][key] = self._counters[name].get(key, 0) + value

    def observe(self, name: str, seconds: float, buckets: tuple[float, ...] = SECONDS_BUCKETS, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._buckets.setdefault(name, buckets)
            b = self._buckets[name]
            row = self._hist[name].setdefault(key, [0.0] * (len(b) + 2))
            for i, edge in enumerate(b):
                if seconds <= edge:
                    row[i] += 1
            row[-2] += seconds
            row[-1] += 1

    def render(self) -> str:
        def lab(key: Labels, extra: tuple[tuple[str, str], ...] = ()) -> str:
            items = [*key, *extra]
            if not items:
                return ""
            return "{" + ",".join(f'{k}="{_esc(v)}"' for k, v in items) + "}"

        out: list[str] = []
        with self._lock:
            for name in sorted(set(HELP) | set(self._counters) | set(self._hist)):
                kind, text = HELP.get(name, ("untyped", ""))
                out += [f"# HELP {name} {text}", f"# TYPE {name} {kind}"]
                for key, v in sorted(self._counters.get(name, {}).items()):
                    out.append(f"{name}{lab(key)} {_num(v)}")
                b = self._buckets.get(name, ())
                for key, row in sorted(self._hist.get(name, {}).items()):
                    for i, edge in enumerate(b):
                        out.append(f"{name}_bucket{lab(key, (('le', _num(edge)),))} {_num(row[i])}")
                    out.append(f"{name}_bucket{lab(key, (('le', '+Inf'),))} {_num(row[-1])}")
                    out.append(f"{name}_sum{lab(key)} {round(row[-2], 6)}")
                    out.append(f"{name}_count{lab(key)} {_num(row[-1])}")
        return "\n".join(out) + "\n"


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


async def watch_event_loop(metrics: Metrics, interval: float = 0.25) -> None:
    """Records how late a timer fires: a direct measure of anything blocking the loop (AI-10)."""
    while True:
        started = time.perf_counter()
        await asyncio.sleep(interval)
        metrics.observe("desk_event_loop_lag_seconds", max(0.0, time.perf_counter() - started - interval), LAG_BUCKETS)


def tool_label(name: str, category: str) -> str:
    """A bounded label: built-in tools by name, every MCP server's tools as one."""
    return "mcp" if category == "mcp" else name

