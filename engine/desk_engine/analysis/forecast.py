"""Forecasts set by the finance lead: a method and an assumption for every P&L line, working-capital days, capital
spending and debt repayment. From these the forecast projects the P&L, receivables, inventory, payables and cash,
month by month.

A statistical reference (exponential smoothing, with seasonality when there are two years of history) is computed
next to the revenue forecast, with its error on the last months of history, as a check on the assumptions. It is
never used in place of them.
"""

from __future__ import annotations

from typing import Any

from ..errors import Invalid
from .ledger import Ledger

LINES = ["revenue", "cogs", "payroll", "opex", "depreciation", "other_income", "other_expense", "interest", "tax"]
COSTS = {"cogs", "payroll", "opex", "depreciation", "other_expense", "interest", "tax"}
METHODS = {"growth", "yoy", "pct_of", "average", "trend", "fixed", "manual"}
D = 91.25


def add_month(m: str, n: int) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    y, mo = divmod(y * 12 + mo - 1 + n, 12)
    return f"{y:04d}-{mo + 1:02d}"


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def history(ledger: Ledger, line: str) -> list[float]:
    return [ledger.flows[line].get(m, 0.0) for m in ledger.months]


def defaults(ledger: Ledger, kpi: dict[str, float | None]) -> dict[str, Any]:
    """Starting assumptions from the company's own history; the finance lead changes them."""
    n = len(ledger.months)
    rev = history(ledger, "revenue")[-12:]
    lines: dict[str, Any] = {}
    lines["revenue"] = {"method": "yoy", "value": 0.0} if n >= 12 else {"method": "average", "n": min(3, max(n, 1))}
    cogs = history(ledger, "cogs")[-12:]
    lines["cogs"] = {"method": "pct_of", "of": "revenue",
                     "value": round(sum(cogs) / sum(rev) * 100, 2) if sum(rev) else 0.0}
    for line in ("payroll", "opex", "depreciation", "other_income", "other_expense", "interest"):
        lines[line] = {"method": "average", "n": min(3, max(n, 1))}
    pbt = [ledger.flows["net_profit"].get(m, 0.0) + ledger.flows["tax"].get(m, 0.0) for m in ledger.months][-12:]
    tax = history(ledger, "tax")[-12:]
    rate = sum(tax) / sum(pbt) * 100 if sum(pbt) > 0 else 0.0
    lines["tax"] = {"method": "pct_of", "of": "profit_before_tax", "value": round(min(max(rate, 0.0), 60.0), 2)}
    icf = history(ledger, "icf")[-6:]
    repaid = history(ledger, "debt_repaid")[-6:]

    def k(name: str) -> float:
        v = kpi.get(name)
        return round(v, 1) if v is not None and v >= 0 else 0.0
    return {"lines": lines, "dso": k("dso"), "dpo": k("dpo"), "dio": k("dio"),
            "capex": round(max(-_avg(icf), 0.0), 2), "debt_repayment": round(_avg(repaid), 2)}


def validate(a: dict[str, Any], months: int) -> dict[str, Any]:
    lines = a.get("lines") or {}
    for line in LINES:
        x = lines.get(line)
        if not x:
            raise Invalid(f"Choose a method for {line}.", code="bad_assumptions")
        if x.get("method") not in METHODS:
            raise Invalid(f"Unknown method for {line}.", code="bad_assumptions")
        if x["method"] == "pct_of":
            allowed = LINES[:LINES.index(line)] + (["profit_before_tax"] if line == "tax" else [])
            if x.get("of") not in allowed:
                raise Invalid(f"{line} can be a percentage of: {', '.join(allowed) or 'nothing'}.", code="bad_assumptions")
        if x["method"] == "manual" and len(x.get("values") or []) > months:
            raise Invalid(f"{line} has more monthly figures than the forecast has months.", code="bad_assumptions")
        if x.get("n") is not None and not 1 <= int(x["n"]) <= 24:
            raise Invalid("Average and trend use 1 to 24 months.", code="bad_assumptions")
    for key in ("dso", "dpo", "dio"):
        if not 0 <= float(a.get(key) or 0) <= 720:
            raise Invalid(f"{key.upper()} is between 0 and 720 days.", code="bad_assumptions")
    return a


