"""The 35 standard KPIs, in international terms, as editable formulas.

Each KPI is a formula over monthly measures (see ``formula.py``). The finance lead can change a formula, set a target
and a warning level, switch a KPI off, or add their own. Margins and turnover days use the last three months so that
one unusual month does not swing them; returns use twelve months.
"""

from __future__ import annotations

from typing import Any

from ..errors import Invalid
from .formula import evaluate, names, parse
from .ledger import MEASURES, Ledger

CATEGORIES = ["growth_profitability", "liquidity", "working_capital", "leverage", "cash_flow", "cost_control"]
D = "91.25"  # days in three months

CATALOG: list[dict[str, Any]] = [
    # growth and profitability
    {"id": "revenue_growth", "name": "Revenue growth (year on year)", "category": "growth_profitability",
     "formula": "revenue / PREV(revenue, 12) - 1", "unit": "percent", "direction": "up"},
    {"id": "gross_margin", "name": "Gross margin", "category": "growth_profitability",
     "formula": "SUMN(gross_profit, 3) / SUMN(revenue, 3)", "unit": "percent", "direction": "up"},
    {"id": "ebitda_margin", "name": "EBITDA margin", "category": "growth_profitability",
     "formula": "SUMN(ebitda, 3) / SUMN(revenue, 3)", "unit": "percent", "direction": "up"},
    {"id": "operating_margin", "name": "Operating margin", "category": "growth_profitability",
     "formula": "SUMN(operating_profit, 3) / SUMN(revenue, 3)", "unit": "percent", "direction": "up"},
    {"id": "net_margin", "name": "Net profit margin", "category": "growth_profitability",
     "formula": "SUMN(net_profit, 3) / SUMN(revenue, 3)", "unit": "percent", "direction": "up"},
    {"id": "roe", "name": "Return on equity", "category": "growth_profitability",
     "formula": "SUMN(net_profit, 12) / AVGBAL(equity)", "unit": "percent", "direction": "up"},
    {"id": "roa", "name": "Return on assets", "category": "growth_profitability",
     "formula": "SUMN(net_profit, 12) / AVGBAL(total_assets)", "unit": "percent", "direction": "up"},
    {"id": "roce", "name": "Return on capital employed", "category": "growth_profitability",
     "formula": "SUMN(operating_profit, 12) / AVGBAL(total_assets - current_liabilities)", "unit": "percent",
     "direction": "up"},
    # liquidity
    {"id": "current_ratio", "name": "Current ratio", "category": "liquidity",
     "formula": "current_assets / current_liabilities", "unit": "ratio", "direction": "up"},
    {"id": "quick_ratio", "name": "Quick ratio", "category": "liquidity",
     "formula": "(current_assets - inventory) / current_liabilities", "unit": "ratio", "direction": "up"},
    {"id": "cash_ratio", "name": "Cash ratio", "category": "liquidity",
     "formula": "cash / current_liabilities", "unit": "ratio", "direction": "up"},
    {"id": "working_capital", "name": "Working capital", "category": "liquidity",
     "formula": "working_capital", "unit": "money", "direction": "up"},
    {"id": "days_cash", "name": "Days of cash on hand", "category": "liquidity",
     "formula": f"cash / (SUMN(cash_out, 3) / {D})", "unit": "days", "direction": "up"},
    # working capital
    {"id": "dso", "name": "Days sales outstanding (DSO)", "category": "working_capital",
     "formula": f"receivables / SUMN(revenue, 3) * {D}", "unit": "days", "direction": "down"},
    {"id": "dpo", "name": "Days payables outstanding (DPO)", "category": "working_capital",
     "formula": f"payables / SUMN(purchases, 3) * {D}", "unit": "days", "direction": "up"},
    {"id": "dio", "name": "Days inventory outstanding (DIO)", "category": "working_capital",
     "formula": f"inventory / SUMN(cogs, 3) * {D}", "unit": "days", "direction": "down"},
    {"id": "cash_conversion_cycle", "name": "Cash conversion cycle", "category": "working_capital",
     "formula": "dso + dio - dpo", "unit": "days", "direction": "down"},
    {"id": "ar_overdue", "name": "Receivables more than 30 days overdue", "category": "working_capital",
     "formula": "ar_overdue_pct", "unit": "percent", "direction": "down"},
    {"id": "ap_overdue", "name": "Payables more than 30 days overdue", "category": "working_capital",
     "formula": "ap_overdue_pct", "unit": "percent", "direction": "down"},
    {"id": "collection_rate", "name": "Collection rate", "category": "working_capital",
     "formula": "SUMN(collections, 3) / (PREV(receivables, 3) + SUMN(invoiced, 3))", "unit": "percent",
     "direction": "up"},
    # leverage
    {"id": "debt_to_equity", "name": "Debt to equity", "category": "leverage",
     "formula": "debt / equity", "unit": "ratio", "direction": "down"},
    {"id": "net_debt_to_ebitda", "name": "Net debt to EBITDA", "category": "leverage",
     "formula": "net_debt / SUMN(ebitda, 12)", "unit": "ratio", "direction": "down"},
    {"id": "interest_cover", "name": "Interest cover", "category": "leverage",
     "formula": "SUMN(operating_profit, 12) / SUMN(interest, 12)", "unit": "ratio", "direction": "up"},
    {"id": "dscr", "name": "Debt service coverage (DSCR)", "category": "leverage",
     "formula": "SUMN(ocf, 12) / (SUMN(debt_repaid, 12) + SUMN(interest, 12))", "unit": "ratio", "direction": "up"},
    {"id": "equity_ratio", "name": "Equity ratio", "category": "leverage",
     "formula": "equity / total_assets", "unit": "percent", "direction": "up"},
    # cash flow
    {"id": "operating_cash_flow", "name": "Operating cash flow (3 months)", "category": "cash_flow",
     "formula": "SUMN(ocf, 3)", "unit": "money", "direction": "up"},
    {"id": "free_cash_flow", "name": "Free cash flow (3 months)", "category": "cash_flow",
     "formula": "SUMN(ocf, 3) + SUMN(icf, 3)", "unit": "money", "direction": "up"},
    {"id": "cash_runway", "name": "Cash runway", "category": "cash_flow",
     "formula": "cash / IFPOS(SUMN(burn, 3) / 3)", "unit": "months", "direction": "up"},
    {"id": "earnings_quality", "name": "Cash conversion of profit", "category": "cash_flow",
     "formula": "SUMN(ocf, 12) / SUMN(net_profit, 12)", "unit": "ratio", "direction": "up"},
    # cost and control
    {"id": "opex_ratio", "name": "Operating expenses to revenue", "category": "cost_control",
     "formula": "SUMN(total_opex, 3) / SUMN(revenue, 3)", "unit": "percent", "direction": "down"},
    {"id": "payroll_ratio", "name": "Payroll to revenue", "category": "cost_control",
     "formula": "SUMN(payroll, 3) / SUMN(revenue, 3)", "unit": "percent", "direction": "down"},
    {"id": "margin_of_safety", "name": "Margin of safety over break-even", "category": "cost_control",
     "formula": "1 - SUMN(total_opex, 12) / SUMN(gross_profit, 12)",
     "unit": "percent", "direction": "up"},
    {"id": "plan_variance", "name": "Revenue against plan", "category": "cost_control",
     "formula": "revenue / plan_revenue - 1", "unit": "percent", "direction": "up"},
    {"id": "forecast_accuracy", "name": "Forecast accuracy (revenue, 3 months)", "category": "cost_control",
     "formula": "1 - AVGN(ABS(revenue - plan_revenue) / revenue, 3)", "unit": "percent", "direction": "up"},
    {"id": "customer_concentration", "name": "Revenue from top 5 customers", "category": "cost_control",
     "formula": "top5_share", "unit": "percent", "direction": "down"},
]
BY_ID = {k["id"]: k for k in CATALOG}
UNITS = {"percent", "ratio", "days", "money", "months", "number"}


