"""Process metrics for the agent, in the Prometheus text format (audit finding AI-12).

What is measured: model calls (outcome, attempts, time to first event, total time), retries by error class, tool calls
(status, duration), agent runs by outcome, and event-loop lag. Labels come only from closed vocabularies (outcome
codes, built-in tool names; every MCP tool is ``mcp``), never from workspace, user or thread identifiers, so the
endpoint shows nothing about any tenant. Values live in memory and restart with the process, as Prometheus expects.

Read them at ``GET /api/admin/metrics`` (the instance administrator only).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import resource
import threading
import time
from collections import defaultdict
from collections.abc import Callable

SECONDS_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
LAG_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5)

log = logging.getLogger("desk.metrics")

MAX_SERIES = 300        # per metric family; beyond it new label sets are dropped and counted (cardinality control)

HELP = {
    "desk_llm_calls_total": ("counter", "Model calls by outcome (ok or the error code after retries)."),
    "desk_llm_retries_total": ("counter", "Model calls retried, by the error class that caused the retry."),
    "desk_llm_call_seconds": ("histogram", "Model call duration, all attempts included."),
    "desk_llm_ttfb_seconds": ("histogram", "Time from the model call to its first streamed event."),
    "desk_tool_calls_total": ("counter", "Tool calls by tool and status (ok, error, refused, reused)."),
    "desk_tool_seconds": ("histogram", "Tool call duration."),
    "desk_agent_runs_total": ("counter", "Agent runs by how they ended."),
    "desk_event_loop_lag_seconds": ("histogram", "How late a 0.25 s timer fired on the event loop."),
    # agent
    "desk_agent_runs_started_total": ("counter", "Agent runs started."),
    "desk_agent_run_seconds": ("histogram", "Agent run duration, start to end, by outcome."),
    "desk_agent_run_errors_total": ("counter", "Agent runs that ended in an error, by error code."),
    "desk_agent_runs_active": ("gauge", "Agent runs in progress now."),
    "desk_llm_tokens_total": ("counter", "Model tokens by kind (input, output, cache_read, cache_write)."),
    "desk_llm_finish_total": ("counter", "Model replies by finish reason (stop, tool_calls, length, content_filter)."),
    "desk_llm_request_chars": ("histogram", "Characters sent to the model per call (about 4 per token)."),
    "desk_llm_history_budget_ratio": ("histogram", "Share of the history budget a request used (1 = at the budget)."),
    # financial reliability
    "desk_figures_total": ("counter", "Figures in final answers by provenance (tool, derived, "
                                      "memory, user, unsupported)."),
    "desk_answers_total": ("counter", "Final answers by figure check result (verified, unsupported, no_figures)."),
    "desk_reconciliation_lines_total": ("counter", "Bank reconciliation lines by result "
                                                   "(matched, statement_only, ledger_only)."),
    "desk_reconciliations_total": ("counter", "Bank reconciliations by result "
                                              "(agreed, difference, no_closing_balance)."),
    # backend
    "desk_http_requests_total": ("counter", "API requests by route template, method and status class."),
    "desk_http_request_seconds": ("histogram", "API request duration by route template."),
    "desk_db_query_seconds": ("histogram", "Database call duration by kind (read, write)."),
    "desk_db_connections": ("gauge", "Open SQLite connections (one per thread that used the database)."),
    "desk_background_failures_total": ("counter", "Background jobs that failed, by kind."),
    "desk_process_resident_memory_bytes": ("gauge", "Resident memory of the engine process."),
    "desk_process_cpu_seconds_total": ("counter", "CPU time used by the engine process (user + system)."),
    "desk_process_start_time_seconds": ("gauge", "When the process started (Unix time)."),
    "desk_up": ("gauge", "1 while the engine answers scrapes."),
    # security
    "desk_auth_failures_total": ("counter", "Requests refused as not signed in (401), by route template."),
    "desk_access_denied_total": ("counter", "Requests refused for lack of permission (403), by reason code."),
    "desk_workspace_rejections_total": ("counter", "Requests naming a workspace the user is not a member of."),
    "desk_tool_refusals_total": ("counter", "Tool calls refused by a guard (payment, sealed, role, unknown)."),
    "desk_metrics_dropped_series_total": ("counter", "Label sets dropped by the cardinality cap."),
}


Labels = tuple[tuple[str, str], ...]


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[Labels, float]] = defaultdict(dict)
        self._hist: dict[str, dict[Labels, list[float]]] = defaultdict(dict)   # bucket counts..., sum, count
        self._buckets: dict[str, tuple[float, ...]] = {}
        self._gauges: dict[str, Callable[[], float]] = {}

    def gauge(self, name: str, fn: Callable[[], float]) -> None:
        """A value read when the metrics are rendered (memory, connections, active runs)."""
        self._gauges[name] = fn

    def _room(self, family: dict, key: Labels) -> bool:
        if key in family or len(family) < MAX_SERIES:
            return True
        c = self._counters["desk_metrics_dropped_series_total"]
        c[()] = c.get((), 0) + 1
        return False

    def inc(self, name: str, value: float = 1, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            if self._room(self._counters[name], key):
                self._counters[name][key] = self._counters[name].get(key, 0) + value

    def observe(self, name: str, seconds: float, buckets: tuple[float, ...] = SECONDS_BUCKETS, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._buckets.setdefault(name, buckets)
            b = self._buckets[name]
            if not self._room(self._hist[name], key):
                return
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
        values: dict[str, float] = {}
        for name, fn in list(self._gauges.items()):
            try:
                values[name] = float(fn())
            except Exception:  # noqa: BLE001 -- a broken probe must not break the scrape
                log.warning("metric probe %s failed", name, exc_info=True)
        with self._lock:
            for name, v in values.items():
                self._counters[name] = {(): v}
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



def process_rss_bytes() -> float:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):   # not Linux: the peak instead of the current value
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def process_cpu_seconds() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def scrape_token_ok(header: str | None) -> bool:
    """A Prometheus server has no user session. DESK_METRICS_TOKEN (at least 32 characters) lets it read
    /api/admin/metrics and nothing else; unset, only the instance administrator can."""
    expected = os.environ.get("DESK_METRICS_TOKEN") or ""
    if len(expected) < 32 or not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), expected.encode())
