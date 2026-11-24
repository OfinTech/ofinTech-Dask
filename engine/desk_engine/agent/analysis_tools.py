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
            # the average is over the six months before it, or over as many as the ledger has
            span = "six months" if r["history_months"] >= 6 else f"{r['history_months']} months"
            lines.append(f"- Unusual amount, {x['account']} ({line}): {x['value']:,.2f} this month against an average of "
                         f"{x['average']:,.2f} over the previous {span}{change}.")
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


def _has_ledger_and_data(ctx: ToolContext) -> bool:
    """A ledger, and at least one dataset that is not a ledger (a statement to hold against it)."""
    try:
        from ..costcentres.service import is_ledger
        from ..data.store import list_datasets
        sets = list_datasets(ctx.engine, ctx.ws_id)
        return any(is_ledger(d) for d in sets) and any(not is_ledger(d) for d in sets)
    except Exception:  # noqa: BLE001
        return False


def _move(x: dict[str, Any]) -> str:
    return f"received {x['in']:,.2f}" if x["in"] else f"paid {x['out']:,.2f}"


@registry.tool("bank_reconciliation", "Compares a bank statement (an imported dataset) with the general ledger's bank "
               "account for one month: the statement's closing balance and the ledger's, the difference, every "
               "movement that is in the ledger and not on the statement (payments and deposits the bank has not "
               "seen), every movement on the statement and not in the ledger (charges, interest, receipts not "
               "recorded), and how much of the difference those items explain. Movements are matched on direction "
               "and amount to the cent, on the same date, then within a few days, then on the amount alone. Use it "
               "for every question about a bank statement against the books; report what it lists and do not match "
               "the lines yourself with query_dataset. It reads only: nothing is posted.",
               schema({"statement": {"type": "string", "description": "the statement's dataset name or id; may be left "
                                     "out when the workspace has one bank statement"},
                       "month": {"type": "string", "description": "YYYY-MM; the statement's last month when left out"},
                       "account": {"type": "string", "description": "the ledger's bank account, by code or name, when "
                                   "the ledger has several bank and cash accounts"},
                       "columns": {"type": "object", "description": "the statement's column names, only when the tool "
                                   "says it cannot tell them", "properties": {
                                       k: {"type": "string"} for k in ("date", "money_in", "money_out", "amount",
                                                                       "balance", "description")}}}),
               available=_has_ledger_and_data, offload=True)