def definitions(settings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The catalogue with the workspace's changes applied, plus its own KPIs."""
    out = []
    for k in CATALOG:
        s = settings.get(k["id"], {})
        out.append(k | {"standard_formula": k["formula"], "formula": s.get("formula") or k["formula"],
                        "target": s.get("target"), "warn": s.get("warn"), "enabled": s.get("enabled", True),
                        "custom": False, "changed": bool(s.get("formula"))})
    for kid, s in settings.items():
        if kid not in BY_ID and s.get("name"):
            out.append({"id": kid, "name": s["name"], "category": s.get("category") or "cost_control",
                        "formula": s["formula"], "unit": s.get("unit") or "number", "direction": s.get("direction") or "up",
                        "target": s.get("target"), "warn": s.get("warn"), "enabled": s.get("enabled", True),
                        "custom": True, "changed": False, "standard_formula": None})
    return out


def check_formula(text: str, known_kpis: set[str]) -> None:
    node = parse(text)
    unknown = sorted(n for n in names(node) if n not in MEASURES and n not in known_kpis)
    if unknown:
        raise Invalid(f"Unknown name{'s' if len(unknown) > 1 else ''} in the formula: {', '.join(unknown)}. Use measures "
                      f"such as revenue, cash or receivables, or the id of another KPI.", code="bad_formula")


def status(value: float | None, k: dict[str, Any]) -> str | None:
    target, warn = k.get("target"), k.get("warn")
    if value is None or target is None:
        return None
    up = k.get("direction", "up") == "up"
    if (value >= target) if up else (value <= target):
        return "good"
    if warn is not None and ((value >= warn) if up else (value <= warn)):
        return "watch"
    return "act"


class Evaluator:
    def __init__(self, ledger: Ledger, defs: list[dict[str, Any]]) -> None:
        self.ledger = ledger
        self.defs = {k["id"]: k for k in defs}
        self.nodes: dict[str, Any] = {}
        self.cache: dict[tuple[str, int], float | None] = {}
        self.errors: dict[str, str] = {}
        for k in defs:
            try:
                self.nodes[k["id"]] = parse(k["formula"])
            except Invalid as exc:
                self.errors[k["id"]] = exc.message

    def value(self, name: str, i: int, stack: tuple[str, ...] = ()) -> float | None:
        months = self.ledger.months
        if i < 0 or i >= len(months):
            return None
        if name in self.nodes and name not in MEASURES:
            if name in stack:
                return None  # a KPI that refers back to itself
            key = (name, i)
            if key not in self.cache:
                self.cache[key] = evaluate(self.nodes[name], months, i,
                                          lambda n, j: self.value(n, j, (*stack, name)))
            return self.cache[key]
        return self.ledger.value(name, months[i])

    def series(self, kid: str, last: int = 13) -> list[dict[str, Any]]:
        months = self.ledger.months
        start = max(0, len(months) - last)
        return [{"month": months[i], "value": self.value(kid, i)} for i in range(start, len(months))]

    def scorecard(self, month: str | None = None) -> list[dict[str, Any]]:
        months = self.ledger.months
        i = months.index(month) if month in months else len(months) - 1
        out = []
        for kid, k in self.defs.items():
            if not k.get("enabled", True):
                continue
            v = self.value(kid, i) if kid not in self.errors else None
            prev = self.value(kid, i - 1) if kid not in self.errors else None
            spark = [self.value(kid, j) for j in range(max(0, i - 11), i + 1)]
            out.append({**{x: k[x] for x in ("id", "name", "category", "unit", "direction", "target", "warn", "formula",
                                             "custom", "changed")},
                        "month": months[i] if months else None, "value": v, "previous": prev,
                        "status": status(v, k), "spark": spark, "error": self.errors.get(kid),
                        "missing": self.missing(kid, i) if v is None else None})
        return out

    def lookback(self, node: Any, seen: tuple[str, ...] = ()) -> int:
        """Months of history a formula needs before the month it is computed for."""
        from .formula import Fn, Name, Op

        if isinstance(node, Name):
            if node.n in self.nodes and node.n not in MEASURES and node.n not in seen:
                return self.lookback(self.nodes[node.n], (*seen, node.n))
            return 0
        if isinstance(node, Op):
            return max(self.lookback(node.a, seen), self.lookback(node.b, seen))
        if isinstance(node, Fn):
            inner = max(self.lookback(a, seen) for a in node.args)
            if node.f in {"SUMN", "AVGN"}:
                return inner + int(node.args[1].v) - 1
            if node.f == "PREV":
                return inner + int(node.args[1].v)
            if node.f == "AVGBAL":
                return inner + 12
            return inner
        return 0

    def missing(self, kid: str, i: int) -> str:
        """Why a KPI has no value: no documents, no plan, no customer names, too little history, or a zero divisor."""
        node = self.nodes.get(kid)
        if node is None:
            return "formula"
        used = names(node)
        if used & {"ar_overdue_pct", "ap_overdue_pct"} and all(self.ledger.snapshot.get(n) is None for n in used
                                                               & {"ar_overdue_pct", "ap_overdue_pct"}):
            return "no_documents"
        if "plan_revenue" in used and not self.ledger.flows.get("plan_revenue"):
            return "no_plan"
        if "top5_share" in used and self.ledger.revenue_lines_with_party * 2 < max(self.ledger.revenue_lines, 1):
            return "no_customers"
        if i < self.lookback(node) or ("top5_share" in used and i < 11):
            return "history"
        if "burn" in used and "IFPOS" in repr(node):
            return "not_burning"
        return "zero"

    def explain(self, kid: str, month: str | None = None) -> dict[str, Any]:
        k = self.defs.get(kid)
        if k is None:
            raise Invalid("Unknown KPI.")
        months = self.ledger.months
        i = months.index(month) if month in months else len(months) - 1
        node = self.nodes.get(kid)
        parts = []
        for n in sorted(names(node) if node is not None else set()):
            if n in MEASURES:
                parts.append({"name": n, "kind": MEASURES[n][0], "description": MEASURES[n][1],
                              "value": self.ledger.value(n, months[i]) if months else None,
                              "accounts": self.ledger.accounts(n, months[i]) if months else []})
            elif n in self.defs:
                parts.append({"name": n, "kind": "kpi", "description": self.defs[n]["name"],
                              "value": self.value(n, i), "accounts": []})
        return {"kpi": {x: k[x] for x in ("id", "name", "category", "unit", "direction", "target", "warn", "formula",
                                          "standard_formula", "custom", "changed")},
                "month": months[i] if months else None, "value": self.value(kid, i) if months else None,
                "series": self.series(kid, 24), "parts": parts, "error": self.errors.get(kid)}