def project(ledger: Ledger, a: dict[str, Any], start: str, months: int) -> dict[str, Any]:
    """The forecast, month by month. Actual months before ``start`` feed the methods that look back."""
    validate(a, months)
    hist_months = [m for m in ledger.months if m < start]
    if not hist_months:
        raise Invalid("The forecast must start after the first month of the ledger.", code="bad_assumptions")
    fmonths = [add_month(start, i) for i in range(months)]
    actual: dict[str, dict[str, float]] = {line: {m: ledger.flows[line].get(m, 0.0) for m in hist_months}
                                           for line in [*LINES, "purchases"]}
    out: dict[str, list[float]] = {line: [] for line in LINES}

    def val(line: str, m: str) -> float:
        if m in actual[line]:
            return actual[line][m]
        if m in fmonths and fmonths.index(m) < len(out[line]):
            return out[line][fmonths.index(m)]
        raise Invalid(f"{line}: no figure for {m}; this method needs more history.", code="bad_assumptions")

    hist = {line: [actual[line][m] for m in hist_months] for line in LINES}
    for k, m in enumerate(fmonths):
        pbt = None
        for line in LINES:
            x = a["lines"][line]
            meth, v = x["method"], float(x.get("value") or 0)
            n = int(x.get("n") or 3)
            if meth == "growth":
                base = _avg(hist[line][-n:])
                y = base * (1 + v / 100) ** (k + 1)
            elif meth == "yoy":
                y = val(line, add_month(m, -12)) * (1 + v / 100)
            elif meth == "pct_of":
                of = x["of"]
                if of == "profit_before_tax":
                    pbt = (out["revenue"][k] - out["cogs"][k] - out["payroll"][k] - out["opex"][k]
                           - out["depreciation"][k] + out["other_income"][k] - out["other_expense"][k]
                           - out["interest"][k])
                    y = max(pbt, 0.0) * v / 100
                else:
                    y = out[of][k] * v / 100
            elif meth == "average":
                y = _avg(hist[line][-n:])
            elif meth == "trend":
                ys = hist[line][-(int(x.get("n") or 12)):]
                y = _trend(ys, len(ys) + k)
            elif meth == "fixed":
                y = v
            else:
                vals = x.get("values") or []
                y = float(vals[k]) if k < len(vals) and vals[k] is not None else 0.0
            out[line].append(round(y, 2))
    f = out
    gp = [f["revenue"][i] - f["cogs"][i] for i in range(months)]
    ebitda = [gp[i] - f["payroll"][i] - f["opex"][i] for i in range(months)]
    op = [ebitda[i] - f["depreciation"][i] for i in range(months)]
    net = [op[i] + f["other_income"][i] - f["other_expense"][i] - f["interest"][i] - f["tax"][i] for i in range(months)]
    # working capital and cash, with the same conventions as the DSO, DIO and DPO KPIs (last three months)
    last = hist_months[-1]
    bal = {s: ledger.stocks[s].get(last, 0.0) for s in ("cash", "receivables", "inventory", "payables")}
    rev_all = [actual["revenue"][mm] for mm in hist_months] + f["revenue"]
    cogs_all = [actual["cogs"][mm] for mm in hist_months] + f["cogs"]
    purch_all = [actual["purchases"][mm] for mm in hist_months] + [f["cogs"][i] + f["opex"][i] for i in range(months)]
    h = len(hist_months)
    dso, dio, dpo = float(a.get("dso") or 0), float(a.get("dio") or 0), float(a.get("dpo") or 0)
    capex, repay = float(a.get("capex") or 0), float(a.get("debt_repayment") or 0)
    rows = {"receivables": [], "inventory": [], "payables": [], "cash": []}
    cash, ar, inv, ap = bal["cash"], bal["receivables"], bal["inventory"], bal["payables"]
    for i in range(months):
        j = h + i
        ar2 = sum(rev_all[max(0, j - 2):j + 1]) * dso / D
        inv2 = sum(cogs_all[max(0, j - 2):j + 1]) * dio / D
        ap2 = sum(purch_all[max(0, j - 2):j + 1]) * dpo / D
        cash += net[i] + f["depreciation"][i] - (ar2 - ar) - (inv2 - inv) + (ap2 - ap) - capex - repay
        ar, inv, ap = ar2, inv2, ap2
        rows["receivables"].append(round(ar, 2))
        rows["inventory"].append(round(inv, 2))
        rows["payables"].append(round(ap, 2))
        rows["cash"].append(round(cash, 2))
    low = min(range(months), key=lambda i: rows["cash"][i])
    negative = next((fmonths[i] for i in range(months) if rows["cash"][i] < 0), None)
    tot = {line: round(sum(f[line]), 2) for line in LINES}
    tot_net = round(sum(net), 2)
    return {"start": start, "months": fmonths, "lines": f,
            "gross_profit": [round(x, 2) for x in gp], "ebitda": [round(x, 2) for x in ebitda],
            "operating_profit": [round(x, 2) for x in op], "net_profit": [round(x, 2) for x in net],
            "balances": rows, "opening": {k: round(v, 2) for k, v in bal.items()}, "last_actual_month": last,
            "summary": {"revenue": tot["revenue"], "net_profit": tot_net,
                        "net_margin": round(tot_net / tot["revenue"], 4) if tot["revenue"] else None,
                        "ending_cash": rows["cash"][-1], "lowest_cash": rows["cash"][low], "lowest_cash_month": fmonths[low],
                        "cash_negative_from": negative},
            "totals": tot}


