"""Monthly measures from general-ledger lines: P&L by role, month-end balances, and cash flow by activity.

* P&L measures are the month's net amounts per role (income positive, costs positive).
* Balances are month-end balances per role. They start from opening balances (taken from the connection's balance
  sheet when there is one, see ``opening_from_balance_sheet``) plus every movement. Equity is assets minus
  liabilities, so it always includes the profit that has not been closed yet.
* Cash flow uses the bank lines (accounts with the *cash* role). Each entry's net bank movement is classed by the
  other lines in the entry: financing when it touches debt or equity, investing when it touches fixed or other
  non-current assets, operating otherwise. Entries that only move money between bank accounts are left out.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..costcentres.rules import norm
from .roles import ASSET_ROLES, BALANCE_ROLES, INCOME_ROLES, LIABILITY_ROLES, PL_ROLES, account_key, suggest

DEBT_ROLES = {"short_term_debt", "long_term_debt"}

MEASURES: dict[str, tuple[str, str]] = {  # name -> (kind, description)
    "revenue": ("flow", "Revenue"), "other_income": ("flow", "Other income"), "cogs": ("flow", "Cost of sales"),
    "payroll": ("flow", "Payroll"), "opex": ("flow", "Other operating expenses"),
    "depreciation": ("flow", "Depreciation and amortisation"), "interest": ("flow", "Interest"),
    "tax": ("flow", "Tax on profit"), "other_expense": ("flow", "Other expenses"),
    "gross_profit": ("flow", "Revenue − cost of sales"),
    "total_opex": ("flow", "Payroll + other operating expenses + depreciation"),
    "ebitda": ("flow", "Gross profit − payroll − other operating expenses"),
    "operating_profit": ("flow", "EBITDA − depreciation"),
    "net_profit": ("flow", "Operating profit + other income − other expenses − interest − tax"),
    "purchases": ("flow", "Supplier bills raised (credits to payables)"),
    "invoiced": ("flow", "Customer invoices raised (debits to receivables)"),
    "collections": ("flow", "Customer receipts (credits to receivables paid into the bank)"),
    "cash_in": ("flow", "Money into the bank"), "cash_out": ("flow", "Money out of the bank"),
    "ocf": ("flow", "Operating cash flow"), "icf": ("flow", "Investing cash flow"), "fcf_fin": ("flow", "Financing cash flow"),
    "burn": ("flow", "Net cash out from operating and investing (positive when burning)"),
    "debt_repaid": ("flow", "Debt repaid from the bank"),
    "cash": ("stock", "Bank and cash"), "receivables": ("stock", "Receivables"), "inventory": ("stock", "Inventory"),
    "other_current_assets": ("stock", "Other current assets"), "fixed_assets": ("stock", "Fixed assets (net)"),
    "other_non_current_assets": ("stock", "Other non-current assets"), "payables": ("stock", "Payables"),
    "other_current_liabilities": ("stock", "Other current liabilities"), "short_term_debt": ("stock", "Short-term debt"),
    "long_term_debt": ("stock", "Long-term debt"), "other_non_current_liabilities": ("stock", "Other non-current liabilities"),
    "current_assets": ("stock", "Current assets"), "total_assets": ("stock", "Total assets"),
    "current_liabilities": ("stock", "Current liabilities"), "total_liabilities": ("stock", "Total liabilities"),
    "debt": ("stock", "Total debt"), "net_debt": ("stock", "Debt − cash"), "equity": ("stock", "Assets − liabilities"),
    "working_capital": ("stock", "Current assets − current liabilities"),
    "top5_share": ("flow", "Share of revenue from the five largest customers (12 months)"),
    "ar_overdue_pct": ("snapshot", "Share of open receivables more than 30 days overdue"),
    "ap_overdue_pct": ("snapshot", "Share of open payables more than 30 days overdue"),
    "plan_revenue": ("flow", "Revenue in the approved plan"),
}


def month_of(d: str) -> str:
    return d[:7]


def month_range(first: str, last: str) -> list[str]:
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class Ledger:
    def __init__(self, lines: list[dict[str, Any]], roles: dict[str, str], *, opening: dict[str, float] | None = None,
                 last_date: str | None = None) -> None:
        self.lines = [ln for ln in lines if ln["date"] and (not last_date or ln["date"] <= last_date)]
        self.roles = roles
        self.opening = opening or {}
        dates = [ln["date"] for ln in self.lines]
        self.first_date = min(dates) if dates else None
        self.last_date = max(dates) if dates else None
        self.months = month_range(month_of(self.first_date), month_of(self.last_date)) if dates else []
        self.flows: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.by_account: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        self.balances: dict[str, dict[str, float]] = {}
        self.stocks: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.snapshot: dict[str, float | None] = {}
        self.party_revenue: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.revenue_lines = self.revenue_lines_with_party = 0
        self.names: dict[str, str] = {}
        self._build()

    def role_of(self, ln: dict[str, Any]) -> str:
        key = account_key(ln["account_code"], ln["account"])
        return self.roles.get(key) or suggest(ln["account_class"], ln["account"])

    def _build(self) -> None:
        movement: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        key_role: dict[str, str] = {}
        entries: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
        for ln in self.lines:
            key = account_key(ln["account_code"], ln["account"])
            role = self.role_of(ln)
            key_role[key] = role
            self.names[key] = f"{ln['account_code']} {ln['account']}".strip() if ln["account_code"] else ln["account"]
            m = month_of(ln["date"])
            amt = ln["debit"] - ln["credit"]
            if role in PL_ROLES:
                v = -amt if role in INCOME_ROLES else amt
                self.flows[role][m] += v
                self.by_account[role][m][key] += v
                if role == "revenue":
                    self.revenue_lines += 1
                    if ln.get("party"):
                        self.revenue_lines_with_party += 1
                        self.party_revenue[m][norm(ln["party"])] += v
            else:
                movement[key][m] += amt
            entries[ln["entry"]].append((ln, role))
        # month-end balances
        for key in set(movement) | {k for k in self.opening if k in key_role}:
            role = key_role.get(key)
            if role is None or role not in BALANCE_ROLES:
                continue
            bal = self.opening.get(key, 0.0)
            series = {}
            for m in self.months:
                bal += movement[key].get(m, 0.0)
                series[m] = bal
            self.balances[key] = series
            sign = 1 if role in ASSET_ROLES else -1
            for m, v in series.items():
                self.stocks[role][m] += sign * v
                self.by_account[role][m][key] += sign * v
        # cash flow by activity, and flows that come from payables and receivables
        for lines in entries.values():
            cash = [ln for ln, r in lines if r == "cash"]
            others = [(ln, r) for ln, r in lines if r != "cash"]
            m = month_of(min(ln["date"] for ln, _ in lines))
            if not cash:
                for ln, r in others:
                    if r == "payables":
                        self.flows["purchases"][m] += ln["credit"]
                    elif r == "receivables":
                        self.flows["invoiced"][m] += ln["debit"]
                continue
            if not others:
                continue  # transfer between bank accounts
            net = sum(ln["debit"] - ln["credit"] for ln in cash)
            roles_here = {r for _, r in others}
            if roles_here & (DEBT_ROLES | {"equity"}):
                cat = "fcf_fin"
                if net < 0:
                    repaid = sum(ln["debit"] for ln, r in others if r in DEBT_ROLES)
                    self.flows["debt_repaid"][m] += min(repaid, -net)
            elif roles_here & {"fixed_assets", "other_non_current_assets"}:
                cat = "icf"
            else:
                cat = "ocf"
            self.flows[cat][m] += net
            self.flows["cash_in" if net > 0 else "cash_out"][m] += abs(net)
            for ln, r in others:
                if r == "receivables":
                    self.flows["collections"][m] += ln["credit"]
        # derived measures
        f = self.flows
        for m in self.months:
            gp = f["revenue"][m] - f["cogs"][m]
            ebitda = gp - f["payroll"][m] - f["opex"][m]
            op = ebitda - f["depreciation"][m]
            f["gross_profit"][m] = gp
            f["total_opex"][m] = f["payroll"][m] + f["opex"][m] + f["depreciation"][m]
            f["ebitda"][m] = ebitda
            f["operating_profit"][m] = op
            f["net_profit"][m] = op + f["other_income"][m] - f["other_expense"][m] - f["interest"][m] - f["tax"][m]
            f["burn"][m] = -(f["ocf"][m] + f["icf"][m])
            s = self.stocks
            ca = s["cash"][m] + s["receivables"][m] + s["inventory"][m] + s["other_current_assets"][m]
            ta = ca + s["fixed_assets"][m] + s["other_non_current_assets"][m]
            cl = s["payables"][m] + s["other_current_liabilities"][m] + s["short_term_debt"][m]
            tl = cl + s["long_term_debt"][m] + s["other_non_current_liabilities"][m]
            debt = s["short_term_debt"][m] + s["long_term_debt"][m]
            s["current_assets"][m], s["total_assets"][m] = ca, ta
            s["current_liabilities"][m], s["total_liabilities"][m] = cl, tl
            s["debt"][m], s["net_debt"][m] = debt, debt - s["cash"][m]
            s["equity"][m], s["working_capital"][m] = ta - tl, ca - cl
        # concentration, from the ledger when most revenue lines name the customer
        if self.revenue_lines and self.revenue_lines_with_party / self.revenue_lines >= 0.5:
            for i, m in enumerate(self.months):
                if i < 11:
                    continue
                window = self.months[i - 11:i + 1]
                totals: dict[str, float] = defaultdict(float)
                for w in window:
                    for p, v in self.party_revenue[w].items():
                        totals[p] += v
                total = sum(f["revenue"][w] for w in window)
                if total > 0:
                    f["top5_share"][m] = sum(sorted(totals.values(), reverse=True)[:5]) / total

    def value(self, name: str, month: str) -> float | None:
        """A measure in a month; None before the ledger starts (or for a month with no figure of that kind)."""
        if month not in self.months:
            return None
        kind = MEASURES.get(name, ("flow", ""))[0]
        if kind == "snapshot":
            return self.snapshot.get(name) if month == self.months[-1] else None
        if kind == "stock":
            return self.stocks[name].get(month, 0.0)
        if name == "top5_share":
            return self.flows[name].get(month)
        if name == "plan_revenue":
            return self.flows[name].get(month)
        return self.flows[name].get(month, 0.0)

    def accounts(self, measure: str, month: str, limit: int = 12) -> list[dict[str, Any]]:
        """The accounts behind a base measure in a month (for drill-down)."""
        rows = self.by_account.get(measure, {}).get(month, {})
        items = sorted(rows.items(), key=lambda kv: -abs(kv[1]))
        return [{"account": self.names.get(k, k), "key": k, "amount": round(v, 2)} for k, v in items[:limit] if abs(v) > 0.004]

    def balance_check(self) -> dict[str, Any]:
        """Debits equal credits in every month (a ledger that does not balance cannot give reliable ratios)."""
        diff = round(sum(ln["debit"] - ln["credit"] for ln in self.lines), 2)
        return {"balanced": abs(diff) < 0.5, "difference": diff}


def opening_from_balance_sheet(lines: list[dict[str, Any]], bs_rows: list[list[Any]]) -> tuple[dict[str, float], dict[str, Any]]:
    """Opening balances (at the ledger's first date) that make the month-end balances agree with a balance sheet.

    ``bs_rows`` are rows of a synced balance-sheet dataset: as of, section, group, account, amount. For every
    balance-sheet account in the ledger whose name is found on the balance sheet: opening = balance on the
    balance-sheet date − movements up to that date. Accounts whose name is not found keep a zero opening balance
    and are counted in the result, so the page can say how complete the balances are."""
    if not bs_rows:
        return {}, {"source": None}
    as_of = max(str(r[0])[:10] for r in bs_rows)
    bs: dict[str, float] = {}
    for r in bs_rows:
        if str(r[0])[:10] != as_of:
            continue
        section, account, amount = r[1], str(r[3] or ""), float(r[4] or 0)
        signed = amount if section == "Assets" else -amount
        bs[norm(account)] = bs.get(norm(account), 0.0) + signed
    moved: dict[str, float] = defaultdict(float)
    accounts: dict[str, tuple[str | None, str, str]] = {}
    for ln in lines:
        key = account_key(ln["account_code"], ln["account"])
        accounts[key] = (ln["account_code"], ln["account"], ln["account_class"])
        if ln["date"] <= as_of:
            moved[key] += ln["debit"] - ln["credit"]
    opening: dict[str, float] = {}
    matched = 0
    for key, (code, name, cls) in accounts.items():
        if cls in {"Income", "Other income", "Cost of sales", "Expense", "Other expense", "Equity"}:
            continue
        found = None
        for cand in (f"{code} {name}" if code else None, name, f"{code} - {name}" if code else None):
            if cand and norm(cand) in bs:
                found = bs[norm(cand)]
                break
        if found is None:
            continue
        matched += 1
        opening[key] = found - moved[key]
    unmatched = sum(1 for k, (_, _, c) in accounts.items() if c in {"Bank", "Receivable", "Payable", "Asset", "Liability"}
                    and k not in opening)
    return opening, {"source": "balance_sheet", "as_of": as_of, "matched_accounts": matched, "unmatched_accounts": unmatched,
                     "balance_sheet_accounts": len(bs)}
