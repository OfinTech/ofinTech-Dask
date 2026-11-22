"""Agent tools over the KPI scorecard and forecasts (read-only: people set targets, formulas and assumptions)."""

from __future__ import annotations

from typing import Any

import re
from decimal import ROUND_HALF_UP, Decimal

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


def _month_problem(r: dict[str, Any], asked: str | None) -> str | None:
    """A month the ledger does not cover is said so, before any figure: never answered with another month's figures."""
    if not asked:
        return None
    import re as _re
    if not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", asked):
        return f"“{asked}” is not a month; use YYYY-MM, for example 2026-10."
    months = r.get("months") or []
    if asked not in months:
        span = f"{months[0]} to {months[-1]}" if months else "no months yet"
        return (f"No ledger data for {asked}: the ledger covers {span}. No figures are given for {asked}; ask about a "
                "month inside that range, or import or sync the missing period first.")
    return None


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
               category="analysis", available=_has_ledger, offload=True)
async def _kpis(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        r = await ctx.engine.analysis.scorecard(ctx.ws_id, a.get("month"))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    if problem := _month_problem(r, a.get("month")):
        return ToolOutput(problem, [], {"month": a.get("month"), "available": False})
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
               category="analysis", available=_has_ledger, offload=True)
async def _detail(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        sc = await ctx.engine.analysis.scorecard(ctx.ws_id)
        if problem := _month_problem(sc, a.get("month")):
            return ToolOutput(problem, [], {"month": a.get("month"), "available": False})
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
               category="analysis", available=_has_ledger, offload=True)
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


# --- the statements pages, read as they are ----------------------------------------------------------------------
# The Statements, Receivables, Payables and Anomalies pages are computed in analysis/statements.py from the same
# ledger as the KPIs. Until these tools existed the assistant could not read them: asked for net profit or for what
# is more than 90 days old, it queried the raw ledger lines and did the arithmetic itself, and the answer disagreed
# with the page the user had open. These tools hand it the page's own figures. They are in the core group
# (category "finance"), so they are offered whenever the workspace has a ledger, whatever words the user chose.

PL_LABELS = {"revenue": "Revenue", "cogs": "Cost of sales", "gross_profit": "Gross profit", "payroll": "Payroll",
             "opex": "Other operating expenses", "depreciation": "Depreciation", "operating_profit": "Operating profit",
             "other_income": "Other income", "other_expense": "Other expenses", "interest": "Interest",
             "tax": "Tax on profit", "net_profit": "Net profit"}
BS_LABELS = {"cash": "Bank and cash", "receivables": "Receivables", "inventory": "Inventory",
             "other_current_assets": "Other current assets", "fixed_assets": "Fixed assets",
             "other_non_current_assets": "Other non-current assets", "payables": "Payables",
             "other_current_liabilities": "Other current liabilities", "short_term_debt": "Short-term debt",
             "long_term_debt": "Long-term debt", "other_non_current_liabilities": "Other non-current liabilities",
             "current_assets": "Total current assets", "non_current_assets": "Total non-current assets",
             "current_liabilities": "Total current liabilities", "non_current_liabilities": "Total non-current liabilities",
             "total_assets": "Total assets", "total_liabilities": "Total liabilities",
             "equity": "Equity (assets less liabilities)", "working_capital": "Working capital", "net_debt": "Net debt"}
CF_LABELS = [("opening", "Opening cash"), ("operating", "Operating cash flow"), ("investing", "Investing cash flow"),
             ("financing", "Financing cash flow"), ("net", "Net change in cash"), ("closing", "Closing cash"),
             ("free", "Free cash flow"), ("cash_in", "Cash received"), ("cash_out", "Cash paid")]
BUCKET_LABELS = ["0-30 days", "31-60 days", "61-90 days", "Over 90 days"]


def _provenance_note(r: dict[str, Any]) -> str:
    kind = (r.get("provenance") or {}).get("kind")
    if kind == "unverified":
        return " The account roles are not confirmed by finance yet, so the figures are unverified."
    return ""


