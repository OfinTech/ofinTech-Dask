"""Where figures come from.

* ``SourceBook`` numbers the sources of a file (S1, S2, …) and keeps one entry per distinct origin.
* ``Evidence`` is everything the conversation saw: tool results (accounting reports, dataset queries, calculator
  results, e-mails, document passages) and the user's own messages. A figure typed into a table or a report is
  traced to the evidence line that contains it, preferring lines that also mention the row's label.
* ``Evidence.from_results`` is the same kind of evidence from results a caller read itself, outside a conversation:
  each one a tool's name, its arguments, what it returned and when. A file built with it names its sources the way
  a file built in a conversation does.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..agent.harness import _numbers
from ..db import loads

if TYPE_CHECKING:
    from ..app import Engine

TOOL_TITLES = {
    "accounting_report": "{report} from the accounting system",
    "list_invoices": "{kind} documents from the accounting system",
    "query_dataset": "Query of dataset “{dataset}”",
    "describe_dataset": "Statistics of dataset “{dataset}”",
    "gmail_read": "E-mail",
    "gmail_search": "Gmail search",
    "calc_npv_irr": "NPV / IRR calculation",
    "calc_xnpv_xirr": "XNPV / XIRR calculation",
    "calc_loan_schedule": "Loan schedule calculation",
    "calc_depreciation": "Depreciation calculation",
    "calc_financial_ratios": "Ratio calculation",
    "calc_variance": "Variance calculation",
    "calc_sales_tax": "Sales tax calculation",
    "calc_breakeven": "Break-even calculation",
    "calc_growth": "Growth calculation",
    "calc_currency_convert": "Currency conversion",
    "inspect_file": "Contents of the user's file",
}


class SourceBook:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self._by_key: dict[str, str] = {}

    def add(self, key: str, **info: Any) -> str:
        if key in self._by_key:
            return self._by_key[key]
        sid = f"S{len(self.items) + 1}"
        self._by_key[key] = sid
        self.items.append({"id": sid, **info})
        return sid

    def get(self, sid: str) -> dict[str, Any] | None:
        return next((s for s in self.items if s["id"] == sid), None)


@dataclass
class Item:
    key: str
    kind: str  # tool | passage | user
    title: str
    system: str
    at: float
    link: str | None
    lines: list[tuple[str, list[tuple[str, float]]]] = field(default_factory=list)
    priority: int = 1


def _lines(text: str) -> list[tuple[str, list[tuple[str, float]]]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and any(ch.isdigit() for ch in line):
            nums = _numbers(line)
            if nums:
                out.append((line[:400], nums))
    return out


class Evidence:
    def __init__(self, items: list[Item]) -> None:
        self.items = items

    @classmethod
    def for_thread(cls, engine: Engine, thread_id: str | None) -> Evidence:
        if not thread_id:
            return cls([])
        rows = engine.db.all("SELECT m.id, m.role, m.content, m.payload, m.created_at, u.name AS author FROM messages m "
                             "LEFT JOIN users u ON u.id = m.author_id WHERE m.thread_id = ? AND m.role IN ('user','tool',"
                             "'assistant') ORDER BY m.created_at", (thread_id,))
        conns = {c["id"]: c["label"] for c in engine.db.all("SELECT c.id, c.label FROM connections c JOIN threads t ON "
                                                             "t.workspace_id = c.workspace_id WHERE t.id = ?", (thread_id,))}
        items: list[Item] = []
        for r in rows:
            payload = loads(r["payload"], {})
            if r["role"] == "assistant":  # memory consulted for an answer counts as a source
                for mem in payload.get("memories") or []:
                    items.append(Item(f"memory:{mem['id']}", "memory", f"Saved memory ({mem.get('scope', 'workspace')})",
                                      "Memory", r["created_at"], f"memory?id={mem['id']}", _lines(mem.get("content") or ""),
                                      1))
                continue
            if r["role"] == "user":
                items.append(Item(f"user:{r['id']}", "user", f"Stated by {r['author'] or 'a user'} in the conversation",
                                  "Conversation", r["created_at"], f"chat/{thread_id}", _lines(r["content"] or ""), 0))
                continue
            if payload.get("status") not in (None, "ok"):
                continue
            name = payload.get("name") or "tool"
            args = payload.get("arguments") or {}
            sources = next((b for b in payload.get("blocks") or [] if b.get("type") == "sources"), None)
            if sources:  # document passages: one item per passage
                parts = re.split(r"^\[(\d+)\] ", r["content"] or "", flags=re.M)
                texts = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
                for it in sources.get("items", []):
                    body = texts.get(int(it.get("n") or 0), it.get("snippet") or "")
                    link = (f"documents/{it['document_id']}" + (f"?page={it['page']}" if it.get("page") else "")
                            if it.get("document_id") else it.get("link"))
                    title = f"{it.get('name')}" + (f", page {it['page']}" if it.get("page") else "")
                    items.append(Item(f"doc:{it.get('document_id') or it.get('link')}:{it.get('page')}", "passage", title,
                                      "Documents", r["created_at"], link, _lines(body), 2))
                continue
            title, system = _tool_title(name, args, conns)
            items.append(Item(f"tool:{r['id']}", "tool", title, system, r["created_at"],
                              f"chat/{thread_id}?message={r['id']}", _lines(r["content"] or ""), 2))
        return cls(items)

    @classmethod
    def from_results(cls, results: Iterable[Mapping[str, Any]]) -> Evidence:
        """Evidence from tool results the caller read itself. Each result holds ``name`` (the tool), ``result``
        (what it returned: text as it is, or data, which is written one record to a line so a figure stays beside
        its label) and ``at`` (when it was read, seconds since the epoch). Optional: ``arguments`` (they name the
        report and the period, as in a conversation), ``system`` (where the figures came from; by default as in a
        conversation), ``link`` (a place in the app to open) and ``key`` (two results with the same key are one
        source). A result the caller marks with ``status`` other than "ok" is left out, as a failed call is."""
        items: list[Item] = []
        for n, r in enumerate(results, 1):
            if r.get("status") not in (None, "ok"):
                continue
            if r.get("at") is None:
                raise ValueError("Each result needs `at`: when it was read, so the file can say how fresh it is.")
            name = str(r.get("name") or "tool")
            args = r.get("arguments") or {}
            title, system = _tool_title(name, args, {})
            body = r.get("result")
            items.append(Item(str(r.get("key") or f"result:{n}:{name}"), "tool", title, str(r.get("system") or system),
                              float(r["at"]), r.get("link"), _lines(body if isinstance(body, str) else _text(body)), 2))
        return cls(items)

    def match(self, value: float, labels: list[str]) -> tuple[Item, str] | None:
        """The evidence line holding ``value`` (to the cent, either sign), best first."""
        best: tuple[float, Item, str] | None = None
        a = abs(value)
        tol = max(0.005, a * 1e-9)
        wanted = [w.lower() for w in labels if w and len(w) > 2]
        for idx, item in enumerate(self.items):
            for line, nums in item.lines:
                if not any(abs(abs(v) - a) <= tol for _, v in nums):
                    continue
                low = line.lower()
                score = item.priority * 10 + idx * 0.001
                score += sum(25 for w in wanted if w in low)
                if best is None or score > best[0]:
                    best = (score, item, line)
        return (best[1], best[2]) if best else None


def _tool_title(name: str, args: Mapping[str, Any], conns: Mapping[str, str]) -> tuple[str, str]:
    """How a tool result is named in a file's list of sources, and the system it came from."""
    fmt = {k: (v if not isinstance(v, (dict, list)) else "…") for k, v in args.items()}
    fmt.setdefault("report", "Report")
    fmt.setdefault("kind", "Invoices")
    fmt.setdefault("dataset", "dataset")
    title = TOOL_TITLES.get(name, name.replace("_", " ").capitalize()).format_map(_Safe(fmt))
    title = title.replace("profit_and_loss", "Profit and loss").replace("balance_sheet", "Balance sheet")
    system = conns.get(str(args.get("connection_id") or ""), "")
    if name in {"accounting_report", "list_invoices"} and not system and conns:
        system = next(iter(conns.values()))
    if not system:
        system = "Dataset" if "dataset" in name else "Gmail" if name.startswith("gmail") else "OfinTech Desk"
    period = " · ".join(str(args[k]) for k in ("start_date", "end_date", "as_of") if args.get(k))
    if period:
        title += f" ({period})"
    return title, system


def _text(value: Any, label: str = "") -> str:
    """Data as lines: a record to a line, its fields side by side, so a figure is on the line that names it."""
    if isinstance(value, Mapping):
        if all(not isinstance(v, (Mapping, list, tuple)) for v in value.values()):
            return (f"{label}: " if label else "") + json.dumps(value, ensure_ascii=False, default=str)
        return "\n".join(_text(v, f"{label}.{k}" if label else str(k)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "\n".join(_text(v, label) for v in value)
    return (f"{label}: " if label else "") + str(value)


class _Safe(dict):  # type: ignore[type-arg]
    def __missing__(self, key: str) -> str:
        return key
