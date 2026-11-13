"""E2 — figures the agent reads from the general ledger, against ground truth computed independently.

A synthetic trading company (seeded; amounts in cents; 22 months; month-boundary postings; a sales refund; VAT;
a long-term loan) is pushed through the real ingest API. The expected monthly measures and balances are summed
here from the generated journal lines, then the agent's own tools (financial_kpis, kpi_detail) are run as the
agent runs them and every KPI that can be derived from the story is compared.

It also checks: a second workspace sees none of it; an unbalanced ledger is reported as unbalanced; a month with
no postings is not filled in with invented figures.

    python3 reports/ai-agent-audit/evals/e2_ledger_grounding.py
"""
from __future__ import annotations

import random
from collections import defaultdict
from decimal import Decimal as D

from common import Harness, run_tool, save, tool_ctx

SEED = 7031
rng = random.Random(SEED)
MONTHS = [f"{2025 + (k // 12)}-{k % 12 + 1:02d}" for k in range(22)]  # 2025-01 .. 2026-10
ACC = {"bank": ("1010", "Bank current account", "Bank"), "ar": ("1100", "Trade receivables", "Receivable"),
       "inv": ("1300", "Inventory", "Asset"), "eq": ("1600", "Vehicles and equipment", "Asset"),
       "ap": ("2100", "Trade payables", "Payable"), "vat": ("2200", "VAT payable", "Liability"),
       "loan": ("2700", "Bank loan - long term", "Liability"), "cap": ("3000", "Share capital", "Equity"),
       "sales": ("4000", "Sales", "Income"), "cogs": ("5000", "Cost of goods sold", "Cost of sales"),
       "sal": ("6100", "Salaries and wages", "Expense"), "rent": ("6200", "Office rent", "Expense"),
       "dep": ("6400", "Depreciation", "Expense"), "int": ("7100", "Interest on bank loan", "Other expense")}


def money(lo: float, hi: float) -> D:
    return D(rng.randint(int(lo * 100), int(hi * 100))) / 100


def build() -> tuple[list[dict], dict]:
    lines: list[dict] = []
    n = [0]
    bal = defaultdict(D)                       # running balances, debit positive
    flow = defaultdict(lambda: defaultdict(D))  # month -> measure -> amount

    def je(day: str, party: str | None, desc: str, *legs: tuple[str, D, D]) -> None:
        n[0] += 1
        assert sum(dr for _, dr, _ in legs) == sum(cr for _, _, cr in legs), legs
        for a, dr, cr in legs:
            code, name, cls = ACC[a]
            lines.append({"date": day, "entry": f"JE{n[0]:05d}", "type": "Journal", "account_code": code,
                          "account": name, "account_class": cls, "debit": float(dr), "credit": float(cr),
                          "description": desc, "party": party, "tags": []})
            bal[a] += dr - cr
        m = day[:7]
        for a, dr, cr in legs:
            if a == "sales":
                flow[m]["revenue"] += cr - dr
            elif a == "cogs":
                flow[m]["cogs"] += dr - cr
            elif a in ("sal", "rent"):
                flow[m]["opex_cash"] += dr - cr
            elif a == "dep":
                flow[m]["dep"] += dr - cr
            elif a == "int":
                flow[m]["interest"] += dr - cr
            elif a == "inv" and dr:
                flow[m]["purchases"] += dr

    z = D(0)
    je("2025-01-01", None, "Capital and loan", ("bank", D(250000), z), ("cap", z, D(150000)), ("loan", z, D(100000)))
    je("2025-01-03", "Fleet Co", "Delivery van", ("eq", D(48000), z), ("bank", z, D(48000)))
    prev_sales: dict[str, D] = {}
    for k, m in enumerate(MONTHS):
        last = {"02": 28, "04": 30, "06": 30, "09": 30, "11": 30}.get(m[5:], 31)
        sales_total = z
        for c in range(rng.randint(3, 7)):
            net = money(4000, 18000)
            vat = (net * D("0.15")).quantize(D("0.01"))
            day = f"{m}-{rng.choice([1, 2, 15, last]):02d}"   # postings on the first and last day of the month too
            je(day, f"Customer {c}", "Invoice", ("ar", net + vat, z), ("sales", z, net), ("vat", z, vat))
            cost = (net * D("0.55")).quantize(D("0.01"))
            je(day, None, "Cost of sale", ("cogs", cost, z), ("inv", z, cost))
            sales_total += net
        if k % 5 == 3:  # a credit note (refund) reduces revenue in the month it is posted
            refund = money(500, 2000)
            je(f"{m}-{last:02d}", "Customer 0", "Credit note", ("sales", refund, z), ("ar", z, refund))
        buy = (sales_total * D("0.55")).quantize(D("0.01"))
        je(f"{m}-05", "Supplier", "Stock purchase", ("inv", buy, z), ("ap", z, buy))
        if k:
            collect = (prev_sales["ar_open"] * D("0.8")).quantize(D("0.01"))
            je(f"{m}-20", "Customers", "Receipts", ("bank", collect, z), ("ar", z, collect))
            pay = (prev_sales["ap_open"] * D("0.9")).quantize(D("0.01"))
            je(f"{m}-22", "Supplier", "Payment", ("ap", pay, z), ("bank", z, pay))
        s = money(9000, 11000)
        je(f"{m}-25", None, "Payroll", ("sal", s, z), ("bank", z, s))
        je(f"{m}-01", "Landlord", "Rent", ("rent", D(3500), z), ("bank", z, D(3500)))
        je(f"{m}-{last:02d}", None, "Depreciation", ("dep", D(800), z), ("eq", z, D(800)))
        je(f"{m}-{last:02d}", "Bank", "Loan interest", ("int", D(625), z), ("bank", z, D(625)))
        prev_sales = {"ar_open": bal["ar"], "ap_open": -bal["ap"]}
        flow[m]["bal"] = dict(bal)
    return lines, flow


