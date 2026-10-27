"""Maker–checker review of a file.

1. Automatic checks (instant, deterministic): figures without a source, divisions that came out blank, empty
   tables, narrative figures without a source.
2. An independent reviewer: a separate model call that sees only the finished file, its sources and read-only
   tools — not the conversation that produced it — and records findings against cells and paragraphs. The
   workspace can choose a different model for reviewing than for drafting.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ..agent.tools import ToolContext, ToolOutput
from ..llm.base import ChatMessage, ToolSpec, Usage
from ..llm.registry import build_model, resolve_choice
from .resolve import fmt_value

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.review")

MAX_STEPS = 10
REVIEW_CATEGORIES = {"finance", "data", "knowledge", "accounting"}
EXCLUDED_TOOLS = {"sync_accounting", "create_dashboard", "create_workbook", "create_report"}
SEVERITIES = ("high", "medium", "low")

REVIEWER_PROMPT = """You are the independent reviewer (the checker in a maker–checker control) for a finance team.
You did not prepare this file and you have not seen the conversation that produced it. Review it as an experienced
financial controller would before it goes to management or auditors.

Look for:
- figures that disagree with their sources or with each other (totals, cross-references between tables, KPIs and
  narrative);
- arithmetic, sign, unit, currency or period errors; formulas that do not compute what their label says (for example
  a variance % divided by the wrong base);
- statements the tables do not support, or that overstate certainty; missing context a reader needs (period, basis,
  exclusions);
- tax, legal or regulatory statements presented as definitive.

