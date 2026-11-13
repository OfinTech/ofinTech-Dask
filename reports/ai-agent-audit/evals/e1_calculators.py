"""E1 — the deterministic finance calculators against independently computed ground truth.

Each case is generated from a fixed seed, its expected answer is computed here with a separate implementation
(high-precision Decimal arithmetic, root scanning for IRR), and the tool is run exactly as the agent runs it.
Both the structured result and the text the model actually reads are checked.

    python3 reports/ai-agent-audit/evals/e1_calculators.py
"""
from __future__ import annotations

import math
import random
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, getcontext

from common import Harness, run_tool, save, tool_ctx

getcontext().prec = 50
SEED = 20261113
rng = random.Random(SEED)
D = Decimal


def q2(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def nums(text: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d*", text)]


# --- independent ground truth ------------------------------------------------------------------
def npv_gt(rate, cfs) -> Decimal:
    r = D(str(rate))
    return sum((D(str(c)) / (1 + r) ** t for t, c in enumerate(cfs)), D(0))


def roots(f, lo=-0.99, hi=10.0, steps=20000) -> list[float]:
    """Every sign change of f on a fine grid, refined by bisection (finds all IRRs, not just one)."""
    out, xs = [], [lo + (hi - lo) * i / steps for i in range(steps + 1)]
    prev = f(xs[0])
    for a, b in zip(xs, xs[1:]):
        cur = f(b)
        if prev == 0:
            out.append(a)
        elif prev * cur < 0:
            x0, x1, f0 = a, b, prev
            for _ in range(200):
                m = (x0 + x1) / 2
                fm = f(m)
                if f0 * fm <= 0:
                    x1 = m
                else:
                    x0, f0 = m, fm
            out.append((x0 + x1) / 2)
        prev = cur
    return out


def unique_root(f, lo=-0.999999, hi=1e6):
    """Bisection for a function with a single sign change (conventional cash flows)."""
    flo, fhi = f(lo), f(hi)
    if flo == 0:
        return lo
    if flo * fhi > 0:
        return None
    for _ in range(400):
        m = (lo + hi) / 2
        fm = f(m)
        if flo * fm <= 0:
            hi = m
        else:
            lo, flo = m, fm
    return (lo + hi) / 2


def xnpv_gt(rate, cfs, dates) -> Decimal:
    t0 = dates[0]
    return sum((D(str(c)) / D(math.pow(1 + rate, (d - t0).days / 365.0)) for c, d in zip(cfs, dates)), D(0))


def pmt_gt(p, rate, n, per_year=12) -> Decimal:
    r = D(str(rate)) / per_year
    if r == 0:
        return D(str(p)) / n
    return D(str(p)) * r / (1 - (1 + r) ** -n)


results: list[dict] = []


def record(tool: str, case: str, ok: bool, expected, got, note: str = "") -> None:
    results.append({"tool": tool, "case": case, "pass": bool(ok), "expected": expected, "got": got, "note": note})


def main() -> None:
    h = Harness()
    ws = h.setup_workspace(with_model=False)
    ctx = tool_ctx(h, ws)
    T = lambda name, args: run_tool(h, ctx, name, args)  # noqa: E731

    # NPV / IRR, conventional cash flows (one sign change: exactly one IRR)
    for i in range(200):
        n = rng.randint(2, 15)
        cfs = [-round(rng.uniform(1_000, 1_000_000), 2)] + [round(rng.uniform(0, 400_000), 2) for _ in range(n)]
        rate = round(rng.uniform(0.0, 0.25), 4)
        o = T("calc_npv_irr", {"cashflows": cfs, "discount_rate": rate})
        exp_npv = npv_gt(rate, cfs)
        exp_irr = unique_root(lambda r: npv_gt(r, cfs))   # one sign change: exactly one IRR in (-1, inf)
        got_irr = o.data["irr"]
        ok = abs(D(str(o.data["npv"])) - exp_npv) <= D("0.005") and (
            (exp_irr is None and got_irr is None) or (exp_irr is not None and got_irr is not None and abs(got_irr - exp_irr) < 1e-6))
        shown = nums(o.text)
        ok_text = abs(shown[1] - float(q2(exp_npv))) <= 0.011 if len(shown) > 1 else False  # "NPV at r%: value"
        record("calc_npv_irr", f"conventional#{i}", ok and ok_text, {"npv": float(q2(exp_npv)), "irr": exp_irr},
               {"npv": o.data["npv"], "irr": got_irr, "text": o.text.splitlines()[0]})

    # NPV / IRR, non-conventional (two or more sign changes): the tool should not present one root as "the" IRR
    for i in range(30):
        cfs = [-1000, 3600, -4310, 1716] if i == 0 else [-round(rng.uniform(100, 1000), 2),
                                                        round(rng.uniform(2000, 3000), 2), -round(rng.uniform(1000, 2000), 2)]
        o = T("calc_npv_irr", {"cashflows": cfs, "discount_rate": 0.1})
        rs = roots(lambda r: float(npv_gt(r, cfs)))
        multiple = len(rs) > 1
        warned = "multiple" in o.text.lower() or "more than one" in o.text.lower()
        record("calc_npv_irr", f"non_conventional#{i}", (not multiple) or warned, {"irrs": rs},
               {"irr": o.data["irr"], "text": o.text.replace("\n", " | ")},
               "several IRRs exist; the tool reports one without saying so" if multiple and not warned else "")

    # XNPV / XIRR
    for i in range(100):
        n = rng.randint(2, 10)
        start = date(2024, 1, 1) + timedelta(days=rng.randint(0, 700))
        ds = [start] + sorted(start + timedelta(days=rng.randint(30, 3000)) for _ in range(n))
        cfs = [-round(rng.uniform(1_000, 500_000), 2)] + [round(rng.uniform(100, 200_000), 2) for _ in range(n)]
        rate = round(rng.uniform(0.01, 0.2), 4)
        o = T("calc_xnpv_xirr", {"cashflows": cfs, "dates": [d.isoformat() for d in ds], "discount_rate": rate})
        exp = xnpv_gt(rate, cfs, ds)
        # one sign change: exactly one XIRR in (-100%, inf); found with 60-digit arithmetic over (-99.9999%, 1e8%).
        # (The audited version scanned only up to 1,000% and so took a real XIRR above that for "none".)
        years = [D((d_ - ds[0]).days) / D(365) for d_ in ds]
        exp_r = unique_root(lambda r: sum((D(str(c)) / (1 + D(str(r))) ** y for c, y in zip(cfs, years)), D(0)))
        got = o.data.get("xirr")
        ok = abs(D(str(o.data["xnpv"])) - exp) <= D("0.01") and (
            (exp_r is None and got is None) or (exp_r is not None and got is not None
                                                and abs(got - exp_r) <= 1e-6 * max(1.0, abs(exp_r))))
        record("calc_xnpv_xirr", f"#{i}", ok, {"xnpv": float(q2(exp)), "xirr": exp_r},
               {"xnpv": o.data.get("xnpv"), "xirr": got})
    # XIRR above 1000% (fast payback): the bisection bracket stops at 10 (1000%)
    o = T("calc_xnpv_xirr", {"cashflows": [-100, 200], "dates": ["2026-01-01", "2026-02-01"]})
    exp_r = 2 ** (365 / 31) - 1
    record("calc_xnpv_xirr", "xirr_above_1000pct", o.data.get("xirr") is not None and abs(o.data["xirr"] - exp_r) < 1e-4,
           {"xirr": exp_r}, {"xirr": o.data.get("xirr"), "text": o.text},
           "XIRR above 1,000% is reported as n/a" if o.data.get("xirr") is None else "")

    # Loan schedules
    for i in range(150):
        p = round(rng.uniform(1_000, 5_000_000), 2)
        rate = rng.choice([0, round(rng.uniform(0.001, 0.25), 4)])
        n = rng.randint(1, 360)
        per = rng.choice([12, 4, 1])
        o = T("calc_loan_schedule", {"principal": p, "annual_rate": rate, "periods": n, "payments_per_year": per})
        tbl = [b for b in o.blocks if b.get("type") == "table"][0]["rows"]
        exp_pmt = q2(pmt_gt(p, rate, n, per))
        principal_sum = q2(sum((D(str(r[3])) for r in tbl), D(0)))
        paid = q2(sum((D(str(r[1])) for r in tbl), D(0)))
        interest = q2(sum((D(str(r[2])) for r in tbl), D(0)))
        ok = (abs(D(str(o.data["payment"])) - exp_pmt) <= D("0.01") and principal_sum == q2(D(str(p)))
              and tbl[-1][4] == 0 and paid - interest == q2(D(str(p))))
        record("calc_loan_schedule", f"#{i}", ok, {"payment": float(exp_pmt), "principal": p},
               {"payment": o.data["payment"], "principal_repaid": float(principal_sum), "final_balance": tbl[-1][4]})

    # Depreciation
    for i in range(150):
        cost = round(rng.uniform(500, 2_000_000), 2)
        salv = round(cost * rng.uniform(0, 0.3), 2)
        life = rng.randint(1, 40)
        method = ["straight_line", "double_declining", "sum_of_years"][i % 3]
        o = T("calc_depreciation", {"cost": cost, "salvage_value": salv, "useful_life_years": life, "method": method})
        rows = o.data
        total = q2(sum((D(str(r[1])) for r in rows), D(0)))
        target = q2(D(str(cost)) - D(str(salv)))
        ok = abs(total - target) <= D("0.01") * life and abs(D(str(rows[-1][3])) - q2(D(str(salv)))) <= D("0.01") * life
        if method == "straight_line":
            ok = ok and all(abs(D(str(r[1])) - q2(target / life)) <= D("0.01") for r in rows)
        if method == "sum_of_years":
            syd = D(life * (life + 1) // 2)
            ok = ok and all(abs(D(str(r[1])) - q2(target * (life - k) / syd)) <= D("0.01") for k, r in enumerate(rows))
        record("calc_depreciation", f"{method}#{i}", ok, {"total": float(target), "final_book": salv},
               {"total": float(total), "final_book": rows[-1][3]})
    o = T("calc_depreciation", {"cost": 1000, "salvage_value": 1500, "useful_life_years": 5})
    record("calc_depreciation", "salvage_above_cost", "salvage" in o.text.lower() and "cost" in o.text.lower(),
           "a refusal or a warning", o.text.splitlines()[-1], "salvage above cost gives a schedule of zeros without a word")

    # Ratios
    keys = ["revenue", "cogs", "operating_income", "net_income", "total_assets", "total_equity", "current_assets",
            "current_liabilities", "inventory", "receivables", "payables", "cash", "total_debt", "interest_expense"]
    for i in range(150):
        f = {k: round(rng.uniform(1_000, 10_000_000), 2) for k in keys}
        if i % 10 == 0:
            f["current_liabilities"] = 0
        days = rng.choice([365, 90, 30])
        o = T("calc_financial_ratios", {**f, "days_in_period": days})
        got = {r["key"]: r["value"] for r in o.data}
        F = {k: D(str(v)) for k, v in f.items()}

        def div(a, b):
            return None if b == 0 else a / b
        exp = {"gross_margin": div(F["revenue"] - F["cogs"], F["revenue"]), "net_margin": div(F["net_income"], F["revenue"]),
               "current_ratio": div(F["current_assets"], F["current_liabilities"]),
               "quick_ratio": div(F["current_assets"] - F["inventory"], F["current_liabilities"]),
               "debt_to_equity": div(F["total_debt"], F["total_equity"]),
               "interest_cover": div(F["operating_income"], F["interest_expense"]),
               "dso": F["receivables"] / F["revenue"] * days, "dio": F["inventory"] / F["cogs"] * days,
               "dpo": F["payables"] / F["cogs"] * days}
        ok = all((exp[k] is None and got.get(k) is None) or (exp[k] is not None and got.get(k) is not None and
                 abs(D(str(got[k])) - exp[k]) <= abs(exp[k]) * D("1e-9") + D("1e-9")) for k in exp)
        record("calc_financial_ratios", f"#{i}", ok, {k: (None if v is None else float(v)) for k, v in exp.items()},
               {k: got.get(k) for k in exp})
    args = {"revenue": 1000, "cogs": 400, "days_in_period": 90}
    import json as _j
    sent = _j.loads(_j.dumps(args))
    run_tool(h, ctx, "calc_financial_ratios", sent)
    record("calc_financial_ratios", "arguments_not_mutated", sent == args, args, sent,
           "the tool removes days_in_period from the call's own arguments (a.pop), so the stored call no longer shows it")

    # Variance
    for i in range(100):
        lines = [{"name": f"L{j}", "actual": round(rng.uniform(-1e5, 1e6), 2), "budget": round(rng.uniform(-1e5, 1e6), 2),
                  "higher_is_better": rng.choice([True, False])} for j in range(rng.randint(1, 8))]
        if i % 7 == 0:
            lines[0]["budget"] = 0
        o = T("calc_variance", {"lines": lines})
        ok = True
        for ln, row in zip(lines, o.data):
            var = D(str(ln["actual"])) - D(str(ln["budget"]))
            pct_ = None if ln["budget"] == 0 else var / abs(D(str(ln["budget"])))
            fav = "favourable" if var == 0 or (var > 0) == ln["higher_is_better"] else "unfavourable"
            ok = ok and abs(D(str(row[3])) - var) <= D("0.005") and row[5] == fav and (
                (pct_ is None and row[4] is None) or (pct_ is not None and abs(D(str(row[4])) - pct_) < D("1e-9")))
        record("calc_variance", f"#{i}", ok, None, None)

    # Sales tax
    for i in range(200):
        amt = round(rng.uniform(0.01, 1_000_000), 2)
        rate = rng.choice([0.05, 0.15, 0.2, 0.0, 0.075, 0.125, 0.17])
        inc = bool(i % 2)
        o = T("calc_sales_tax", {"amount": amt, "rate": rate, "amount_includes_tax": inc})
        A, R = D(str(amt)), D(str(rate))
        if inc:
            net = q2(A / (1 + R)); exp = {"net": net, "tax": q2(A - net), "gross": q2(A)}
        else:
            tax = q2(A * R); exp = {"net": q2(A), "tax": tax, "gross": q2(A + tax)}
        ok = all(D(str(o.data[k])) == v for k, v in exp.items()) and D(str(o.data["net"])) + D(str(o.data["tax"])) == D(str(o.data["gross"]))
        record("calc_sales_tax", f"{'inclusive' if inc else 'exclusive'}#{i}", ok, {k: float(v) for k, v in exp.items()}, o.data)
    o = T("calc_sales_tax", {"amount": 100, "rate": 15, "amount_includes_tax": False})
    record("calc_sales_tax", "rate_given_as_percent", "rate" in o.text.lower() and (o.data is None or o.data["gross"] < 1000),
           "a question or refusal for rate=15 (likely 15%)", o.data, "rate 15 is taken as 1,500% (gross 1,600)")

    # Break-even
    for i in range(100):
        fixed = round(rng.uniform(1_000, 1_000_000), 2)
        price = round(rng.uniform(1, 500), 2)
        var = round(price * rng.uniform(0.1, 0.9), 2)
        o = T("calc_breakeven", {"fixed_costs": fixed, "unit_price": price, "unit_variable_cost": var})
        units = D(str(fixed)) / (D(str(price)) - D(str(var)))
        shown_units = math.ceil(units)
        shown = nums(o.text)
        ok_units = shown[0] == shown_units
        exp_rev = float(q2(units * D(str(price))))   # break-even revenue = fixed costs / contribution margin ratio
        consistent = abs(shown[1] - exp_rev) <= 0.011
        record("calc_breakeven", f"#{i}", ok_units and consistent, {"units": shown_units, "revenue": exp_rev},
               {"text": o.text})

    # Growth
    for i in range(100):
        vals = [round(rng.uniform(1, 1e7), 2) for _ in range(rng.randint(2, 10))]
        yrs = len(vals) - 1
        o = T("calc_growth", {"values": vals})
        exp_cagr = (D(str(vals[-1])) / D(str(vals[0]))) ** (D(1) / yrs) - 1
        ok = abs(D(str(o.data["cagr"])) - exp_cagr) < D("1e-9") and all(
            abs(D(str(c)) - (D(str(b)) / D(str(a)) - 1)) < D("1e-9") for c, a, b in zip(o.data["changes"], vals, vals[1:]))
        record("calc_growth", f"#{i}", ok, float(exp_cagr), o.data.get("cagr"))
    o = T("calc_growth", {"values": [-100, -50]})
    ch = o.data["changes"][0]
    record("calc_growth", "negative_base", ch is None or ch > 0 or "negative" in o.text.lower(),
           "an improvement from -100 to -50 is not a -50% decline", {"change": ch, "text": o.text},
           "a loss shrinking from -100 to -50 is reported as -50.00% growth")

    # Currency conversion
    for i in range(100):
        amts = [round(rng.uniform(-1e6, 1e6), 2) for _ in range(rng.randint(1, 6))]
        rate = round(rng.uniform(0.001, 500), 6)
        o = T("calc_currency_convert", {"amounts": amts, "rate": rate, "from_currency": "USD", "to_currency": "SAR"})
        ok = all(D(str(g)) == q2(D(str(a)) * D(str(rate))) for g, a in zip(o.data, amts))
        record("calc_currency_convert", f"#{i}", ok, None, None)

    h.close()
    by_tool: dict[str, dict[str, int]] = {}
    for r in results:
        t = by_tool.setdefault(r["tool"], {"cases": 0, "passed": 0})
        t["cases"] += 1
        t["passed"] += r["pass"]
    failed = [r for r in results if not r["pass"]]
    summary = {"seed": SEED, "cases": len(results), "passed": sum(r["pass"] for r in results), "by_tool": by_tool,
               "failed": failed[:60], "failed_total": len(failed)}
    save("e1_calculators", summary)
    print(f"E1: {summary['passed']}/{summary['cases']} passed")
    for t, v in by_tool.items():
        print(f"  {t:24s} {v['passed']}/{v['cases']}")
    seen = set()
    for f in failed:
        key = (f["tool"], f["case"].split("#")[0])
        if key not in seen:
            seen.add(key)
            print("  FAIL", f["tool"], f["case"], "|", f["note"] or "", "| expected", str(f["expected"])[:120], "| got", str(f["got"])[:160])


if __name__ == "__main__":
    main()
