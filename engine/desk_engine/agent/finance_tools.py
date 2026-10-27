"""Deterministic finance calculators. The model chooses inputs; the arithmetic is done here, so
figures in answers come from code rather than from the model."""

from __future__ import annotations

import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .tools import ToolContext, ToolOutput, registry, schema, table_block, table_text

NUM = {"type": "number"}
NUMS = {"type": "array", "items": {"type": "number"}}


def d(x: Any) -> Decimal:
    return Decimal(str(x))


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --- pure functions (unit-tested) -----------------------------------------------------------


def npv(rate: float, cashflows: list[float]) -> float:
    """Cash flow 0 is at time 0 (not discounted)."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))


def irr(cashflows: list[float], guess: float = 0.1) -> float | None:
    if not (any(c > 0 for c in cashflows) and any(c < 0 for c in cashflows)):
        return None

    def f(r: float) -> float:
        return npv(r, cashflows)

    r = guess
    for _ in range(100):
        if r <= -0.999999:
            break
        value = f(r)
        deriv = sum(-t * cf / (1 + r) ** (t + 1) for t, cf in enumerate(cashflows))
        if deriv == 0:
            break
        nxt = r - value / deriv
        if abs(nxt - r) < 1e-10:
            return nxt
        r = nxt
    # bisection fallback over a wide bracket
    lo, hi = -0.9999, 10.0
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-9:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def xnpv(rate: float, cashflows: list[float], dates: list[date]) -> float:
    t0 = dates[0]
    return sum(cf / (1 + rate) ** ((dt - t0).days / 365.0) for cf, dt in zip(cashflows, dates, strict=True))


def xirr(cashflows: list[float], dates: list[date]) -> float | None:
    if not (any(c > 0 for c in cashflows) and any(c < 0 for c in cashflows)):
        return None
    lo, hi = -0.9999, 10.0
    flo = xnpv(lo, cashflows, dates)
    fhi = xnpv(hi, cashflows, dates)
    if flo * fhi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = xnpv(mid, cashflows, dates)
        if abs(fm) < 1e-9:
            return mid
        if flo * fm < 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def payment(principal: float, annual_rate: float, periods: int, per_year: int = 12) -> float:
    r = annual_rate / per_year
    if r == 0:
        return principal / periods
    return principal * r / (1 - (1 + r) ** -periods)


def amortization(principal: float, annual_rate: float, periods: int, per_year: int = 12) -> list[dict[str, Decimal]]:
    pmt = d(payment(principal, annual_rate, periods, per_year))
    r = d(annual_rate) / per_year
    balance = d(principal)
    rows = []
    for n in range(1, periods + 1):
        interest = q2(balance * r)
        principal_part = q2(pmt - interest)
        if n == periods:  # last payment clears rounding
            principal_part = balance
        balance = balance - principal_part
        rows.append({"period": Decimal(n), "payment": q2(interest + principal_part), "interest": interest,
                     "principal": q2(principal_part), "balance": q2(balance)})
    return rows


def depreciation(cost: float, salvage: float, life: int, method: str = "straight_line") -> list[dict[str, Decimal]]:
    c, s = d(cost), d(salvage)
    rows = []
    book = c
    accumulated = Decimal(0)
    syd_total = Decimal(life * (life + 1) // 2)
    for year in range(1, life + 1):
        if method == "straight_line":
            dep = (c - s) / life
        elif method == "double_declining":
            dep = book * d(2) / life
            if book - dep < s:
                dep = book - s
            if year == life:
                dep = book - s
        elif method == "sum_of_years":
            dep = (c - s) * d(life - year + 1) / syd_total
        else:
            raise ValueError("method must be straight_line, double_declining or sum_of_years")
        dep = q2(max(dep, Decimal(0)))
        accumulated += dep
        book = c - accumulated
        rows.append({"year": Decimal(year), "depreciation": dep, "accumulated": q2(accumulated), "book_value": q2(book)})
    return rows


RATIO_DEFS: list[tuple[str, str, str, list[str], str]] = [
    # key, label, group, inputs, format
    ("gross_margin", "Gross margin", "Profitability", ["revenue", "cogs"], "percent"),
    ("operating_margin", "Operating margin", "Profitability", ["revenue", "operating_income"], "percent"),
    ("net_margin", "Net margin", "Profitability", ["revenue", "net_income"], "percent"),
    ("roa", "Return on assets", "Profitability", ["net_income", "total_assets"], "percent"),
    ("roe", "Return on equity", "Profitability", ["net_income", "total_equity"], "percent"),
    ("current_ratio", "Current ratio", "Liquidity", ["current_assets", "current_liabilities"], "number"),
    ("quick_ratio", "Quick ratio", "Liquidity", ["current_assets", "inventory", "current_liabilities"], "number"),
    ("cash_ratio", "Cash ratio", "Liquidity", ["cash", "current_liabilities"], "number"),
    ("debt_to_equity", "Debt to equity", "Leverage", ["total_debt", "total_equity"], "number"),
    ("debt_to_assets", "Debt to assets", "Leverage", ["total_debt", "total_assets"], "percent"),
    ("interest_cover", "Interest cover", "Leverage", ["operating_income", "interest_expense"], "number"),
    ("asset_turnover", "Asset turnover", "Efficiency", ["revenue", "total_assets"], "number"),
    ("dso", "Days sales outstanding", "Efficiency", ["receivables", "revenue"], "days"),
    ("dio", "Days inventory outstanding", "Efficiency", ["inventory", "cogs"], "days"),
    ("dpo", "Days payables outstanding", "Efficiency", ["payables", "cogs"], "days"),
]


def ratios(f: dict[str, float], days: int = 365) -> list[dict[str, Any]]:
    out = []

    def div(a: float, b: float) -> float | None:
        return None if b == 0 else a / b

    for key, label, group, inputs, fmt in RATIO_DEFS:
        if not all(k in f and f[k] is not None for k in inputs):
            continue
        g = f.get
        value: float | None
        if key == "gross_margin":
            value = div(g("revenue") - g("cogs"), g("revenue"))
        elif key == "operating_margin":
            value = div(g("operating_income"), g("revenue"))
        elif key == "net_margin":
            value = div(g("net_income"), g("revenue"))
        elif key == "roa":
            value = div(g("net_income"), g("total_assets"))
        elif key == "roe":
            value = div(g("net_income"), g("total_equity"))
        elif key == "current_ratio":
            value = div(g("current_assets"), g("current_liabilities"))
        elif key == "quick_ratio":
            value = div(g("current_assets") - g("inventory"), g("current_liabilities"))
        elif key == "cash_ratio":
            value = div(g("cash"), g("current_liabilities"))
        elif key == "debt_to_equity":
            value = div(g("total_debt"), g("total_equity"))
        elif key == "debt_to_assets":
            value = div(g("total_debt"), g("total_assets"))
        elif key == "interest_cover":
            value = div(g("operating_income"), g("interest_expense"))
        elif key == "asset_turnover":
            value = div(g("revenue"), g("total_assets"))
        elif key == "dso":
            v = div(g("receivables"), g("revenue"))
            value = None if v is None else v * days
        elif key == "dio":
            v = div(g("inventory"), g("cogs"))
            value = None if v is None else v * days
        else:  # dpo
            v = div(g("payables"), g("cogs"))
            value = None if v is None else v * days
        out.append({"key": key, "label": label, "group": group, "value": value, "format": fmt})
    keys = {r["key"]: r["value"] for r in out}
    if all(keys.get(k) is not None for k in ("dso", "dio", "dpo")):
        out.append({"key": "ccc", "label": "Cash conversion cycle", "group": "Efficiency",
                    "value": keys["dso"] + keys["dio"] - keys["dpo"], "format": "days"})
    return out


def tax_split(amount: float, rate: float, inclusive: bool) -> dict[str, Decimal]:
    a, r = d(amount), d(rate)
    if inclusive:
        net = q2(a / (1 + r))
        return {"net": net, "tax": q2(a - net), "gross": q2(a)}
    tax = q2(a * r)
    return {"net": q2(a), "tax": tax, "gross": q2(a + tax)}


# --- tool wrappers ---------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.2f}%"


@registry.tool("calc_npv_irr", "Net present value and internal rate of return for periodic cash flows "
               "(first value is at time 0, usually negative).",
               schema({"cashflows": NUMS, "discount_rate": {**NUM, "description": "e.g. 0.1 for 10%"}},
                      ["cashflows"]))
async def _npv_irr(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    cfs = [float(x) for x in a["cashflows"]]
    result = {"irr": irr(cfs)}
    lines = [f"IRR: {_pct(result['irr'])}"]
    items = [{"label": "IRR", "value": result["irr"], "format": "percent"}]
    if a.get("discount_rate") is not None:
        rate = float(a["discount_rate"])
        result["npv"] = npv(rate, cfs)
        lines.insert(0, f"NPV at {rate * 100:.2f}%: {result['npv']:,.2f}")
        items.insert(0, {"label": f"NPV @ {rate * 100:.2f}%", "value": result["npv"], "format": "money"})
    total = sum(cfs)
    lines.append(f"Undiscounted total: {total:,.2f}")
    return ToolOutput("\n".join(lines), [{"type": "kpis", "items": items}], result)


@registry.tool("calc_xnpv_xirr", "NPV and IRR for cash flows on specific dates (like Excel XNPV/XIRR).",
               schema({"cashflows": NUMS, "dates": {"type": "array", "items": {"type": "string",
                                                                                "description": "YYYY-MM-DD"}},
                       "discount_rate": NUM}, ["cashflows", "dates"]))
async def _xnpv(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    cfs = [float(x) for x in a["cashflows"]]
    dates = [date.fromisoformat(s) for s in a["dates"]]
    if len(cfs) != len(dates):
        return ToolOutput("cashflows and dates must have the same length.")
    rate_irr = xirr(cfs, dates)
    lines = [f"XIRR: {_pct(rate_irr)}"]
    data: dict[str, Any] = {"xirr": rate_irr}
    if a.get("discount_rate") is not None:
        data["xnpv"] = xnpv(float(a["discount_rate"]), cfs, dates)
        lines.insert(0, f"XNPV: {data['xnpv']:,.2f}")
    return ToolOutput("\n".join(lines), [], data)


@registry.tool("calc_loan_schedule", "Loan payment and amortization schedule.",
               schema({"principal": NUM, "annual_rate": {**NUM, "description": "e.g. 0.065"},
                       "periods": {"type": "integer", "description": "number of payments"},
                       "payments_per_year": {"type": "integer", "default": 12}},
                      ["principal", "annual_rate", "periods"]))
async def _loan(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    per_year = int(a.get("payments_per_year") or 12)
    periods = int(a["periods"])
    if not 1 <= periods <= 1200:
        return ToolOutput("periods must be between 1 and 1200.")
    rows = amortization(float(a["principal"]), float(a["annual_rate"]), periods, per_year)
    total_interest = sum(r["interest"] for r in rows)
    cols = [{"key": "period", "label": "Period", "format": "number"},
            {"key": "payment", "label": "Payment", "format": "money"},
            {"key": "interest", "label": "Interest", "format": "money"},
            {"key": "principal", "label": "Principal", "format": "money"},
            {"key": "balance", "label": "Balance", "format": "money"}]
    table = [[int(r["period"]), float(r["payment"]), float(r["interest"]), float(r["principal"]), float(r["balance"])]
             for r in rows]
    summary = (f"Payment per period: {float(rows[0]['payment']):,.2f}; total interest: {float(total_interest):,.2f}; "
               f"total paid: {float(sum(r['payment'] for r in rows)):,.2f}")
    return ToolOutput(summary + "\n" + table_text(cols, table, 12),
                      [{"type": "kpis", "items": [
                          {"label": "Payment", "value": float(rows[0]["payment"]), "format": "money"},
                          {"label": "Total interest", "value": float(total_interest), "format": "money"}]},
                       table_block(cols, table, "Amortization schedule")],
                      {"payment": float(rows[0]["payment"]), "total_interest": float(total_interest)})


@registry.tool("calc_depreciation", "Depreciation schedule: straight_line, double_declining or sum_of_years.",
               schema({"cost": NUM, "salvage_value": NUM, "useful_life_years": {"type": "integer"},
                       "method": {"type": "string", "enum": ["straight_line", "double_declining", "sum_of_years"]}},
                      ["cost", "useful_life_years"]))
async def _dep(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    life = int(a["useful_life_years"])
    if not 1 <= life <= 100:
        return ToolOutput("useful_life_years must be between 1 and 100.")
    rows = depreciation(float(a["cost"]), float(a.get("salvage_value") or 0), life, a.get("method") or "straight_line")
    cols = [{"key": "year", "label": "Year", "format": "number"},
            {"key": "depreciation", "label": "Depreciation", "format": "money"},
            {"key": "accumulated", "label": "Accumulated", "format": "money"},
            {"key": "book_value", "label": "Book value", "format": "money"}]
    table = [[int(r["year"]), float(r["depreciation"]), float(r["accumulated"]), float(r["book_value"])] for r in rows]
    return ToolOutput(table_text(cols, table), [table_block(cols, table, "Depreciation schedule")], table)


@registry.tool("calc_financial_ratios", "Profitability, liquidity, leverage and efficiency ratios from figures "
               "you have (pass only the figures you know; same period and currency).",
               schema({k: NUM for k in ["revenue", "cogs", "operating_income", "net_income", "total_assets",
                                        "total_equity", "current_assets", "current_liabilities", "inventory",
                                        "receivables", "payables", "cash", "total_debt", "interest_expense"]}
                      | {"days_in_period": {"type": "integer", "default": 365}}))
async def _ratios(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    days = int(a.pop("days_in_period", 365) or 365)
    rows = ratios({k: float(v) for k, v in a.items() if v is not None}, days)
    if not rows:
        return ToolOutput("Not enough figures to compute any ratio.")
    cols = [{"key": "group", "label": "Group", "format": "text"}, {"key": "label", "label": "Ratio", "format": "text"},
            {"key": "value", "label": "Value", "format": "ratio"}]
    table = [[r["group"], r["label"], None if r["value"] is None else round(r["value"], 6)] for r in rows]
    text_lines = []
    for r in rows:
        v = r["value"]
        shown = "n/a" if v is None else (_pct(v) if r["format"] == "percent" else
                                         f"{v:,.1f} days" if r["format"] == "days" else f"{v:,.2f}x")
        text_lines.append(f"{r['label']}: {shown}")
    block = table_block(cols, table, "Financial ratios")
    block["formats"] = {r["label"]: r["format"] for r in rows}
    return ToolOutput("\n".join(text_lines), [block], rows)


@registry.tool("calc_variance", "Budget vs actual (or period vs period) variance per line.",
               schema({"lines": {"type": "array", "items": {"type": "object", "properties": {
                   "name": {"type": "string"}, "actual": NUM, "budget": NUM,
                   "higher_is_better": {"type": "boolean", "description": "true for revenue, false for costs"}},
                   "required": ["name", "actual", "budget"]}}}, ["lines"]))
async def _variance(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    cols = [{"key": "name", "label": "Line", "format": "text"}, {"key": "actual", "label": "Actual", "format": "money"},
            {"key": "budget", "label": "Budget", "format": "money"},
            {"key": "variance", "label": "Variance", "format": "money"},
            {"key": "pct", "label": "Variance %", "format": "percent"},
            {"key": "fav", "label": "", "format": "text"}]
    rows = []
    for line in a["lines"]:
        act, bud = float(line["actual"]), float(line["budget"])
        var = act - bud
        pct = None if bud == 0 else var / abs(bud)
        better = line.get("higher_is_better", True)
        fav = "favourable" if (var >= 0) == better or var == 0 else "unfavourable"
        rows.append([line["name"], act, bud, var, pct, fav])
    return ToolOutput(table_text(cols, rows), [table_block(cols, rows, "Variance analysis")], rows)


@registry.tool("calc_sales_tax", "Split an amount into net, tax and gross for any VAT, GST or sales tax rate.",
               schema({"amount": NUM, "rate": {**NUM, "description": "e.g. 0.2 for 20%"},
                       "amount_includes_tax": {"type": "boolean"}}, ["amount", "rate", "amount_includes_tax"]))
async def _tax(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    r = tax_split(float(a["amount"]), float(a["rate"]), bool(a["amount_includes_tax"]))
    return ToolOutput(f"Net {r['net']:,}, tax {r['tax']:,}, gross {r['gross']:,}",
                      [{"type": "kpis", "items": [{"label": k.title(), "value": float(v), "format": "money"}
                                                  for k, v in r.items()]}],
                      {k: float(v) for k, v in r.items()})


@registry.tool("calc_breakeven", "Break-even units and revenue.",
               schema({"fixed_costs": NUM, "unit_price": NUM, "unit_variable_cost": NUM},
                      ["fixed_costs", "unit_price", "unit_variable_cost"]))
async def _breakeven(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    margin = float(a["unit_price"]) - float(a["unit_variable_cost"])
    if margin <= 0:
        return ToolOutput("Unit price must be higher than the unit variable cost.")
    units = float(a["fixed_costs"]) / margin
    revenue = units * float(a["unit_price"])
    return ToolOutput(f"Break-even: {math.ceil(units):,} units, revenue {revenue:,.2f}; contribution margin "
                      f"{margin / float(a['unit_price']) * 100:.2f}%",
                      [{"type": "kpis", "items": [{"label": "Units", "value": math.ceil(units), "format": "number"},
                                                  {"label": "Revenue", "value": revenue, "format": "money"}]}],
                      {"units": units, "revenue": revenue})


@registry.tool("calc_growth", "Compound annual growth rate and period-over-period growth.",
               schema({"values": {**NUMS, "description": "values in time order"},
                       "years": {**NUM, "description": "years between first and last value, for CAGR"}}, ["values"]))
async def _growth(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    vals = [float(x) for x in a["values"]]
    if len(vals) < 2:
        return ToolOutput("Give at least two values.")
    changes = [None if vals[i - 1] == 0 else vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    data: dict[str, Any] = {"changes": changes}
    lines = ["Period changes: " + ", ".join(_pct(c) for c in changes)]
    years = float(a.get("years") or len(vals) - 1)
    if vals[0] > 0 and vals[-1] > 0 and years > 0:
        data["cagr"] = (vals[-1] / vals[0]) ** (1 / years) - 1
        lines.insert(0, f"CAGR over {years:g} years: {_pct(data['cagr'])}")
    return ToolOutput("\n".join(lines), [], data)


@registry.tool("calc_currency_convert", "Convert amounts with an exchange rate the user or a dataset supplied "
               "(the app has no live FX feed; always state the rate and its date).",
               schema({"amounts": NUMS, "rate": {**NUM, "description": "units of target currency per 1 source"},
                       "from_currency": {"type": "string"}, "to_currency": {"type": "string"},
                       "rate_date": {"type": "string"}}, ["amounts", "rate", "from_currency", "to_currency"]))
async def _fx(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    rate = d(a["rate"])
    out = [float(q2(d(x) * rate)) for x in a["amounts"]]
    when = f" (rate dated {a['rate_date']})" if a.get("rate_date") else ""
    return ToolOutput(f"Converted {a['from_currency']}→{a['to_currency']} at {a['rate']}{when}: "
                      + ", ".join(f"{v:,.2f}" for v in out), [], out)
