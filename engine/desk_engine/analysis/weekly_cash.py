"""Thirteen weeks of cash by the direct method: what is owed, what is owing and what is paid every month.

The monthly forecast (``forecast.py``) carries a plan forward. A lender or a board asks for something else: week by
week, the cash that existing invoices will bring in and that existing bills and standing payments will take out. The
ledger holds what that needs, so it is worked out here and every figure traces to the entries it came from:

- **Receipts.** Each open customer invoice is placed in the week it is expected: the invoice date plus the days that
  customer has taken to pay (their own settled invoices of the last twelve months, weighted by amount), or plus the
  payment terms where there is no such history. The ledger carries no due date, so terms are an assumption the
  caller gives (30 days unless told otherwise) and the result says which was used for whom.
- **Bills.** Open supplier bills, placed the same way from the days this company has taken to pay that supplier.
- **Standing payments.** Payments made straight from the bank to the same account in each of the last three months
  for about the same amount (rent, payroll, a loan instalment), or every third month three times (a quarterly tax),
  are carried forward on the same days at the latest amount. Each one names the past entries it was found from.

What it leaves out is said in the result and never filled in: sales not yet invoiced, bills not yet received, and
payments that follow no pattern. With fewer than three months of ledger the standing payments cannot be found, so
the weekly balances are withheld rather than shown without them.

Nothing here changes data, proposes an entry or carries a score.
"""

from __future__ import annotations

import calendar
import math
from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from ..errors import Invalid
from .ledger import Ledger, month_of
from .roles import account_key

WEEKS = 13
DEFAULT_TERMS = 30          # days, where a party has no payment history of its own
MAX_TERMS = 365
HISTORY_DOCUMENTS = 3       # settled documents a party needs before its own history is used
HISTORY_DAYS = 365          # ... settled within this many days of the date the forecast starts from
PATTERN_MONTHS = 3          # a standing payment was paid in each of this many months
PATTERN_SPREAD = 0.20       # ... for amounts within this share of their median
MAX_ITEMS = 200
MAX_PARTIES = 60
NO_PARTY = "—"


def month_end(month: str) -> date:
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _shift(d: date, months: int) -> date:
    """The same day ``months`` later; the last day of the month where that month is shorter."""
    total = d.year * 12 + d.month - 1 + months
    y, m = total // 12, total % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _week(d: date, as_of: date) -> int:
    """Week 1 is the seven days after ``as_of``."""
    return math.ceil((d - as_of).days / 7)


def _cash_by_entry(led: Ledger, last: str) -> dict[str, float]:
    """Net movement on bank and cash accounts in each entry (positive is money in)."""
    net: dict[str, float] = defaultdict(float)
    for ln in led.lines:
        if ln["date"] <= last and led.role_of(ln) == "cash":
            net[ln["entry"]] += ln["debit"] - ln["credit"]
    return net


def open_items(led: Ledger, kind: str, as_of: date, cash: dict[str, float]) -> dict[str, dict[str, Any]]:
    """What each party still owes (receivables) or is owed (payables) at ``as_of``, document by document, and how
    long its settled documents took.

    Settlements are set against the oldest open document first, as the ageing report does. Money received before
    there is a document to set it against waits as a credit and covers the party's next document. Only a settlement
    that moved cash counts as payment history: a credit note closes an invoice and says nothing about how long the
    customer takes to pay."""
    last = as_of.isoformat()
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"documents": [], "credit": 0.0, "paid": []})
    for ln in sorted(led.lines, key=lambda x: (x["date"], x.get("row") or 0)):
        if ln["date"] > last or led.role_of(ln) != kind:
            continue
        p = out[(ln.get("party") or "").strip() or NO_PARTY]
        raised = ln["debit"] if kind == "receivables" else ln["credit"]
        settled = ln["credit"] if kind == "receivables" else ln["debit"]
        if raised > 0.005:
            doc = {"date": ln["date"], "entry": ln["entry"], "amount": raised, "left": raised}
            take = min(p["credit"], raised)
            doc["left"] -= take
            p["credit"] -= take
            p["documents"].append(doc)
        if settled > 0.005:
            left = settled
            for doc in p["documents"]:
                if left <= 0.005:
                    break
                if doc["left"] <= 0.005:
                    continue
                take = min(doc["left"], left)
                doc["left"] -= take
                left -= take
                if abs(cash.get(ln["entry"], 0.0)) > 0.005:
                    p["paid"].append({"raised": doc["date"], "paid": ln["date"], "amount": take,
                                      "document": (doc["date"], doc["entry"])})
            if left > 0.005:
                p["credit"] += left
    return out


