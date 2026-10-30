"""Agent tools over the KPI scorecard and forecasts (read-only: people set targets, formulas and assumptions)."""

from __future__ import annotations

from typing import Any

import re

from ..analysis.kpis import CATALOG
from ..errors import Invalid, NotFound
from .tools import ToolContext, ToolOutput, registry, schema, table_block, table_text


KPI_IDS = [k["id"] for k in CATALOG]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")) if w not in {"the", "of", "to", "our"}}


def match_kpi(kpis: list[dict[str, Any]], asked: str) -> dict[str, Any] | None:
    """The KPI the model meant: its id ("gross_margin", "gross margin", "Gross-Margin"), its name, a name that
    contains what was asked, or a name whose words all appear in it ("gross profit margin")."""
    want = re.sub(r"[\s\-]+", "_", asked.strip().lower())
    for k in kpis:
        if want in (k["id"], re.sub(r"[\s\-]+", "_", k["name"].lower())):
            return k
    plain = asked.strip().lower()
    hit = next((k for k in kpis if plain and plain in k["name"].lower()), None)
    if hit:
        return hit
    asked_words = _words(asked)
    for k in kpis:
        core = _words(re.sub(r"\(.*?\)", "", k["name"]))  # "Days sales outstanding (DSO)" -> days sales outstanding
        if core and core <= asked_words:
            return k
    return None


def _has_ledger(ctx: ToolContext) -> bool:
    try:
        return bool(ctx.engine.costcentres.ledgers(ctx.ws_id))
    except Exception:  # noqa: BLE001
        return False