def _percent(share: float) -> str:
    """0.0705 -> "7.1%": rounded half up, as the page rounds it (a float would give 7.0)."""
    return f"{(Decimal(str(share)) * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _statement_rows(r: dict[str, Any], statement: str) -> list[list[Any]]:
    """The rows of one statement, in the order of the page: a label, then one value per month in r["columns"]."""
    if statement == "profit_and_loss":
        pl = r["profit_and_loss"]
        rows: list[list[Any]] = []
        costs = [0.0] * len(r["columns"])
        for ln in pl["lines"]:
            rows.append([PL_LABELS.get(ln["measure"], ln["measure"]), *ln["values"], ln["ytd"]])
            if ln["kind"] == "cost":
                costs = [c + (v or 0.0) for c, v in zip(costs, ln["values"], strict=True)]
            if ln["measure"] in pl["margins"]:
                rows.append([f"{PL_LABELS.get(ln['measure'], ln['measure'])}, % of revenue",
                             *[None if m is None else _percent(m) for m in pl["margins"][ln["measure"]]], None])
        ytd_costs = sum(ln["ytd"] or 0.0 for ln in pl["lines"] if ln["kind"] == "cost")
        # the Overview's "Expenses": every cost line together (cost of sales, payroll, other operating expenses,
        # depreciation, other expenses, interest and tax), so that revenue less this is net profit
        rows.append(["Total expenses (all cost lines)", *[round(c, 2) for c in costs], round(ytd_costs, 2)])
        return rows
    if statement == "balance_sheet":
        bs = r["balance_sheet"]
        rows = []
        for section in bs["sections"]:
            rows += [[BS_LABELS.get(x["measure"], x["measure"]), *x["values"]] for x in section["rows"]]
            rows.append([BS_LABELS[section["section"]], *section["total"]])
        rows += [[BS_LABELS[k], *bs["totals"][k]] for k in ("total_assets", "total_liabilities", "equity",
                                                              "working_capital", "net_debt")]
        return rows
    return [[label, *[m[key] for m in r["cash_flow"]]] for key, label in CF_LABELS]


@registry.tool("financial_statements", "The company's monthly statements exactly as the Statements page shows them, "
               "from the general ledger: profit_and_loss (revenue, cost of sales, gross profit, payroll, other operating "
               "expenses, depreciation, operating profit, interest, tax, net profit, total expenses, margins, year to "
               "date), balance_sheet (bank and cash, receivables, payables, debt, equity, working capital) or cash_flow. "
               "Use this, not query_dataset, whenever the user asks for revenue, expenses, profit, margin, cash, "
               "balances or any statement line for a month or several months: copy the figures as given, do not "
               "recompute them from ledger lines.",
               schema({"statement": {"type": "string", "enum": ["profit_and_loss", "balance_sheet", "cash_flow"]},
                       "month": {"type": "string", "description": "the last month shown, YYYY-MM; the latest when left out"},
                       "months": {"type": "integer", "minimum": 1, "maximum": 12,
                                  "description": "how many months to show, ending at month; 3 when left out"}},
                      ["statement"]),
               available=_has_ledger, offload=True)