def truth(flow: dict, month: str) -> dict[str, D | None]:
    ms = [m for m in MONTHS if m <= month]
    i = len(ms) - 1

    def s(measure: str, n: int) -> D:
        return sum((flow[m][measure] for m in ms[max(0, i - n + 1):]), D(0))

    def gp(n):
        return s("revenue", n) - s("cogs", n)

    def op(n):
        return gp(n) - s("opex_cash", n) - s("dep", n)

    b = flow[month]["bal"]
    cash, ar, inv, ap, vat = b["bank"], b["ar"], b["inv"], -b["ap"], -b["vat"]
    ca, cl = cash + ar + inv, ap + vat
    equity = -b["cap"] + sum((flow[m]["revenue"] - flow[m]["cogs"] - flow[m]["opex_cash"] - flow[m]["dep"] - flow[m]["interest"]
                              for m in ms), D(0))
    total_assets = ca + b["eq"]
    rev3 = s("revenue", 3)
    out = {
        "gross_margin": gp(3) / rev3, "operating_margin": op(3) / rev3,
        "ebitda_margin": (op(3) + s("dep", 3)) / rev3, "net_margin": (op(3) - s("interest", 3)) / rev3,
        "current_ratio": ca / cl, "quick_ratio": (ca - inv) / cl, "cash_ratio": cash / cl, "working_capital": ca - cl,
        "dso": ar / rev3 * D("91.25"), "dio": inv / s("cogs", 3) * D("91.25"), "dpo": ap / s("purchases", 3) * D("91.25"),
        "debt_to_equity": -b["loan"] / equity, "equity_ratio": equity / total_assets,
        "revenue_growth": (flow[month]["revenue"] / flow[ms[i - 12]]["revenue"] - 1) if i >= 12 else None,
        "interest_cover": op(12) / s("interest", 12) if i >= 11 else None,
    }
    return out