def fmt(v: float | None, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "percent":
        return f"{v * 100:,.1f}%"
    if unit == "days":
        return f"{v:,.0f} days"
    if unit == "months":
        return f"{v:,.1f} months"
    if unit == "ratio":
        return f"{v:,.2f}×"
    return f"{v:,.2f}"


@registry.tool("financial_kpis", "The company's KPI scorecard from the general ledger: growth and profitability, "
               "liquidity, working capital, leverage, cash flow and cost control, each with its value, the previous "
               "month, the target and status set by the finance lead. Optional month (YYYY-MM) and category. For one KPI "
               "(explain it, break it down, what drives it) use kpi_detail instead.",
               schema({"month": {"type": "string"},
                       "category": {"type": "string", "enum": ["growth_profitability", "liquidity", "working_capital",
                                                               "leverage", "cash_flow", "cost_control"]}}),
               category="analysis", available=_has_ledger)
async def _kpis(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        r = await ctx.engine.analysis.scorecard(ctx.ws_id, a.get("month"))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    cards = [k for k in r["kpis"] if not a.get("category") or k["category"] == a["category"]]
    cols = [{"key": k, "label": label, "format": "text"} for k, label in (
        ("name", "KPI"), ("value", "Value"), ("previous", "Previous month"), ("target", "Target"), ("status", "Status"),
        ("formula", "Formula"))]
    rows = [[k["name"], fmt(k["value"], k["unit"]), fmt(k["previous"], k["unit"]),
             fmt(k["target"], k["unit"]) if k["target"] is not None else "—", k["status"] or "—", k["formula"]]
            for k in cards]
    notes = [f"Ledger “{r['ledger']['name']}”, month {r['month']}, lines from {r['ledger']['first_date']} to "
             f"{r['ledger']['last_date']}. Account roles confirmed: {r['roles']['confirmed']} of {r['roles']['accounts']}."]
    if not r["balance"]["balanced"]:
        notes.append(f"Warning: the ledger does not balance (difference {r['balance']['difference']:,.2f}).")
    if r["opening"].get("source") == "balance_sheet" and r["opening"].get("unmatched_accounts"):
        notes.append(f"{r['opening']['unmatched_accounts']} balance-sheet accounts had no opening balance.")
    return ToolOutput("\n".join(notes) + "\n" + table_text(cols, rows, 40), [table_block(cols, rows, "KPIs")], r)


@registry.tool("kpi_detail", "One KPI explained: its formula, the last 24 months, and the measures and ledger accounts "
               "behind the latest value. Use it when the user asks about one KPI: to explain it, break it down or say "
               "what drives it.",
               schema({"kpi": {"type": "string", "description": "the KPI id, in English whatever the user's language: "
                               + ", ".join(KPI_IDS)},
                       "month": {"type": "string", "description": "YYYY-MM; the latest month when left out"}}, ["kpi"]),
               category="analysis", available=_has_ledger)
async def _detail(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        sc = await ctx.engine.analysis.scorecard(ctx.ws_id)
        k = match_kpi(sc["kpis"], a["kpi"])
        if k is None:
            return ToolOutput(f"No KPI matches “{a['kpi']}”. Ids: " + ", ".join(x["id"] for x in sc["kpis"]))
        d = await ctx.engine.analysis.detail(ctx.ws_id, k["id"], a.get("month"))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    unit = d["kpi"]["unit"]
    lines = [f"{d['kpi']['name']} = {d['kpi']['formula']}; {d['month']}: {fmt(d['value'], unit)}.",
             "Last months: " + ", ".join(f"{s['month']} {fmt(s['value'], unit)}" for s in d["series"][-12:])]
    for p in d["parts"]:
        shown = "—" if p["value"] is None else f"{p['value']:,.2f}"
        lines.append(f"- {p['name']} ({p['description']}): {shown}"
                     + ("; accounts: " + "; ".join(f"{x['account']} {x['amount']:,.2f}" for x in p["accounts"][:6])
                        if p["accounts"] else ""))
    return ToolOutput("\n".join(lines), [], d)


@registry.tool("forecast_report", "A forecast set by the finance lead: status (draft, submitted, approved, plan), "
               "assumptions per line, projected revenue, net profit and cash, the lowest cash month, the statistical "
               "reference and, for approved forecasts, accuracy against actuals. Without a name, lists the forecasts.",
               schema({"forecast": {"type": "string", "description": "name or id"}}),
               category="analysis", available=_has_ledger)
async def _forecast(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    items = ctx.engine.analysis.list_forecasts(ctx.ws_id)
    if not items:
        return ToolOutput("There are no forecasts yet. The finance team creates them under Financial analysis → "
                          "Forecasts.")
    if not a.get("forecast"):
        return ToolOutput("\n".join(f"- {x['name']} · {x['scenario']} v{x['version']} ({x['status']}"
                                    f"{', plan' if x['is_plan'] else ''}), from {x['start_month']}, {x['months']} months"
                                    for x in items), [], items)
    want = a["forecast"].lower()
    hits = [x for x in items if x["id"] == a["forecast"] or want in f"{x['name']} {x['scenario']}".lower()]
    # the plan first, then approved versions, then the latest draft
    f = min(hits, key=lambda x: (not x["is_plan"], x["status"] != "approved", -x["updated_at"]), default=None)
    if f is None:
        return ToolOutput(f"No forecast matches “{a['forecast']}”.")
    v = await ctx.engine.analysis.forecast_view(ctx.ws_id, f["id"])
    s, res = v["result"]["summary"], v["result"]
    out = [f"{f['name']} · {f['scenario']} v{f['version']}, {f['status']}{' (the plan)' if f['is_plan'] else ''}; "
           f"{res['months'][0]} to {res['months'][-1]}.",
           f"Revenue {s['revenue']:,.2f}; net profit {s['net_profit']:,.2f}; ending cash {s['ending_cash']:,.2f}; lowest "
           f"cash {s['lowest_cash']:,.2f} in {s['lowest_cash_month']}"
           + (f"; cash goes below zero from {s['cash_negative_from']}." if s["cash_negative_from"] else "."),
           "Assumptions: " + "; ".join(f"{line} {x['method']}" + (f" {x.get('value')}" if x.get("value") is not None else "")
                                       + (f" of {x['of']}" if x.get("of") else "")
                                       for line, x in v["forecast"]["assumptions"]["lines"].items())
           + f"; DSO {v['forecast']['assumptions'].get('dso')}, DPO {v['forecast']['assumptions'].get('dpo')}, "
             f"DIO {v['forecast']['assumptions'].get('dio')}."]
    ref = res.get("reference")
    if ref:
        out.append(f"Statistical reference ({ref['method']}): revenue {sum(ref['values']):,.2f} over the period"
                   + (f"; its error on the last {ref['backtest_months']} months was {ref['backtest_mape'] * 100:.1f}%."
                      if ref.get("backtest_mape") is not None else "."))
    if v["accuracy"] and v["accuracy"]["revenue_mape"] is not None:
        out.append(f"Accuracy so far: revenue error {v['accuracy']['revenue_mape'] * 100:.1f}% over "
                   f"{len(v['accuracy']['months'])} closed months (average bias {v['accuracy']['revenue_bias']:,.2f}).")
    return ToolOutput("\n".join(out), [{"type": "kpis", "items": [
        {"label": "Revenue", "value": s["revenue"], "format": "money"},
        {"label": "Net profit", "value": s["net_profit"], "format": "money"},
        {"label": "Ending cash", "value": s["ending_cash"], "format": "money"}]}], v)