def habit(paid: list[dict[str, Any]], as_of: date) -> dict[str, Any] | None:
    """The days a party's settled documents took, weighted by amount; None without enough of them to say."""
    since = (as_of - timedelta(days=HISTORY_DAYS)).isoformat()
    recent = [x for x in paid if x["paid"] >= since]
    documents = {x["document"] for x in recent}
    total = sum(x["amount"] for x in recent)
    if len(documents) < HISTORY_DOCUMENTS or total <= 0.005:
        return None
    days = sum(x["amount"] * (date.fromisoformat(x["paid"]) - date.fromisoformat(x["raised"])).days for x in recent) / total
    return {"days": max(0, round(days)), "documents": len(documents)}


def _place(parties: dict[str, dict[str, Any]], as_of: date, terms: int) -> dict[str, Any]:
    """Every open document in the week it is expected, and what could not be given a week."""
    items, by_party, credit = [], [], 0.0
    for name, p in parties.items():
        credit += p["credit"]
        open_docs = [d for d in p["documents"] if d["left"] > 0.005]
        if not open_docs:
            continue
        h = habit(p["paid"], as_of)
        days = h["days"] if h else terms
        for d in open_docs:
            expected = date.fromisoformat(d["date"]) + timedelta(days=days)
            overdue = expected <= as_of
            week = 1 if overdue else _week(expected, as_of)
            items.append({"party": name, "entry": d["entry"], "raised": d["date"], "amount": round(d["left"], 2),
                          "expected": expected.isoformat(), "overdue": overdue, "week": week if week <= WEEKS else None,
                          "basis": "history" if h else "terms", "days": days})
        by_party.append({"party": name, "open": round(sum(d["left"] for d in open_docs), 2), "documents": len(open_docs),
                         "basis": "history" if h else "terms", "days": days,
                         "settled_documents": h["documents"] if h else len({x["document"] for x in p["paid"]})})
    items.sort(key=lambda x: (x["week"] or WEEKS + 1, -x["amount"], x["party"], x["entry"]))
    by_party.sort(key=lambda x: (-x["open"], x["party"]))
    placed = [x for x in items if x["week"]]
    by_week = [round(sum(x["amount"] for x in placed if x["week"] == n), 2) for n in range(1, WEEKS + 1)]
    return {"open": round(sum(x["amount"] for x in items), 2), "by_week": by_week,
            "placed": round(sum(x["amount"] for x in placed), 2),
            "overdue": round(sum(x["amount"] for x in placed if x["overdue"]), 2),
            "later": round(sum(x["amount"] for x in items if not x["week"]), 2),
            "credit": round(credit, 2), "terms_days": terms,
            "by_history": sum(1 for x in by_party if x["basis"] == "history"),
            "by_terms": sum(1 for x in by_party if x["basis"] == "terms"),
            "parties": by_party[:MAX_PARTIES], "items": items[:MAX_ITEMS], "more_items": max(0, len(items) - MAX_ITEMS)}


def _direct_payments(led: Ledger, last: str, cash: dict[str, float]) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, float]]]:
    """Payments made straight from the bank, by the account and party they went to; and, by month, what moved through
    the bank that the open invoices and bills do not explain."""
    lines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ln in led.lines:
        if ln["date"] <= last and abs(cash.get(ln["entry"], 0.0)) > 0.005:
            lines[ln["entry"]].append(ln)
    keys: dict[tuple[str, str], dict[str, Any]] = {}
    seen: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for ref, lns in lines.items():
        net = cash[ref]
        other = [ln for ln in lns if led.role_of(ln) != "cash"]
        roles = {led.role_of(ln) for ln in other}
        day = max(ln["date"] for ln in lns)
        m = month_of(day)
        if "receivables" in roles or "payables" in roles:
            # a refund to a customer lowers what customers paid; one from a supplier lowers what suppliers were paid
            customers = "payables" not in roles or ("receivables" in roles and net > 0)
            seen[m]["customer_receipts" if customers else "supplier_payments"] += net if customers else -net
            continue
        if net > 0:
            seen[m]["other_receipts"] += net
            continue
        to = max(other, key=lambda ln: ln["debit"] - ln["credit"], default=None)
        if to is None:
            continue
        party = next(((ln.get("party") or "").strip() for ln in [to, *lns] if (ln.get("party") or "").strip()), "")
        acct = account_key(to["account_code"], to["account"])
        k = keys.setdefault((acct, party), {"account": led.names.get(acct, to["account"]), "party": party,
                                            "payments": defaultdict(list)})
        k["payments"][m].append({"date": day, "entry": ref, "amount": round(-net, 2)})
    return keys, seen