def main() -> None:
    lines, flow = build()
    h = Harness()
    ws = h.setup_workspace(with_model=False)
    conn = h.api("POST", f"/api/w/{ws}/connections/custom", json={"label": "ERP", "mode": "push"})
    r = h.client.post("/api/ingest/v1/accounting/journal-lines", json={"lines": lines},
                      headers={"authorization": f"Bearer {conn['ingest_key']}"})
    assert r.status_code == 200, r.text
    ctx = tool_ctx(h, ws)
    checks: list[dict] = []

    def compare(month: str) -> None:
        o = run_tool(h, ctx, "financial_kpis", {"month": month})
        assert o is not None, "financial_kpis was not offered"
        got = {k["id"]: k["value"] for k in o.data["kpis"]}
        for kpi, exp in truth(flow, month).items():
            g = got.get(kpi)
            if exp is None:
                ok = g is None
            else:
                ok = g is not None and abs(D(str(g)) - exp) <= abs(exp) * D("0.0005") + D("0.005")
            checks.append({"month": month, "kpi": kpi, "expected": None if exp is None else float(exp),
                           "got": g, "pass": ok})
        # what the model reads: the text table must carry the same figure, formatted
        checks.append({"month": month, "kpi": "text_has_gross_margin", "expected": f"{float(truth(flow, month)['gross_margin']) * 100:,.1f}%",
                       "got": None, "pass": f"{float(truth(flow, month)['gross_margin']) * 100:,.1f}%" in o.text})

    for month in ("2026-10", "2026-03", "2025-12", "2025-06"):
        compare(month)

    # kpi_detail: the series the model is shown for DSO, month by month
    o = run_tool(h, ctx, "kpi_detail", {"kpi": "dso"})
    series = {s["month"]: s["value"] for s in o.data["series"]}
    for m in MONTHS[3:]:
        exp = truth(flow, m)["dso"]
        g = series.get(m)
        checks.append({"month": m, "kpi": "kpi_detail.dso", "expected": float(exp), "got": g,
                       "pass": g is not None and abs(D(str(g)) - exp) <= abs(exp) * D("0.0005") + D("0.005")})

    # a month after the ledger ends is not filled in
    o = run_tool(h, ctx, "financial_kpis", {"month": "2027-06"})
    checks.append({"month": "2027-06", "kpi": "future_month_not_invented", "expected": "no figures or a refusal",
                   "got": o.text[:200], "pass": not o.data or all(k["value"] is None for k in (o.data or {}).get("kpis", []))
                   if isinstance(o.data, dict) else True})

    # workspace B: no ledger, so the ledger tools are not even offered, and nothing of A can be reached
    ws_b = h.api("POST", "/api/workspaces", json={"name": "Other Co", "base_currency": "USD"})["id"]
    ctx_b = tool_ctx(h, ws_b)
    checks.append({"month": "-", "kpi": "tenant_b_has_no_ledger_tools", "expected": None, "got": None,
                   "pass": run_tool(h, ctx_b, "financial_kpis", {}) is None})

    # an unbalanced ledger is reported, not silently analysed
    ws_c = h.api("POST", "/api/workspaces", json={"name": "Broken Books", "base_currency": "USD"})["id"]
    conn_c = h.api("POST", f"/api/w/{ws_c}/connections/custom", json={"label": "ERP", "mode": "push"})
    bad = [dict(x) for x in lines[:400]]
    bad[10]["debit"] = bad[10]["debit"] + 1234.56 if bad[10]["debit"] else 1234.56
    r = h.client.post("/api/ingest/v1/accounting/journal-lines", json={"lines": bad},
                      headers={"authorization": f"Bearer {conn_c['ingest_key']}"})
    if r.status_code == 200:
        o = run_tool(h, tool_ctx(h, ws_c), "financial_kpis", {})
        checks.append({"month": "-", "kpi": "unbalanced_ledger_is_flagged", "expected": "warning in the text",
                       "got": o.text.splitlines()[:2], "pass": "does not balance" in o.text})
    else:
        checks.append({"month": "-", "kpi": "unbalanced_ledger_is_flagged", "expected": "warning or refusal",
                       "got": f"ingest refused: {r.status_code} {r.text[:200]}", "pass": True})
    h.close()

    passed = sum(c["pass"] for c in checks)
    save("e2_ledger_grounding", {"seed": SEED, "journal_lines": len(lines), "checks": len(checks), "passed": passed,
                                 "results": checks})
    print(f"E2: {passed}/{len(checks)} passed ({len(lines)} journal lines)")
    for c in checks:
        if not c["pass"]:
            print("  FAIL", c["month"], c["kpi"], "expected", c["expected"], "got", c["got"])


if __name__ == "__main__":
    main()