async def _statements(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        periods = max(1, min(12, int(a.get("months") or 3)))
        r = ctx.engine.statements.statements(ctx.ws_id, ctx.workspace, a.get("month"), periods)
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    if problem := _month_problem(r, a.get("month")):
        return ToolOutput(problem, [], {"month": a.get("month"), "available": False})
    statement = a["statement"]
    rows = _statement_rows(r, statement)
    cols = [{"key": "line", "label": "Line", "format": "text"}] + [
        {"key": m, "label": m, "format": "money"} for m in r["columns"]]
    if statement == "profit_and_loss":
        cols.append({"key": "ytd", "label": f"Year to date (from {r['fiscal_year_start']})", "format": "money"})
    title = {"profit_and_loss": "Profit and loss", "balance_sheet": "Balance sheet", "cash_flow": "Cash flow"}[statement]
    note = (f"{title} from the general ledger “{r['ledger']['name']}”, {r['columns'][0]} to {r['columns'][-1]}, "
            f"in {r.get('currency') or 'the base currency'}."
            + {"profit_and_loss": " Costs are positive amounts.", "balance_sheet": " Balances are month-end.",
               "cash_flow": ""}[statement] + _provenance_note(r))
    return ToolOutput(note + "\n" + table_text(cols, rows, 60), [table_block(cols, rows, title)],
                      {"statement": statement, "columns": r["columns"], "rows": rows, "currency": r.get("currency")})


@registry.tool("ageing_report", "What customers owe (receivables) or what is owed to suppliers (payables) at a "
               "month-end, by party, exactly as the Receivables and Payables pages show it: opening, raised and settled "
               "in the month, closing balance, the balance aged from the document date into 0-30, 31-60, 61-90 and over "
               "90 days (each payment settles the oldest open documents first), payments not matched to any document "
               "(advances, overpayments), the oldest open document, and the totals. Use this, not query_dataset, for "
               "who owes what, the largest customer or supplier balance, overdue or old invoices and bills, advances "
               "and unapplied payments, and ageing: copy the figures as given.",
               schema({"kind": {"type": "string", "enum": ["receivables", "payables"]},
                       "month": {"type": "string", "description": "YYYY-MM; the latest month when left out"}}, ["kind"]),
               available=_has_ledger, offload=True)
async def _ageing(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        r = ctx.engine.statements.parties(ctx.ws_id, ctx.workspace, a["kind"], a.get("month"))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    if problem := _month_problem(r, a.get("month")):
        return ToolOutput(problem, [], {"month": a.get("month"), "available": False})
    who = "Customer" if r["kind"] == "receivables" else "Supplier"
    cols = [{"key": "party", "label": who, "format": "text"}] + [
        {"key": k, "label": label, "format": "money"} for k, label in (
            ("opening", "Opening"), ("raised", "Invoiced in month" if r["kind"] == "receivables" else "Billed in month"),
            ("settled", "Settled in month"), ("closing", "Closing balance"))] + [
        {"key": f"b{n}", "label": label, "format": "money"} for n, label in enumerate(BUCKET_LABELS)] + [
        {"key": "over30", "label": "Over 30 days", "format": "money"},
        {"key": "unapplied", "label": "Unapplied payments", "format": "money"},
        {"key": "oldest", "label": "Oldest open", "format": "date"}]
    rows = [[x["party"], x["opening"], x["raised"], x["settled"], x["closing"], *x["buckets"], x["overdue_30"],
             x["unapplied"], x["oldest"] or "—"] for x in r["rows"]]
    t = r["totals"]
    rows.append(["Total", None, t["raised"], t["settled"], t["closing"], *t["buckets"], t["overdue_30"],
                 t["unapplied"], "—"])
    # A payment that settled no document: an advance, an overpayment, a credit not yet matched. The page keeps it
    # per party and the closing balance is already net of it; until 23 Nov 2026 this tool dropped the column, so
    # the assistant, asked whether any customer had paid in advance, could only say the data did not show it.
    advances = [x for x in r["rows"] if (x["unapplied"] or 0) > 0.005]
    paid_by = "received from" if r["kind"] == "receivables" else "paid to"
    label = "Owed by customers" if r["kind"] == "receivables" else "Owed to suppliers"
    note = (f"{label} at {r['as_of']}: {t['closing']:,.2f} {r.get('currency') or ''}".rstrip()
            + f" across {r['count']} parties; over 30 days {t['overdue_30']:,.2f}; over 90 days {t['buckets'][3]:,.2f}. "
            + (f"Agrees with the ledger balance of {r['ledger_balance']:,.2f}."
               if abs(r["difference"] or 0) < 0.005 else
               f"The ledger balance is {r['ledger_balance']:,.2f}: {r['difference']:,.2f} is not explained by party.")
            + " Aged from the document date, not from a due date."
            + (" Unapplied payments (advances or overpayments " + paid_by + " a party and matched to no document; the "
               "closing balance is net of them): " + "; ".join(f"{x['party']} {x['unapplied']:,.2f}" for x in advances) + "."
               if advances else " No unapplied payments.")
            + _provenance_note(r))
    return ToolOutput(note + "\n" + table_text(cols, rows, 61), [table_block(cols, rows, label)], r)


@registry.tool("ledger_anomalies", "Postings in a month that break the pattern of the previous months, exactly as the "
               "Anomalies page shows them: an unusual amount on an account against its recent average, an account that "
               "is posted every month but not this month (a missing depreciation or accrual), a new account, and "
               "possible duplicate entries. Use it when the user asks what is unusual, missing or worth reviewing "
               "before the close.",
               schema({"month": {"type": "string", "description": "YYYY-MM; the latest month when left out"}}),
               available=_has_ledger, offload=True)
async def _anomalies(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        r = ctx.engine.statements.anomalies(ctx.ws_id, ctx.workspace, a.get("month"))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    if problem := _month_problem(r, a.get("month")):
        return ToolOutput(problem, [], {"month": a.get("month"), "available": False})
    lines = []
    for x in r["items"]:
        line = PL_LABELS.get(x["measure"], x["measure"])
        if x["kind"] == "unusual_amount":
            change = "" if x.get("change") is None else f" ({x['change'] * 100:+.0f}%)"
            lines.append(f"- Unusual amount, {x['account']} ({line}): {x['value']:,.2f} this month against an average of "
                         f"{x['average']:,.2f} over the previous six months{change}.")
        elif x["kind"] == "missing_posting":
            lines.append(f"- Nothing posted, {x['account']} ({line}): posted every month recently (about "
                         f"{x['average']:,.2f} a month) but nothing this month.")
        elif x["kind"] == "new_account":
            lines.append(f"- New account, {x['account']} ({line}): {x['value']:,.2f} this month, nothing before.")
        else:
            lines.append(f"- Possible duplicate, {x['account']} ({line}): {x['value']:,.2f}"
                         + (f" to {x['party']}" if x.get("party") else "")
                         + f" in entries {x['entries'][0]} and {x['entries'][1]} ({x['dates'][0]}, {x['dates'][1]}).")
    head = (f"Anomalies in {r['month']}: {r['count']} found; amounts under {r['materiality']:,.2f} are ignored."
            + _provenance_note(r))
    return ToolOutput(head + ("\n" + "\n".join(lines) if lines else " Nothing breaks the pattern of the previous months."),
                      [], r)
