"""Month-end close checks over general-ledger lines. Pure functions: no database, no model.

Everything here is arithmetic on the ledger, so every finding can be shown with the lines behind it:

* **health**: entries in the month that do not balance, accounts whose role is only suggested;
* **missing recurring costs**: a cost account charged every month for the last three months, within 25% of one
  another, with nothing this month. The proposal is an accrual (or a depreciation charge) of last month's amount;
* **customers not invoiced**: a customer invoiced in each of the last three months and not in this one;
* **variances**: P&L accounts whose month differs from the average of the previous three months by at least the
  materiality amount and 20%, explained by the month's largest entries on the account.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from statistics import mean
from typing import Any

from ..analysis.roles import PL_ROLES, account_key, role_fits, suggest
from ..costcentres.rules import norm

COST_ROLES = {"cogs", "payroll", "opex", "depreciation", "interest", "other_expense", "tax"}
INCOME_ROLES = {"revenue", "other_income"}
LOOKBACK = 3
REGULAR = 1.25          # largest / smallest of the last three months
VARIANCE_SHARE = 0.20   # and at least this share of the three-month average
MAX_VARIANCES = 12

ACCRUAL_WORDS = [norm(w) for w in ("accrued", "accrual", "accruals", "مصروفات مستحقة", "مستحقات", "مصاريف مستحقة",
                                   "devengad", "gastos devengados", "charges a payer", "charges à payer")]
ACCUM_DEP_WORDS = [norm(w) for w in ("accumulated depreciation", "accumulated amortisation", "accumulated amortization",
                                     "مجمع الاهلاك", "مجمع الاستهلاك", "amortizacion acumulada",
                                     "amortissements cumules")]


def month_end(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def prev_months(month: str, n: int) -> list[str]:
    y, m = int(month[:4]), int(month[5:7])
    out = []
    for _ in range(n):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        out.append(f"{y:04d}-{m:02d}")
    return list(reversed(out))


def r2(v: float) -> float:
    return round(v + 0.0, 2)


class Accounts:
    """Roles for the ledger's accounts: the person's choice where it fits the class, the suggestion otherwise."""

    def __init__(self, lines: list[dict[str, Any]], chosen: dict[str, str]) -> None:
        self.info: dict[str, dict[str, Any]] = {}
        for ln in lines:
            k = account_key(ln["account_code"], ln["account"])
            if k not in self.info:
                c = chosen.get(k)
                role = c if c and role_fits(c, ln["account_class"]) else suggest(ln["account_class"], ln["account"])
                self.info[k] = {"key": k, "code": ln["account_code"], "name": ln["account"],
                                "class": ln["account_class"], "role": role, "confirmed": bool(c)}

    def role(self, ln: dict[str, Any]) -> str:
        return self.info[account_key(ln["account_code"], ln["account"])]["role"]

    def find(self, words: list[str], *, roles: set[str] | None = None, classes: set[str] | None = None) -> dict[str, Any] | None:
        for a in sorted(self.info.values(), key=lambda a: (a["code"] or "", a["name"])):
            if roles and a["role"] not in roles:
                continue
            if classes and a["class"] not in classes:
                continue
            n = f" {norm(a['name'])} "
            if any(w in n for w in words):
                return a
        return None


def line_ref(ln: dict[str, Any]) -> dict[str, Any]:
    return {"date": ln["date"], "entry": ln["entry"], "account_code": ln["account_code"], "account": ln["account"],
            "debit": r2(ln["debit"]), "credit": r2(ln["credit"]), "description": ln.get("description"),
            "party": ln.get("party")}


def health(lines: list[dict[str, Any]], accts: Accounts, month: str) -> dict[str, Any]:
    in_month = [ln for ln in lines if ln["date"][:7] == month]
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for ln in in_month:
        sums[ln["entry"]][0] += ln["debit"]
        sums[ln["entry"]][1] += ln["credit"]
    unbalanced = [{"entry": e, "debit": r2(d), "credit": r2(c), "difference": r2(d - c)}
                  for e, (d, c) in sums.items() if abs(d - c) > 0.01]
    used = {account_key(ln["account_code"], ln["account"]) for ln in in_month}
    unconfirmed = [accts.info[k] for k in used if not accts.info[k]["confirmed"]]
    later = sum(1 for ln in lines if ln["date"] > month_end(month))
    return {"lines": len(in_month), "entries": len(sums), "debit": r2(sum(d for d, _ in sums.values())),
            "credit": r2(sum(c for _, c in sums.values())), "unbalanced": unbalanced[:50],
            "unbalanced_count": len(unbalanced), "unconfirmed_accounts": len(unconfirmed),
            "unconfirmed": [{"code": a["code"], "name": a["name"], "role": a["role"]} for a in unconfirmed[:30]],
            "later_lines": later}


def _net_by_month(lines: list[dict[str, Any]], key: str) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for ln in lines:
        if account_key(ln["account_code"], ln["account"]) == key:
            out[ln["date"][:7]] += ln["debit"] - ln["credit"]
    return out


def missing_recurring(lines: list[dict[str, Any]], accts: Accounts, month: str) -> list[dict[str, Any]]:
    """Cost accounts charged in each of the last three months, regularly, with nothing this month."""
    history = prev_months(month, LOOKBACK)
    first = min((ln["date"][:7] for ln in lines), default=month)
    if history[0] < first:
        return []
    by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ln in lines:
        if ln["date"][:7] in history:
            by_entry[ln["entry"]].append(ln)
    accrual_acc = accts.find(ACCRUAL_WORDS, classes={"Liability"})
    out = []
    for key, a in accts.info.items():
        if a["role"] not in COST_ROLES - {"tax"}:
            continue
        net = _net_by_month(lines, key)
        amounts = [net.get(m, 0.0) for m in history]
        if min(amounts) <= 0 or max(amounts) / min(amounts) > REGULAR:
            continue
        if net.get(month, 0.0) > 0.005:
            continue
        expected = r2(amounts[-1])
        # where last month's charge went: the account credited most in the entries that charged this account
        credits: dict[str, float] = defaultdict(float)
        evidence = []
        for e, ent in by_entry.items():
            mine = [ln for ln in ent if account_key(ln["account_code"], ln["account"]) == key]
            if not mine:
                continue
            evidence.extend(line_ref(ln) for ln in mine)
            for ln in ent:
                k2 = account_key(ln["account_code"], ln["account"])
                if k2 != key and ln["credit"] > 0:
                    credits[k2] += ln["credit"]
        counterpart = accts.info[max(credits, key=lambda k: credits[k])] if credits else None
        if a["role"] == "depreciation":
            kind = "depreciation"
            acc = accts.find(ACCUM_DEP_WORDS, classes={"Asset"})
            if counterpart and counterpart["role"] not in ("cash", "payables", "receivables"):
                acc = counterpart
            credit_to = acc or {"code": None, "name": "Accumulated depreciation", "class": "Asset", "role": "fixed_assets"}
            what = f"Charge {a['name']} for {month}"
        else:
            kind = "accrual"
            credit_to = accrual_acc or {"code": None, "name": "Accrued expenses", "class": "Liability",
                                        "role": "other_current_liabilities"}
            what = f"Accrue {a['name']} for {month}"
        new_account = credit_to.get("code") is None and account_key(None, credit_to["name"]) not in accts.info
        out.append({
            "key": f"{kind}:{key}", "kind": kind, "amount": expected,
            "title": what,
            "reason": (f"{a['name']} was charged every month from {history[0]} to {history[-1]} "
                       f"({', '.join(f'{x:,.2f}' for x in amounts)}) and has nothing in {month}."),
            "method": "Last month's amount; the three months are within 25% of one another.",
            "history": [{"month": m, "amount": r2(x)} for m, x in zip(history, amounts, strict=True)],
            "lines": [
                {"account_code": a["code"], "account": a["name"], "account_class": a["class"], "debit": expected, "credit": 0.0},
                {"account_code": credit_to.get("code"), "account": credit_to["name"],
                 "account_class": credit_to["class"], "debit": 0.0, "credit": expected},
            ],
            "warning": (f"“{credit_to['name']}” is not in this ledger's chart of accounts; choose the account you use "
                        "before importing the journal.") if new_account else None,
            "evidence": sorted(evidence, key=lambda x: (x["date"], x["entry"]))[-12:],
        })
    return sorted(out, key=lambda x: -x["amount"])


def not_invoiced(lines: list[dict[str, Any]], accts: Accounts, month: str) -> list[dict[str, Any]]:
    """Customers invoiced in each of the last three months and not in this one."""
    history = prev_months(month, LOOKBACK)
    entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ln in lines:
        if ln["date"][:7] in history or ln["date"][:7] == month:
            entries[ln["entry"]].append(ln)
    by_party: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    last_ref: dict[str, dict[str, Any]] = {}
    for ent in entries.values():
        revenue = sum(ln["credit"] - ln["debit"] for ln in ent if accts.role(ln) == "revenue")
        if revenue <= 0:
            continue
        party = next((ln["party"] for ln in ent if ln.get("party")), None)
        if not party:
            continue
        m = ent[0]["date"][:7]
        by_party[party][m] += revenue
        if m in history:
            last_ref[party] = max(last_ref.get(party, ent[0]), ent[0], key=lambda x: x["date"])
    out = []
    for party, months in by_party.items():
        amounts = [months.get(m, 0.0) for m in history]
        if min(amounts) > 0 and months.get(month, 0.0) <= 0:
            ref = last_ref.get(party)
            out.append({"key": f"unbilled:{norm(party)}", "kind": "unbilled", "party": party,
                        "amount": r2(mean(amounts)),
                        "title": f"{party} was not invoiced in {month}",
                        "reason": (f"{party} was invoiced every month from {history[0]} to {history[-1]} "
                                   f"(average {mean(amounts):,.2f}) and has no revenue in {month}."),
                        "history": [{"month": m, "amount": r2(x)} for m, x in zip(history, amounts, strict=True)],
                        "evidence": [{"date": ref["date"], "entry": ref["entry"], "description": ref.get("description")}]
                        if ref else []})
    return sorted(out, key=lambda x: -x["amount"])


def materiality_default(lines: list[dict[str, Any]], accts: Accounts, month: str) -> float:
    history = prev_months(month, LOOKBACK)
    rev = sum(ln["credit"] - ln["debit"] for ln in lines if ln["date"][:7] in history and accts.role(ln) == "revenue")
    monthly = rev / LOOKBACK if rev > 0 else 0.0
    return float(max(500, round(monthly * 0.01 / 100) * 100))


def variances(lines: list[dict[str, Any]], accts: Accounts, month: str, materiality: float,
              skip: set[str] | None = None) -> list[dict[str, Any]]:
    """P&L accounts that moved by at least the materiality amount and 20% against the previous three months."""
    history = prev_months(month, LOOKBACK)
    first = min((ln["date"][:7] for ln in lines), default=month)
    usable = [m for m in history if m >= first]
    if not usable:
        return []
    net: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    month_lines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ln in lines:
        m = ln["date"][:7]
        if m != month and m not in usable:
            continue
        k = account_key(ln["account_code"], ln["account"])
        if accts.info[k]["role"] not in PL_ROLES:
            continue
        sign = -1 if accts.info[k]["role"] in INCOME_ROLES else 1
        net[k][m] += sign * (ln["debit"] - ln["credit"])
        if m == month:
            month_lines[k].append(ln)
    out = []
    for k, months in net.items():
        if skip and k in skip:
            continue
        cur = months.get(month, 0.0)
        avg = mean(months.get(m, 0.0) for m in usable)
        prev = months.get(usable[-1], 0.0)
        diff = cur - avg
        if abs(diff) < materiality or (avg and abs(diff) < VARIANCE_SHARE * abs(avg)):
            continue
        a = accts.info[k]
        top = sorted(month_lines[k], key=lambda ln: -abs(ln["debit"] - ln["credit"]))[:3]
        income = a["role"] in INCOME_ROLES
        up = diff > 0
        word = ("higher" if up else "lower")
        drivers = "; ".join(
            f"{ln['entry']} {ln['date']}"
            + (f" {ln['party']}" if ln.get("party") else "")
            + (f" “{ln['description']}”" if ln.get("description") else "")
            + f" {abs(ln['debit'] - ln['credit']):,.2f}" for ln in top)
        out.append({
            "key": f"variance:{k}", "kind": "variance", "account_code": a["code"], "account": a["name"],
            "role": a["role"], "income": income, "amount": r2(cur), "previous": r2(prev), "average": r2(avg),
            "difference": r2(diff), "change_pct": r2(diff / abs(avg) * 100) if avg else None,
            "good": (up if income else not up),
            "title": f"{a['name']}: {cur:,.2f} in {month}, {word} than the {len(usable)}-month average {avg:,.2f}",
            "reason": (f"{diff:+,.2f} against the average of {usable[0]} to {usable[-1]}"
                       + (f" ({diff / abs(avg) * 100:+.0f}%)" if avg else "") + "."
                       + (f" Largest entries this month: {drivers}." if top else " Nothing was posted this month.")),
            "evidence": [line_ref(ln) for ln in top],
        })
    out.sort(key=lambda x: -abs(x["difference"]))
    return out[:MAX_VARIANCES]


def prepare(lines: list[dict[str, Any]], chosen: dict[str, str], month: str, *,
            materiality: float | None = None) -> dict[str, Any]:
    """Everything the close computes from the ledger for one month (bank matching is added separately)."""
    accts = Accounts(lines, chosen)
    h = health(lines, accts, month)
    missing = missing_recurring(lines, accts, month)
    mat = materiality if materiality is not None else materiality_default(lines, accts, month)
    skip = {it["key"].split(":", 1)[1] for it in missing}
    var = variances(lines, accts, month, mat, skip)
    unbilled = not_invoiced(lines, accts, month)
    return {"month": month, "month_end": month_end(month), "materiality": mat, "health": h,
            "proposals": missing, "variances": var, "unbilled": unbilled,
            "cash_accounts": [{"key": a["key"], "code": a["code"], "name": a["name"]}
                              for a in accts.info.values() if a["role"] == "cash"]}