Use the read-only tools to check figures against the underlying data or documents when that helps. Record each
issue with add_finding. target: a cell address exactly as listed (e.g. P&L!C7), a paragraph id (P3), or "file".
severity: high (wrong or unsupported figure, misleading statement), medium (likely error or important omission),
low (presentation or clarity). Be specific and brief: what is wrong and what to check or change.
Cells marked NO SOURCE are already flagged; report them again only if you have something to add.
If the file is sound, record nothing. Finish with one sentence that sums up your review."""

ADD_FINDING = ToolSpec("add_finding", "Record one review finding.", {
    "type": "object",
    "properties": {
        "target": {"type": "string", "description": "cell address (Sheet!C7), paragraph id (P3) or 'file'"},
        "severity": {"type": "string", "enum": list(SEVERITIES)},
        "title": {"type": "string", "description": "one line"},
        "detail": {"type": "string", "description": "what is wrong and what to check or change"},
    },
    "required": ["target", "severity", "title"],
    "additionalProperties": False,
})


# --- automatic checks ------------------------------------------------------------------------------------------

def _tables(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for s in model["sheets"] for b in s["blocks"] if b["type"] == "table"]


def run_checks(model: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in _tables(model):
        name = t.get("title") or t["id"]
        if not t["rows"]:
            out.append({"target": f"{t['sheet']}!A{t['header_row']}", "severity": "high",
                        "title": f"“{name}” has no rows",
                        "body": "The query returned nothing. Check the filters and the period, or the source data."})
            continue
        missing = [c["a"] for cells in t["rows"] for c in cells if c.get("st") == "unverified"]
        if missing:
            shown = ", ".join(a.rsplit("!", 1)[1] for a in missing[:12]) + (" …" if len(missing) > 12 else "")
            out.append({"target": missing[0], "severity": "high",
                        "title": f"{len(missing)} figure{'s' if len(missing) > 1 else ''} in “{name}” without a source",
                        "body": f"Cells {shown} were typed in and do not appear in any tool result or message. "
                                "Confirm them against the books or replace them with a query."})
        blanks = []
        for ci, col in enumerate(t["columns"]):
            if not col.get("formula") or col.get("span"):
                continue
            for cells in t["rows"] + ([t["total"]] if t.get("total") else []):
                c = cells[ci]
                if c.get("st") == "computed" and c.get("v") is None and "/" in (c.get("x") or ""):
                    blanks.append(c["a"])
        if blanks:
            shown = ", ".join(a.rsplit("!", 1)[1] for a in blanks[:10])
            out.append({"target": blanks[0], "severity": "medium",
                        "title": f"Division by zero left {len(blanks)} cell{'s' if len(blanks) > 1 else ''} blank",
                        "body": f"{shown}: the divisor is zero or empty, so the formula shows a blank."})
    for sec in (model.get("report") or {}).get("sections", []):
        for b in sec["blocks"]:
            paras = [b] if b["type"] == "paragraph" else b.get("items", [])
            for p in paras:
                bad = [r["t"] for r in p.get("runs", []) if (r.get("fig") or {}).get("st") == "unverified"]
                if bad:
                    out.append({"target": p["id"], "severity": "high",
                                "title": f"Figure{'s' if len(bad) > 1 else ''} without a source: {', '.join(bad[:5])}",
                                "body": "Quote figures from the tables (as {{table.row.column}}) or from a tool result."})
    return out


# --- what the reviewer sees -------------------------------------------------------------------------------------

def review_text(model: dict[str, Any], checks: list[dict[str, Any]], max_rows: int = 80) -> str:
    lines = [f"FILE: {model['title']} ({model['kind']}); amounts in {model.get('currency') or 'the base currency'} "
             "unless stated."]
    for sheet in model["sheets"]:
        lines.append(f"\nSHEET “{sheet['name']}”")
        for b in sheet["blocks"]:
            if b["type"] in {"heading", "text", "note"}:
                lines.append(f"[{b['type']}] {b['text']}")
            elif b["type"] == "kpis":
                for it in b["items"]:
                    c = it["cell"]
                    lines.append(f"KPI {c['a']}: {it['label']} = {fmt_value(c.get('v'), c['f'])} (formula: {c.get('e')})")
            elif b["type"] == "table":
                cols = " | ".join(f"{c['letter']} {c['label']} [{c['format']}"
                                  + (f", formula: {c['formula']}" if c.get("formula") else "") + "]"
                                  for c in b["columns"])
                lines.append(f"Table {b['id']} “{b.get('title') or ''}” — columns: {cols}")
                rows = b["rows"][:max_rows]
                for cells in rows + ([b["total"]] if b.get("total") else []):
                    rown = cells[0]["a"].rsplit("!", 1)[1].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                    vals = []
                    for c in cells:
                        v = fmt_value(c.get("v"), c.get("f", "text"))
                        tag = (f" [{c['s']}]" if c.get("s") else " [NO SOURCE]" if c.get("st") == "unverified" else "")
                        vals.append(v + tag)
                    lines.append(f"  row {rown}: " + " | ".join(vals))
                if len(b["rows"]) > max_rows:
                    lines.append(f"  … {len(b['rows']) - max_rows} more rows")
    report = model.get("report")
    if report:
        lines.append("\nREPORT TEXT")
        for sec in report["sections"]:
            lines.append(f"## {sec['heading']}")
            for b in sec["blocks"]:
                paras = [b] if b["type"] == "paragraph" else b.get("items", [])
                if b["type"] == "table":
                    lines.append(f"(table {b['table']})")
                for p in paras:
                    text = "".join(r["t"] + (f" [{r['fig'].get('s') or r['fig'].get('c') or 'NO SOURCE'}]"
                                             if r.get("fig") else "") for r in p["runs"])
                    lines.append(f"{p['id']}: {text}")
    lines.append("\nSOURCES")
    for s in model["sources"]:
        lines.append(f"{s['id']}: {s['title']} — {s.get('system') or ''}. {s.get('detail') or ''}"[:500])
    for c in model.get("calculations", []):
        lines.append(f"{c['id']}: {c.get('e')} = {fmt_value(c.get('v'), c.get('f') or 'number')}")
    if checks:
        lines.append("\nALREADY FLAGGED BY AUTOMATIC CHECKS")
        lines.extend(f"- {c['target']}: {c['title']}" for c in checks)
    return "\n".join(lines)


def valid_targets(model: dict[str, Any]) -> set[str]:
    targets = {"file"}
    for t in _tables(model):
        for cells in t["rows"] + ([t["total"]] if t.get("total") else []):
            targets.update(c["a"] for c in cells)
        targets.add(f"{t['sheet']}!A{t['header_row']}")
    for s in model["sheets"]:
        for b in s["blocks"]:
            if b["type"] == "kpis":
                targets.update(it["cell"]["a"] for it in b["items"])
    for sec in (model.get("report") or {}).get("sections", []):
        for b in sec["blocks"]:
            for p in [b] if b["type"] == "paragraph" else b.get("items", []):
                targets.add(p["id"])
    return targets


def _normalise_target(raw: str, targets: set[str]) -> str:
    t = (raw or "file").strip().strip("'\"")
    if t in targets:
        return t
    t2 = t.replace("'", "").replace("$", "")
    if t2 in targets:
        return t2
    low = {x.lower(): x for x in targets}
    return low.get(t2.lower(), "file")


# --- the reviewer ------------------------------------------------------------------------------------------------

async def llm_review(engine: Engine, workspace: dict[str, Any], model: dict[str, Any], checks: list[dict[str, Any]],
                     user_id: str) -> tuple[list[dict[str, Any]], str, str]:
    """Returns (findings, summary, model name)."""
    provider_id, model_name = resolve_choice(engine, workspace, workspace.get("reviewer_provider_id"),
                                             workspace.get("reviewer_model"))
    llm = build_model(engine, workspace["id"], provider_id, model_name)
    ctx = ToolContext(engine, workspace, user_id, None, None, "review")
    tools = {n: t for n, t in (await engine.tools.for_context(ctx)).items()
             if t.category in REVIEW_CATEGORIES and not t.needs_approval() and n not in EXCLUDED_TOOLS}
    specs = [ADD_FINDING] + [t.spec() for t in tools.values()]
    targets = valid_targets(model)
    messages = [ChatMessage("system", REVIEWER_PROMPT),
                ChatMessage("user", "Review this file.\n\n" + review_text(model, checks))]
    findings: list[dict[str, Any]] = []
    summary = ""
    usage = Usage()
    for _ in range(MAX_STEPS):
        result = None
        async for ev in llm.stream(messages, specs):
            if ev.type == "done":
                result = ev.result
        assert result is not None
        usage.add(result.usage)
        engine.record_usage(workspace["id"], provider_id, model_name, result.usage, "review")
        if not result.tool_calls:
            summary = result.text.strip()
            break
        messages.append(ChatMessage("assistant", result.text, result.tool_calls))
        for call in result.tool_calls:
            if call.name == "add_finding":
                a = call.arguments or {}
                sev = a.get("severity") if a.get("severity") in SEVERITIES else "medium"
                title = str(a.get("title") or "").strip()[:300]
                if title:
                    findings.append({"target": _normalise_target(str(a.get("target") or "file"), targets),
                                     "severity": sev, "title": title, "body": str(a.get("detail") or "")[:2000]})
                text = "Recorded."
            elif call.name in tools:
                out: ToolOutput = await tools[call.name].run(ctx, call.arguments or {})
                text = out.for_model(6000)
            else:
                text = f"Unknown tool {call.name}."
            messages.append(ChatMessage("tool", text, tool_call_id=call.id, name=call.name))
    else:
        summary = "The reviewer stopped after the step limit."
    return findings[:60], summary[:1000], model_name


def findings_json(findings: list[dict[str, Any]]) -> str:
    return json.dumps(findings)