async def _bank_reconciliation(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        r = ctx.engine.statements.bank_reconciliation(ctx.ws_id, ctx.workspace, a.get("statement"), a.get("month"),
                                                      a.get("account"), a.get("columns"))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    cur = r.get("currency") or ""

    def money(v: float | None) -> str:
        return "not on the statement" if v is None else f"{v:,.2f}"
    lines = [f"Bank reconciliation for {r['month']}: the statement “{r['statement']['name']}” against the general "
             f"ledger “{r['ledger']['name']}”, account {', '.join(r['accounts'])}" + (f", in {cur}." if cur else ".")]
    if r["statement_closing"] is None:
        lines.append(f"The statement has no balance column, so balances are not compared. Ledger balance at "
                     f"{r['as_of']}: {r['book_closing']:,.2f}.")
    else:
        lines.append(f"Statement closing balance at {r['statement_date']}: {r['statement_closing']:,.2f} (its last "
                     f"row of the month). Ledger balance at {r['as_of']}: {r['book_closing']:,.2f}. Difference, "
                     f"statement less ledger: {r['difference']:,.2f}.")
        gap = r["opening_difference"] or 0.0
        lines.append(f"Opening balances: statement {money(r['statement_opening'])}, ledger {r['book_opening']:,.2f}"
                     + (" (they agree)." if abs(gap) < 0.005 else
                        f" (they differ by {gap:,.2f}: items from before {r['month']} are still open, and are not "
                        "listed here)."))
    by = r["matched_by"]
    lines.append(f"Matched {r['matched']} movements, out of {r['statement_movements']} on the statement and "
                 f"{r['book_movements']} in the ledger: {by.get('same date', 0)} on the same date, "
                 f"{by.get('within ' + str(r['window_days']) + ' days', 0)} within {r['window_days']} days, "
                 f"{by.get('amount only', 0)} on the amount alone.")
    lines.append(f"In the ledger, not on the statement ({len(r['book_only'])}):"
                 + ("" if r["book_only"] else " none."))
    lines += [f"- {x['entry']}, {x['date']}, {_move(x)}" + (f", {x['text']}" if x["text"] else "")
              for x in r["book_only"][:40]]
    lines.append(f"On the statement, not in the ledger ({len(r['bank_only'])}):"
                 + ("" if r["bank_only"] else " none."))
    lines += [f"- row {x['row']}, {x['date']}, {_move(x)}" + (f", {x['text']}" if x["text"] else "")
              for x in r["bank_only"][:40]]
    hidden = max(0, len(r["book_only"]) - 40) + max(0, len(r["bank_only"]) - 40)
    if hidden:
        lines.append(f"… {hidden} more unmatched movements are in the table.")
    if r["amount_only"]:
        lines.append("Matched on the amount alone, dates apart (worth a look): " + "; ".join(
            f"{m['book']['entry']} ({m['book']['date']}) with row {m['statement']['row']} ({m['statement']['date']}), "
            f"{_move(m['statement'])}" for m in r["amount_only"][:10]) + ".")
    if r["difference"] is not None:
        lines.append(f"The unmatched items explain {r['explained']:,.2f} of the difference; unexplained: "
                     f"{r['unexplained']:,.2f}.")
    if r["other_cash_accounts"]:
        lines.append("Other bank and cash accounts in the ledger, not included: "
                     + ", ".join(r["other_cash_accounts"]) + ".")
    elif len(r["accounts"]) > 1:
        lines.append("The ledger has several bank and cash accounts and all are included; name one with account to "
                     "reconcile it alone.")
    if r["statement"]["unread_rows"]:
        lines.append(f"{r['statement']['unread_rows']} statement rows had no date that could be read and were left out.")
    lines.append("Nothing was posted or changed." + _provenance_note(r))
    cols = [{"key": "where", "label": "Where", "format": "text"}, {"key": "date", "label": "Date", "format": "date"},
            {"key": "ref", "label": "Entry or row", "format": "text"},
            {"key": "text", "label": "Description", "format": "text"},
            {"key": "in", "label": "Received", "format": "money"}, {"key": "out", "label": "Paid", "format": "money"}]
    rows = ([["Ledger only", x["date"], x["entry"], x["text"], x["in"] or None, x["out"] or None] for x in r["book_only"]]
            + [["Statement only", x["date"], f"row {x['row']}", x["text"], x["in"] or None, x["out"] or None]
               for x in r["bank_only"]])
    return ToolOutput("\n".join(lines), [table_block(cols, rows, "Unmatched movements")] if rows else [], r)



# --- the ledger's own lines, read as they are ----------------------------------------------------------------------
# Three more closed questions (analysis/activity.py): what moved a month's profit and which accounts did it, what
# was posted on an account, and what an entry holds. Before these, the assistant answered them with query_dataset
# and its own arithmetic: an average nobody could trace, a payment it could not find, a total given as the amount
# before tax.

def _ym(v: Any) -> str | None:
    """A month as the ledger keys it; a full date is read as its month."""
    text = str(v or "").strip()
    return (text[:7] if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", text) else text) or None


def _period(first: str, last: str) -> str:
    return first if first == last else f"{first} to {last}"


def _side(v: float | None) -> str:
    """A balance as a ledger shows it: the amount and the side it sits on."""
    if v is None:
        return "—"
    return "0.00" if abs(v) < 0.005 else f"{abs(v):,.2f} {'debit' if v > 0 else 'credit'}"


def _moved(change: float) -> str:
    return "unchanged" if abs(change) < 0.005 else f"{'higher' if change > 0 else 'lower'} by {abs(change):,.2f}"


def _amounts(a: dict[str, float] | None) -> str:
    return "" if not a else (f" Before tax {a['before_tax']:,.2f}, tax {a['tax']:,.2f}, total {a['total']:,.2f}.")


def _legs(rows: list[dict[str, Any]], limit: int) -> str:
    shown = "; ".join(f"{x['account']} " + (f"debit {x['debit']:,.2f}" if x["debit"] else f"credit {x['credit']:,.2f}")
                      for x in rows[:limit])
    return shown + (f"; and {len(rows) - limit} more lines" if len(rows) > limit else "")


@registry.tool("profit_drivers", "Why a month's profit is what it is: its profit and loss against a baseline, line by "
               "line and account by account, each with its effect on net profit, and the months of the last twelve "
               "that made a loss. The baseline is the average of the months before it (six unless months says "
               "otherwise), the month before it, or the same month a year earlier, and the result says which. Use it "
               "whenever the user asks what caused a loss, a drop or a jump in profit, revenue or costs, which "
               "account is behind it, or what changed against the usual month: report its figures and its baseline "
               "as given, and do not work out an average of months yourself.",
               schema({"month": {"type": "string", "description": "YYYY-MM; the latest month when left out"},
                       "against": {"type": "string", "enum": ["average", "previous_month", "last_year"],
                                   "description": "average (of the months before it) when left out"},
                       "months": {"type": "integer", "minimum": 1, "maximum": 12,
                                  "description": "how many months before it to average; 6 when left out"}}),
               available=_has_ledger, offload=True)
async def _profit_drivers(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    month = _ym(a.get("month"))
    try:
        r = ctx.engine.statements.profit_drivers(ctx.ws_id, ctx.workspace, month, a.get("against") or "average",
                                                 int(a.get("months") or 6))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    if problem := _month_problem(r, month):
        return ToolOutput(problem, [], {"month": month, "available": False})
    base = r["baseline_months"]
    versus = {"previous_month": f"the month before it ({base[0]})",
              "last_year": f"the same month a year earlier ({base[0]})"}.get(
                  r["against"], f"the month before it ({base[0]})" if len(base) == 1 else
                  f"the average of the {len(base)} months before it ({base[0]} to {base[-1]})")
    net = r["net_profit"]
    out = [f"What moved profit in {r['month']}, against {versus}, in {r.get('currency') or 'the base currency'}, from "
           f"the general ledger “{r['ledger']['name']}”. Costs are positive amounts." + _provenance_note(r),
           f"Net profit: {net['value']:,.2f} against {net['baseline']:,.2f}, {_moved(net['change'])}."]

    def row(label: str, x: dict[str, Any]) -> str:
        return (f"- {label}: {x['value']:,.2f} against {x['baseline']:,.2f} ({_moved(x['change'])}): profit "
                f"{_moved(x['effect'])}.")
    moved = sorted((x for x in r["lines"] if x["effect"] is not None and abs(x["effect"]) >= 0.005),
                   key=lambda x: -abs(x["effect"]))
    out.append("By line, the largest effect on profit first:" if moved else "No line of the profit and loss moved.")
    out += [row(PL_LABELS.get(x["measure"], x["measure"]), x) for x in moved]
    if r["accounts"]:
        out.append("The accounts behind it, the largest effect first:")
        out += [row(f"{x['account']} ({PL_LABELS.get(x['measure'], x['measure'])})", x) for x in r["accounts"]]
        if r["accounts_count"] > len(r["accounts"]):
            out.append(f"… and {r['accounts_count'] - len(r['accounts'])} accounts that moved less.")
    losses = "; ".join(f"{x['month']} ({x['net_profit']:,.2f})" for x in r["loss_months"])
    out.append(f"Months with a loss between {r['year'][0]} and {r['year'][1]}: " + (losses or "none") + ".")
    cols = [{"key": "what", "label": "Line or account", "format": "text"},
            {"key": "value", "label": r["month"], "format": "money"},
            {"key": "baseline", "label": "Baseline", "format": "money"},
            {"key": "change", "label": "Change", "format": "money"},
            {"key": "effect", "label": "Effect on profit", "format": "money"}]
    rows = ([[PL_LABELS.get(x["measure"], x["measure"]), x["value"], x["baseline"], x["change"], x["effect"]]
             for x in r["lines"]]
            + [[x["account"], x["value"], x["baseline"], x["change"], x["effect"]] for x in r["accounts"]])
    return ToolOutput("\n".join(out), [table_block(cols, rows, f"What moved profit in {r['month']}")], r)


@registry.tool("account_activity", "What was posted on one account of the general ledger between two months: its "
               "balance at the start and at the end, total debits and credits, every line with its date, entry number, "
               "party and description, the other accounts in the same entry, and how much was paid from or received "
               "into the bank. Name the account by its code or by words from its name, in any language (VAT, zakat, "
               "loan, rent, sales returns). Use it for what was paid on a tax, a loan or an expense and when, for an "
               "account's balance and movement, and for the entries behind a figure: report the lines as given, and "
               "do not rebuild them with query_dataset. Without months it covers the whole ledger.",
               schema({"account": {"type": "string", "description": "the account's code, or words from its name"},
                       "from_month": {"type": "string", "description": "YYYY-MM, the first month"},
                       "to_month": {"type": "string", "description": "YYYY-MM, the last month"},
                       "party": {"type": "string", "description": "only lines with this customer or supplier"}},
                      ["account"]),
               available=_has_ledger, offload=True)
async def _account_activity(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    from ..analysis.activity import MAX_LINES
    try:
        r = ctx.engine.statements.account_activity(ctx.ws_id, ctx.workspace, str(a.get("account") or ""),
                                                   _ym(a.get("from_month")), _ym(a.get("to_month")), a.get("party"))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    listed = "; ".join(x["label"] for x in r["chart"][:120])
    if not r["matches"]:
        return ToolOutput(f"No account in the general ledger “{r['ledger']['name']}” matches “{r['asked']}”. Its "
                          f"accounts are: {listed}. Ask again with one of these codes or names.", [], r)
    cur = r.get("currency") or "the base currency"
    out: list[str] = []
    if len(r["matches"]) > 1:
        out.append(f"“{r['asked']}” fits {len(r['matches'])} accounts: " + "; ".join(x["label"] for x in r["matches"])
                   + (". The first four are shown; name one by its code for the rest." if len(r["matches"]) > 4 else
                      ". Each is shown below; name one by its code to see it alone."))
    rows = []
    for x in r["accounts"]:
        acct = x["account"]
        out.append(f"Account {acct['label']} ({acct['class'] or acct['role']}), {_period(x['first'], x['last'])}"
                   + (f", lines with {x['party']} only" if x["party"] else "") + f", in {cur}, from the general ledger "
                   f"“{r['ledger']['name']}”." + _provenance_note(r))
        if x["opening"] is not None:
            out.append(f"Balance at the start: {_side(x['opening'])}. Debits {x['debit']:,.2f}, credits "
                       f"{x['credit']:,.2f}. Balance at the end of {x['last']}: {_side(x['closing'])}.")
        else:
            out.append(f"Debits {x['debit']:,.2f}, credits {x['credit']:,.2f} in the period (net {_side(x['net'])}).")
        if x["paid_from_bank"] is not None and x["count"]:
            out.append(f"Paid from the bank in the same entry: {x['paid_from_bank']:,.2f} of the debits. Received into "
                       f"the bank in the same entry: {x['received_into_bank']:,.2f} of the credits.")
        if not x["count"]:
            out.append("No lines in the period.")
        elif x["count"] > MAX_LINES:
            out.append(f"{x['count']} lines, too many to list; by month (ask for one month to see its lines):")
            out += [f"- {m['month']}: debits {m['debit']:,.2f}, credits {m['credit']:,.2f}"
                    + (f", balance at the month end {_side(m['closing'])}" if m["closing"] is not None else "")
                    for m in x["by_month"]]
        else:
            out.append(f"Lines ({x['count']}):")
            for ln in x["lines"]:
                amount = f"debit {ln['debit']:,.2f}" if ln["debit"] else f"credit {ln['credit']:,.2f}"
                head = ", ".join(v for v in (ln["date"], ln["entry"], ln["type"], ln["party"]) if v)
                out.append(f"- {head}: {amount}" + (f", {ln['description']}" if ln["description"] else "")
                           + (f"; in the same entry: {_legs(ln['with'], 4)}." if ln["with"] else ".")
                           + _amounts(ln["amounts"]))
        rows += [[acct["label"], ln["date"], ln["entry"], ln["party"], ln["description"], ln["debit"] or None,
                  ln["credit"] or None] for ln in x["lines"]]
    cols = [{"key": "account", "label": "Account", "format": "text"}, {"key": "date", "label": "Date", "format": "date"},
            {"key": "entry", "label": "Entry", "format": "text"}, {"key": "party", "label": "Party", "format": "text"},
            {"key": "text", "label": "Description", "format": "text"},
            {"key": "debit", "label": "Debit", "format": "money"}, {"key": "credit", "label": "Credit", "format": "money"}]
    return ToolOutput("\n".join(out), [table_block(cols, rows, "Account lines")] if rows else [], r)


@registry.tool("ledger_entries", "Whole entries of the general ledger, every line of each: one entry by its number, or "
               "the entries between two months whose type, descriptions, parties or account names hold the words "
               "asked for (credit note, a supplier's name, zakat, a bill number), in any language. Where an entry is "
               "an invoice, a bill or a credit note with a sales-tax line, it also gives the amount before tax, the "
               "tax and the total. Use it for credit notes and returns, for what an entry is and who it was with, and "
               "whenever the user asks for an amount before or after VAT: report the lines as given.",
               schema({"entry": {"type": "string", "description": "an entry number, for example CN-2001"},
                       "search": {"type": "string", "description": "words to look for, when no entry number is known"},
                       "from_month": {"type": "string", "description": "YYYY-MM, the first month"},
                       "to_month": {"type": "string", "description": "YYYY-MM, the last month"}}),
               available=_has_ledger, offload=True)
async def _ledger_entries(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        r = ctx.engine.statements.ledger_entries(ctx.ws_id, ctx.workspace, a.get("entry"), a.get("search"),
                                                 _ym(a.get("from_month")), _ym(a.get("to_month")))
    except (Invalid, NotFound) as exc:
        return ToolOutput(exc.message)
    where = f"the general ledger “{r['ledger']['name']}”"
    asked = f"entry “{r['entry']}”" if r["entry"] else f"“{r['search']}” between {r['first']} and {r['last']}"
    if not r["entries"]:
        return ToolOutput(f"No entry in {where} matches {asked}. Words are looked for in an entry's number, type, "
                          "descriptions, parties and account names. Entry types in this ledger: "
                          + (", ".join(r["types"][:30]) or "none recorded") + ".", [], r)
    out = [f"Entries in {where} matching {asked}: {r['count']}"
           + (f"; the first {len(r['entries'])} are shown, so narrow the months or the words for the rest."
              if r["count"] > len(r["entries"]) else ".") + _provenance_note(r)]
    rows = []
    for e in r["entries"]:
        head = ", ".join(v for v in (e["entry"], e["date"], e["type"], e["party"]) if v)
        out.append(f"- {head}: {_legs(e['lines'], 12)}." + _amounts(e["amounts"]))
        rows += [[e["entry"], e["date"], e["type"], ln["party"] or e["party"], ln["account"], ln["description"],
                  ln["debit"] or None, ln["credit"] or None] for ln in e["lines"]]
    cols = [{"key": "entry", "label": "Entry", "format": "text"}, {"key": "date", "label": "Date", "format": "date"},
            {"key": "type", "label": "Type", "format": "text"}, {"key": "party", "label": "Party", "format": "text"},
            {"key": "account", "label": "Account", "format": "text"},
            {"key": "text", "label": "Description", "format": "text"},
            {"key": "debit", "label": "Debit", "format": "money"}, {"key": "credit", "label": "Credit", "format": "money"}]
    return ToolOutput("\n".join(out), [table_block(cols, rows, "Entries")], r)