def _trend(ys: list[float], x: int) -> float:
    """Least-squares straight line through ys (at 0..n-1), read at x."""
    n = len(ys)
    if n < 2:
        return ys[0] if ys else 0.0
    mx, my = (n - 1) / 2, _avg(ys)
    sxx = sum((i - mx) ** 2 for i in range(n))
    slope = sum((i - mx) * (y - my) for i, y in enumerate(ys)) / sxx if sxx else 0.0
    return my + slope * (x - mx)


def _holt(ys: list[float], horizon: int, season: int, a: float, b: float, g: float) -> tuple[list[float], float]:
    """Additive Holt (season = 0) or Holt-Winters; returns the forecast and the in-sample squared error."""
    if season:
        level = _avg(ys[:season])
        trend = (_avg(ys[season:2 * season]) - level) / season if len(ys) >= 2 * season else 0.0
        seas = [ys[i] - level for i in range(season)]
    else:
        level, trend, seas = ys[0], (ys[1] - ys[0]) if len(ys) > 1 else 0.0, []
    sse = 0.0
    for t, y in enumerate(ys):
        s = seas[t % season] if season else 0.0
        pred = level + trend + s
        if t:
            sse += (y - pred) ** 2
        last_level = level
        level = a * (y - s) + (1 - a) * (level + trend)
        trend = b * (level - last_level) + (1 - b) * trend
        if season:
            seas[t % season] = g * (y - level) + (1 - g) * s
    n = len(ys)
    out = [level + (h + 1) * trend + (seas[(n + h) % season] if season else 0.0) for h in range(horizon)]
    return out, sse


def reference(ys: list[float], horizon: int) -> dict[str, Any] | None:
    """Exponential smoothing on revenue, with parameters chosen on the history and the error on the last months."""
    if len(ys) < 6:
        return None
    season = 12 if len(ys) >= 24 else 0
    grid = [0.2, 0.4, 0.6, 0.8]
    def fit(series: list[float], h: int) -> list[float]:
        best = min(((a, b, g) for a in grid for b in (0.05, 0.2) for g in ((0.1, 0.3) if season else (0.0,))),
                   key=lambda p: _holt(series, 0, season, *p)[1])
        return _holt(series, h, season, *best)[0]
    hold = max(3, min(6, len(ys) // 4))
    mape = None
    if len(ys) - hold >= (2 * season if season else 6):
        back = fit(ys[:-hold], hold)
        errs = [abs(p - y) / abs(y) for p, y in zip(back, ys[-hold:], strict=True) if abs(y) > 1e-9]
        mape = round(_avg(errs), 4) if errs else None
    return {"method": "Holt-Winters (additive, 12-month season)" if season else "Holt (level and trend)",
            "values": [round(v, 2) for v in fit(ys, horizon)], "backtest_months": hold, "backtest_mape": mape}


def accuracy(ledger: Ledger, result: dict[str, Any]) -> dict[str, Any]:
    """Forecast against actual for months that have closed since the forecast was made (the last ledger month is
    left out because it may still be open)."""
    closed = set(ledger.months[:-1])
    rows = []
    for i, m in enumerate(result["months"]):
        if m not in closed:
            continue
        actual_rev = ledger.flows["revenue"].get(m, 0.0)
        actual_net = ledger.flows["net_profit"].get(m, 0.0)
        rows.append({"month": m, "revenue": result["lines"]["revenue"][i], "actual_revenue": round(actual_rev, 2),
                     "net_profit": result["net_profit"][i], "actual_net_profit": round(actual_net, 2)})
    errs = [abs(r["revenue"] - r["actual_revenue"]) / abs(r["actual_revenue"]) for r in rows if abs(r["actual_revenue"]) > 1e-9]
    bias = _avg([r["revenue"] - r["actual_revenue"] for r in rows]) if rows else None
    return {"months": rows, "revenue_mape": round(_avg(errs), 4) if errs else None,
            "revenue_bias": round(bias, 2) if bias is not None else None}


def default_start(ledger: Ledger) -> str:
    """The month after the last month of the ledger."""
    if not ledger.months:
        raise Invalid("The ledger has no lines yet.", code="no_data")
    return add_month(ledger.months[-1], 1)