def _steady(totals: list[float]) -> bool:
    mid = median(totals)
    return mid > 0.005 and all(abs(t - mid) <= PATTERN_SPREAD * mid + 0.005 for t in totals)


def standing_payments(led: Ledger, month: str, as_of: date, cash: dict[str, float]) -> dict[str, Any] | None:
    """Payments that recur and are carried forward, with the entries each was found from; payments made every month
    for amounts too far apart to carry; and the monthly averages of what the forecast does not contain.

    None when the ledger has fewer than three months up to ``month``: too little to call anything a pattern."""
    i = led.months.index(month)
    if i < PATTERN_MONTHS - 1:
        return None
    window = led.months[i - PATTERN_MONTHS + 1: i + 1]
    horizon = as_of + timedelta(days=7 * WEEKS)
    keys, seen = _direct_payments(led, as_of.isoformat(), cash)
    found, varying, carried_keys = [], [], set()
    for key, k in keys.items():
        paid = k["payments"]
        total = {m: round(sum(x["amount"] for x in v), 2) for m, v in paid.items()}
        label = k["account"] + (f" ({k['party']})" if k["party"] else "")
        every = base = None
        if all(m in paid for m in window):
            if _steady([total[m] for m in window]):
                every, base, months = 1, window[-1], window
            else:
                varying.append({"label": label, "account": k["account"], "party": k["party"],
                                "by_month": [{"month": m, "amount": total[m]} for m in window]})
                continue
        else:
            latest = max(paid)
            li = led.months.index(latest)
            quarters = [led.months[li - 3 * n] for n in range(PATTERN_MONTHS) if li - 3 * n >= 0]
            between = [m for m in led.months[max(0, li - 3 * (PATTERN_MONTHS - 1)): i + 1] if m in paid]
            if (len(quarters) == PATTERN_MONTHS and li > i - 3 and sorted(between) == sorted(quarters)
                    and _steady([total[m] for m in quarters])):
                every, base, months = 3, latest, sorted(quarters)
        if not every:
            continue
        nxt = []
        for n in range(1, WEEKS):
            for x in paid[base]:
                d = _shift(date.fromisoformat(x["date"]), every * n)
                if as_of < d <= horizon:
                    nxt.append({"date": d.isoformat(), "amount": x["amount"], "week": _week(d, as_of)})
        carried_keys.add(key)
        found.append({"label": label, "account": k["account"], "party": k["party"],
                      "every": "month" if every == 1 else "quarter", "amount": total[base],
                      "by_month": [{"month": m, "amount": total[m]} for m in months],
                      "from": [{"date": x["date"], "entry": x["entry"], "amount": x["amount"]}
                               for m in months for x in paid[m]],
                      "next": sorted(nxt, key=lambda x: x["date"]),
                      "in_forecast": round(sum(x["amount"] for x in nxt), 2)})
    found.sort(key=lambda x: (-x["in_forecast"], x["label"]))
    varying.sort(key=lambda x: -sum(b["amount"] for b in x["by_month"]))
    other_out = sum(x["amount"] for key, k in keys.items() if key not in carried_keys
                    for m in window for x in k["payments"].get(m, []))
    n = len(window)
    return {"window": window, "found": found, "varying": varying[:20],
            "in_forecast": round(sum(x["in_forecast"] for x in found), 2),
            "recent": {"customer_receipts": round(sum(seen[m]["customer_receipts"] for m in window) / n, 2),
                       "supplier_payments": round(sum(seen[m]["supplier_payments"] for m in window) / n, 2),
                       "other_receipts": round(sum(seen[m]["other_receipts"] for m in window) / n, 2),
                       "other_payments": round(other_out / n, 2)}}


