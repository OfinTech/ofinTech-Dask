"""Agent tools over cost centres (read-only: the assistant reports; people define cost centres on the page)."""

from __future__ import annotations

from typing import Any

from ..errors import Invalid, NotFound
from .tools import ToolContext, ToolOutput, registry, schema, table_block, table_text


def _has(ctx: ToolContext) -> bool:
    return ctx.engine.db.one("SELECT 1 FROM cost_centres WHERE workspace_id = ? LIMIT 1", (ctx.ws_id,)) is not None


def _money(v: Any) -> str:
    return "—" if v is None else f"{float(v):,.2f}"


@registry.tool("list_cost_centres", "List the workspace's cost centres (projects, departments) with cost to date, "
               "budget, share used and when the budget runs out at the current pace.", schema({}),
               category="costcentres", available=_has)
async def _list(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    items = await ctx.engine.costcentres.summaries(ctx.ws_id)
    cols = [{"key": k, "label": label, "format": f} for k, label, f in (
        ("name", "Cost centre", "text"), ("cost", "Cost to date", "money"), ("budget", "Budget", "money"),
        ("used_pct", "Used %", "number"), ("cash_paid", "Cash paid", "money"), ("unpaid", "Unpaid", "money"),
        ("run_out_date", "Budget runs out", "date"), ("untagged_share", "Untagged %", "number"))]
    rows = [[i["name"], i.get("cost"), i.get("budget"), i.get("used_pct"), i.get("cash_paid"), i.get("unpaid"),
             i.get("exceeded_on") and f"exceeded {i['exceeded_on']}" or i.get("run_out_date"), i.get("untagged_share")]
            for i in items]
    errors = [f"- {i['name']}: {i['error']}" for i in items if i.get("error")]
    return ToolOutput(table_text(cols, rows) + ("\nNot available:\n" + "\n".join(errors) if errors else ""),
                      [table_block(cols, rows, "Cost centres")], items)


@registry.tool("cost_centre_report", "A cost centre's position from the general ledger: cost and revenue picked up by "
               "its rules, how much was tagged in the ledger, accrual cost against cash paid from the bank (paid "
               "directly, through supplier bills, still unpaid), budget lines from the customer's file, and the date "
               "the budget runs out at the current pace. Optional as_of (YYYY-MM-DD).",
               schema({"cost_centre": {"type": "string", "description": "name, code or id"},
                       "as_of": {"type": "string"}}, ["cost_centre"]),
               category="costcentres", available=_has)
async def _report(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        r = await ctx.engine.costcentres.report(ctx.ws_id, a["cost_centre"], a.get("as_of"))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    cc, t, c, ro, b = r["cost_centre"], r["totals"], r["cash"], r["runout"], r["budget"]
    lines = [f"Cost centre {cc['name']} as of {r['as_of']} (ledger “{r['ledger']['name']}”, rules version "
             f"{r['rules_version']}).",
             "Rules: " + "; ".join(r["rules"]) + (" — except " + "; ".join(r["excludes"]) if r["excludes"] else ""),
             f"Cost {_money(t['cost'])}, revenue {_money(t['revenue'])}, margin {_money(t['margin'])}; "
             f"{t['matched_lines']} ledger lines.",
             f"Tagged in the ledger {_money(t['tagged_cost'])}; picked up by account/party/keyword rules "
             f"{_money(t['rule_cost'])} (untagged share {t['untagged_share']}%).",
             f"Cash: paid directly {_money(c['paid_direct'])}; through supplier bills {_money(c['through_payables'])}, "
             f"of which paid {_money(c['paid_through_payables'])}, settled otherwise {_money(c['settled_other'])}, "
             f"unpaid {_money(c['unpaid'])}; not cash (accruals, depreciation) {_money(c['not_cash'])}. "
             f"Cash paid in total {_money(c['cash_paid'])}. Method: {c['method']}"]
    if ro["budget"] is None:
        lines.append("No budget is set.")
    else:
        status = {"exceeded": f"the budget was exceeded on {ro['exceeded_on']}",
                  "before_end": f"at this pace the budget runs out on {ro['run_out_date']}, {ro['days_before_end']} "
                                f"days before the planned end {ro['end_date']}",
                  "after_end": f"at this pace the budget lasts until {ro['run_out_date']}, after the planned end "
                               f"{ro['end_date']}",
                  "open_ended": f"at this pace the budget runs out on {ro['run_out_date']}",
                  "no_spend": "there was no cost in the measuring window, so no run-out date",
                  "too_early": "fewer than 14 days of history, so no run-out date yet"}.get(ro["status"], ro["status"])
        lines.append(f"Budget {_money(ro['budget'])}, spent {_money(ro['spent'])}, remaining {_money(ro['remaining'])}; "
                     f"pace {_money(ro['per_month'])} a month; {status}. Method: {ro['method']}")
        if ro.get("projected_at_end") is not None:
            lines.append(f"Projected cost at the planned end: {_money(ro['projected_at_end'])} "
                         f"({_money(ro['over_at_end'])} against the budget).")
    for key, label in (("possibly_missing", "Cost lines possibly missing from this cost centre"),
                       ("unassigned_bank", "Bank payments not charged to any cost centre that may belong here")):
        if r[key]["count"]:
            lines.append(f"{label}: {r[key]['count']} for {_money(r[key]['amount'])}.")
    if r["overlaps"]["count"]:
        lines.append(f"Also counted by other cost centres: {r['overlaps']['count']} lines for "
                     f"{_money(r['overlaps']['amount'])}.")
    blocks: list[dict[str, Any]] = [{"type": "kpis", "items": [
        {"label": "Cost to date", "value": t["cost"], "format": "money"},
        {"label": "Budget", "value": t["budget"], "format": "money"},
        {"label": "Cash paid", "value": c["cash_paid"], "format": "money"},
        {"label": "Unpaid", "value": c["unpaid"], "format": "money"}]}]
    if b["lines"]:
        cols = [{"key": k, "label": label, "format": f} for k, label, f in (
            ("label", "Budget line", "text"), ("amount", "Budget", "money"), ("actual", "Actual", "money"),
            ("used_pct", "Used %", "number"), ("run_out_date", "Runs out", "date"), ("source", "Source", "text"))]
        rows = [[x["label"], x["amount"], x["actual"], x["used_pct"], x["exceeded_on"] and f"exceeded {x['exceeded_on']}"
                 or x["run_out_date"], (x["source"] or {}).get("cell")] for x in b["lines"]]
        lines.append(table_text(cols, rows, 30))
        blocks.append(table_block(cols, rows, "Budget lines"))
    return ToolOutput("\n".join(lines), blocks, r)


@registry.tool("cost_centre_lines", "The general-ledger lines behind a cost centre: 'matched' (what its rules picked "
               "up, with the rule and share), 'possibly_missing' (cost from its suppliers or naming it that no cost "
               "centre took) or 'unassigned_bank' (bank payments charged to no cost centre that may belong to it).",
               schema({"cost_centre": {"type": "string"},
                       "kind": {"type": "string", "enum": ["matched", "possibly_missing", "unassigned_bank"]},
                       "limit": {"type": "integer"}}, ["cost_centre"]),
               category="costcentres", available=_has)
async def _lines(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        r = await ctx.engine.costcentres.matched(ctx.ws_id, a["cost_centre"], a.get("kind") or "matched",
                                                 limit=min(int(a.get("limit") or 50), 200))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    if r["kind"] == "unassigned_bank":
        cols = [{"key": k, "label": label, "format": f} for k, label, f in (
            ("date", "Date", "date"), ("entry", "Entry", "text"), ("amount", "Paid out", "money"),
            ("party", "Party", "text"), ("description", "Description", "text"), ("reason", "Why", "text"))]
        rows = [[x["date"], x["entry"], x["amount"], x["party"], x["description"], x["reason"]] for x in r["lines"]]
    else:
        cols = [{"key": k, "label": label, "format": f} for k, label, f in (
            ("date", "Date", "date"), ("entry", "Entry", "text"), ("account", "Account", "text"),
            ("debit", "Debit", "money"), ("credit", "Credit", "money"), ("party", "Party", "text"),
            ("description", "Description", "text"), ("how", "How", "text"))]
        rows = [[x["date"], x["entry"], f"{x['account_code'] or ''} {x['account']}".strip(), x["debit"], x["credit"],
                 x["party"], x["description"], x.get("reason") or (f"{x['basis']} rule {x['rule'] + 1}"
                                                                    + (f", {x['share'] * 100:g}%" if x["share"] < 1 else ""))]
                for x in r["lines"]]
    return ToolOutput(f"{r['total']} lines.\n" + table_text(cols, rows, 50), [table_block(cols, rows)], r)