def _terms(value: Any, what: str) -> int:
    if value is None or value == "":
        return DEFAULT_TERMS
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise Invalid(f"{what} is a number of days.", code="bad_terms") from None
    if not 0 <= days <= MAX_TERMS:
        raise Invalid(f"{what} is between 0 and {MAX_TERMS} days.", code="bad_terms")
    return days


def weekly_cash(led: Ledger, month: str, customer_terms: Any = None, supplier_terms: Any = None,
                floor: Any = None) -> dict[str, Any]:
    """Thirteen weeks from the end of ``month``: receipts from open invoices, payments of open bills and standing
    payments, the balance at the end of each week, and the first week it falls below ``floor`` (zero when not given).
    """
    c_terms, s_terms = _terms(customer_terms, "Customer terms"), _terms(supplier_terms, "Supplier terms")
    try:
        low = 0.0 if floor is None or floor == "" else float(floor)
    except (TypeError, ValueError):
        raise Invalid("The floor is an amount.", code="bad_floor") from None
    if not math.isfinite(low) or low < 0:
        raise Invalid("The floor is an amount of zero or more.", code="bad_floor")
    as_of = month_end(month)
    cash = _cash_by_entry(led, as_of.isoformat())
    receipts = _place(open_items(led, "receivables", as_of, cash), as_of, c_terms)
    bills = _place(open_items(led, "payables", as_of, cash), as_of, s_terms)
    standing = standing_payments(led, month, as_of, cash)
    opening = round(led.value("cash", month) or 0.0, 2)

    missing: list[str] = []
    for kind, part, word in (("receivables", receipts, "customers"), ("payables", bills, "suppliers")):
        ledger_balance = led.value(kind, month) or 0.0
        part["ledger_balance"] = round(ledger_balance, 2)
        part["unplaced"] = round(ledger_balance - (part["open"] - part["credit"]), 2)
        if abs(part["unplaced"]) > 0.5:
            missing.append(f"{part['unplaced']:,.2f} of the balance with {word} has no document in the ledger (an "
                           "opening balance, most likely): no week could be given to it.")
    if standing is None:
        missing.append(f"Standing payments (rent, payroll, loan instalments, tax): the ledger has {led.months.index(month) + 1} "
                       f"month(s) up to {month} and {PATTERN_MONTHS} are needed to call a payment regular. The weekly "
                       "balances are withheld, because they would leave these out.")

    standing_by_week: dict[int, float] = defaultdict(float)
    for found in (standing or {}).get("found", []):
        for x in found["next"]:
            standing_by_week[x["week"]] += x["amount"]

    weeks, balance, below = [], opening, None
    for n in range(1, WEEKS + 1):
        r, b = receipts["by_week"][n - 1], bills["by_week"][n - 1]
        row: dict[str, Any] = {"week": n, "start": (as_of + timedelta(days=7 * n - 6)).isoformat(),
                               "end": (as_of + timedelta(days=7 * n)).isoformat(), "receipts": r, "bills": b}
        if standing is None:
            row |= {"standing": None, "payments": None, "opening": None, "closing": None}
        else:
            paid = round(standing_by_week[n], 2)
            row |= {"standing": paid, "payments": round(b + paid, 2), "opening": balance}
            balance = round(balance + r - b - paid, 2)
            row["closing"] = balance
            if below is None and balance < low - 0.005:
                below = {"week": n, "start": row["start"], "end": row["end"], "closing": balance}
        weeks.append(row)
    complete = standing is not None
    total_r, total_b = round(sum(w["receipts"] for w in weeks), 2), round(sum(w["bills"] for w in weeks), 2)
    total_s = round(sum(w["standing"] for w in weeks), 2) if complete else None
    return {"month": month, "as_of": as_of.isoformat(), "weeks_ahead": WEEKS, "complete": complete,
            "opening_cash": opening, "floor": round(low, 2), "weeks": weeks,
            "totals": {"receipts": total_r, "bills": total_b, "standing": total_s,
                       "payments": round(total_b + total_s, 2) if complete else None,
                       "closing": weeks[-1]["closing"]},
            "first_week_below": below, "receipts": receipts, "bills": bills, "standing": standing,
            "could_not_work_out": missing}

