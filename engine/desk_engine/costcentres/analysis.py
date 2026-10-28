"""A cost centre's position, computed from general-ledger lines: cost and revenue by rule, how much was tagged in
the ledger, the accrual cost against the cash that left the bank, and when the budget runs out at the current pace.

Everything is arithmetic in code on the ledger lines; every figure can be traced back to the lines that make it up
(see ``matched_lines``). The methods are written into the result so the page and the assistant can state them.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from ..connectors.accounting import COST_CLASSES, INCOME_CLASSES
from .rules import Matcher, account_matches, norm

CASH_METHOD = ("Paid directly: entries that charge this cost centre and pay from a bank account. Through payables: "
               "supplier bills that charge this cost centre, settled oldest first by each supplier's later payments "
               "(bank) or other settlements (credit notes, offsets). Not cash: accruals, depreciation and other "
               "entries with neither a bank nor a payables line.")


def r2(x: float) -> float:
    v = round(x + 0.0, 2)
    return 0.0 if v == 0 else v


def month_key(d: str) -> str:
    return d[:7]


def months_between(a: date, b: date) -> list[str]:
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def is_cost(cls: str, include_assets: bool) -> bool:
    return cls in COST_CLASSES or (include_assets and cls == "Asset")


def net_cost(line: dict[str, Any]) -> float:
    return float(line["debit"] or 0) - float(line["credit"] or 0)


class Analysis:
    def __init__(self, lines: list[dict[str, Any]], cc: dict[str, Any], *, as_of: date,
                 budget_lines: list[dict[str, Any]] | None = None,
                 others: list[tuple[str, Matcher, bool]] | None = None) -> None:
        self.cc = cc
        self.as_of = as_of
        self.matcher = Matcher(cc["rules"])
        self.include_assets = bool(cc.get("include_assets"))
        self.start = cc.get("start_date") or None
        self.budget_lines = budget_lines or []
        self.others = others or []
        top = as_of.isoformat()
        self.lines = [ln for ln in lines if ln["date"] and ln["date"] <= top and (not self.start or ln["date"] >= self.start)]
        self.entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for ln in self.lines:
            self.entries[ln["entry"]].append(ln)
        # match once
        self.matched: list[tuple[dict[str, Any], float, int | None, bool]] = []
        for ln in self.lines:
            share, rule, tagged = self.matcher.match(ln)
            if share:
                self.matched.append((ln, share, rule, tagged))
        self.cost_lines = [m for m in self.matched if is_cost(m[0]["account_class"], self.include_assets)]
        self.income_lines = [m for m in self.matched if m[0]["account_class"] in INCOME_CLASSES]

    # -- 1. rules and tagging --------------------------------------------------------------------------------------
    def totals(self) -> dict[str, Any]:
        cost = sum(net_cost(ln) * s for ln, s, _, _ in self.cost_lines)
        revenue = sum(-net_cost(ln) * s for ln, s, _, _ in self.income_lines)
        tagged = sum(net_cost(ln) * s for ln, s, _, t in self.cost_lines if t)
        budget = self.budget_total()
        out = {"cost": r2(cost), "revenue": r2(revenue), "margin": r2(revenue - cost), "budget": budget,
               "remaining": r2(budget - cost) if budget is not None else None,
               "used_pct": round(cost / budget * 100, 1) if budget else None,
               "matched_lines": len(self.matched), "tagged_cost": r2(tagged), "rule_cost": r2(cost - tagged),
               "untagged_share": round((cost - tagged) / cost * 100, 1) if cost else None}
        by_rule: dict[int, float] = defaultdict(float)
        for ln, s, rule, _ in self.cost_lines:
            by_rule[rule or 0] += net_cost(ln) * s
        out["by_rule"] = [{"rule": i, "describe": c.describe(), "cost": r2(by_rule.get(i, 0.0))}
                          for i, c in enumerate(self.matcher.rules.include)]
        return out

    def parties(self) -> set[str]:
        return {ln["_party"] for ln, _, _, _ in self.cost_lines if ln["_party"]}

    def _words(self) -> list[str]:
        words = [k for c in self.matcher.rules.include for k in (norm(x) for x in c.keywords) if len(k) >= 3]
        name = norm(self.cc.get("name"))
        if len(name) >= 4:
            words.append(name)
        code = norm(self.cc.get("code"))
        if len(code) >= 3:
            words.append(code)
        return words

    def _taken_elsewhere(self, ln: dict[str, Any]) -> list[tuple[str, float]]:
        out = []
        for name, m, _ in self.others:
            share = m.match(ln)[0]
            if share:
                out.append((name, share))
        return out

    def possibly_missing(self, limit: int = 25) -> dict[str, Any]:
        """Cost lines no cost centre took, from this cost centre's suppliers or naming it in the description."""
        parties, words = self.parties(), self._words()
        taken = {id(m[0]) for m in self.matched}
        found = []
        for ln in self.lines:
            if id(ln) in taken or not is_cost(ln["account_class"], self.include_assets) or not net_cost(ln):
                continue
            reason = None
            if ln["_party"] and ln["_party"] in parties:
                reason = "same supplier"
            elif any(w in ln["_desc"] for w in words):
                reason = "description"
            if reason and not self._taken_elsewhere(ln):
                found.append((ln, reason))
        found.sort(key=lambda x: -abs(net_cost(x[0])))
        return {"count": len(found), "amount": r2(sum(net_cost(x[0]) for x in found)),
                "lines": [public_line(ln) | {"reason": reason} for ln, reason in found[:limit]]}

    def overlaps(self) -> dict[str, Any]:
        """Cost counted more than once across cost centres: lines whose shares add up to more than the whole line
        (a line split 60/40 between two projects is not an overlap)."""
        names: dict[str, float] = defaultdict(float)
        count, amount = 0, 0.0
        for ln, s, _, _ in self.cost_lines:
            hits = self._taken_elsewhere(ln)
            if hits and s + sum(x for _, x in hits) > 1.0001:
                count += 1
                amount += net_cost(ln) * s
                for h, _ in hits:
                    names[h] += net_cost(ln) * s
        return {"count": count, "amount": r2(amount),
                "with": [{"name": n, "amount": r2(a)} for n, a in sorted(names.items(), key=lambda x: -abs(x[1]))]}

    # -- 3. accrual against cash -----------------------------------------------------------------------------------
    def cash(self) -> dict[str, Any]:
        share: dict[int, float] = {id(ln): s for ln, s, _, _ in self.cost_lines}
        months: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "cash": 0.0})
        paid_direct = non_cash = 0.0
        bills: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        settlements: list[tuple[str, str | None, float, bool]] = []
        party_missing = 0.0
        for entry, lines in self.entries.items():
            m = sum(net_cost(ln) * share[id(ln)] for ln in lines if id(ln) in share)
            day = min(ln["date"] for ln in lines)
            bank_out = sum(net_cost(ln) for ln in lines if ln["account_class"] == "Bank")  # negative = money out
            payable: dict[str | None, float] = defaultdict(float)
            for ln in lines:
                if ln["account_class"] == "Payable":
                    payable[ln["_party"] or None] += float(ln["credit"] or 0) - float(ln["debit"] or 0)
            raised = {p: v for p, v in payable.items() if v > 0.005}
            settled = {p: -v for p, v in payable.items() if v < -0.005}
            if m:
                months[month_key(day)]["cost"] += m
            if raised:
                total = sum(raised.values())
                for p, v in raised.items():
                    bills[p].append({"date": day, "entry": entry, "amount": v, "open": v, "project": m * v / total})
                    if p is None and m:
                        party_missing += m * v / total
            elif m and bank_out < -0.005:
                paid_direct += m
                months[month_key(day)]["cash"] += m
            elif m:
                non_cash += m
            for p, v in settled.items():
                settlements.append((day, p, v, bank_out < -0.005))
        paid_via = other_settled = 0.0
        for day, p, amount, is_cash in sorted(settlements, key=lambda s: s[0]):
            left = amount
            for b in sorted(bills.get(p, []), key=lambda b: (b["date"], b["entry"])):
                if left <= 0.005:
                    break
                if b["open"] <= 0.005 or b["date"] > day:
                    continue
                take = min(left, b["open"])
                b["open"] -= take
                left -= take
                part = b["project"] * take / b["amount"]
                if is_cash:
                    paid_via += part
                    months[month_key(day)]["cash"] += part
                else:
                    other_settled += part
        via = sum(b["project"] for bs in bills.values() for b in bs)
        unpaid = via - paid_via - other_settled
        accrual = paid_direct + via + non_cash
        return {"accrual_cost": r2(accrual), "paid_direct": r2(paid_direct), "through_payables": r2(via),
                "paid_through_payables": r2(paid_via), "settled_other": r2(other_settled), "unpaid": r2(unpaid),
                "not_cash": r2(non_cash), "cash_paid": r2(paid_direct + paid_via),
                "gap": r2(accrual - paid_direct - paid_via),
                "supplier_missing": r2(party_missing),
                "method": CASH_METHOD + (" Some payables lines carry no supplier, so those bills were settled in date "
                                         "order across all suppliers." if party_missing else ""),
                "_months": months}

    def unassigned_bank(self, limit: int = 25) -> dict[str, Any]:
        """Money out of the bank whose entry charges no cost centre and pays no supplier bill, from this cost
        centre's suppliers or naming it: candidates that may belong here."""
        parties, words = self.parties(), self._words()
        found = []
        for entry, lines in self.entries.items():
            if any(ln["account_class"] == "Payable" for ln in lines):
                continue
            bank = [ln for ln in lines if ln["account_class"] == "Bank" and net_cost(ln) < 0]
            if not bank:
                continue
            others = [ln for ln in lines if ln["account_class"] != "Bank"]
            if any(self.matcher.match(ln)[0] or self._taken_elsewhere(ln) for ln in others):
                continue
            hit = None
            for ln in lines:
                if ln["_party"] and ln["_party"] in parties:
                    hit = "same supplier"
                elif any(w in ln["_desc"] for w in words):
                    hit = hit or "description"
            if hit:
                amount = -sum(net_cost(ln) for ln in bank)
                found.append({"date": min(ln["date"] for ln in lines), "entry": entry, "amount": r2(amount),
                              "party": next((ln["party"] for ln in lines if ln.get("party")), None),
                              "description": next((ln["description"] for ln in lines if ln.get("description")), None),
                              "accounts": sorted({ln["account"] for ln in others}), "reason": hit})
        found.sort(key=lambda x: -x["amount"])
        return {"count": len(found), "amount": r2(sum(f["amount"] for f in found)), "lines": found[:limit]}

    # -- 6. when the budget runs out -------------------------------------------------------------------------------
    def budget_total(self) -> float | None:
        if self.budget_lines:
            return r2(sum(float(b["amount"] or 0) for b in self.budget_lines))
        v = self.cc.get("budget_amount")
        return r2(float(v)) if v is not None else None

    def daily_costs(self, pick: Any = None) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for ln, s, _, _ in self.cost_lines:
            if pick is None or pick(ln):
                out[ln["date"]] += net_cost(ln) * s
        return out

    def runout(self, budget: float | None, daily: dict[str, float]) -> dict[str, Any]:
        n = int(self.cc.get("burn_months") or 3)
        spent = sum(daily.values())
        first = min(daily) if daily else None
        start_bound = self.start or first
        window_start = add_months(self.as_of, -n) + timedelta(days=1)
        if start_bound and window_start.isoformat() < start_bound:
            window_start = date.fromisoformat(start_bound)
        days = (self.as_of - window_start).days + 1
        in_window = sum(v for d, v in daily.items() if window_start.isoformat() <= d <= self.as_of.isoformat())
        per_day = in_window / days if days > 0 else 0.0
        end = date.fromisoformat(self.cc["end_date"]) if self.cc.get("end_date") else None
        out: dict[str, Any] = {
            "budget": budget, "spent": r2(spent), "remaining": r2(budget - spent) if budget is not None else None,
            "window_start": window_start.isoformat(), "window_end": self.as_of.isoformat(), "window_days": days,
            "spent_in_window": r2(in_window), "per_day": r2(per_day), "per_month": r2(per_day * 365.25 / 12),
            "run_out_date": None, "exceeded_on": None, "projected_at_end": None, "over_at_end": None,
            "days_before_end": None, "end_date": end.isoformat() if end else None,
            "method": f"Average daily cost over the last {n} month{'s' if n != 1 else ''} up to {self.as_of} "
                      f"({days} days, {r2(in_window):,.2f}), applied to what is left of the budget."}
        if end and end > self.as_of:
            proj = spent + per_day * (end - self.as_of).days
            out["projected_at_end"] = r2(proj)
            if budget is not None:
                out["over_at_end"] = r2(proj - budget)
        if budget is None:
            out["status"] = "no_budget"
            return out
        if spent >= budget and budget > 0 or (budget <= 0 and spent > 0):
            run = 0.0
            for d in sorted(daily):
                run += daily[d]
                if run >= budget:
                    out["exceeded_on"] = d
                    break
            out["status"] = "exceeded"
            return out
        if days < 14:
            out["status"] = "too_early"
            return out
        if per_day <= 0.005:
            out["status"] = "no_spend"
            return out
        run_out = self.as_of + timedelta(days=math.ceil((budget - spent) / per_day))
        out["run_out_date"] = run_out.isoformat()
        if end:
            out["days_before_end"] = (end - run_out).days
            out["status"] = "before_end" if run_out < end else "after_end"
        else:
            out["status"] = "open_ended"
        return out

    def budget_report(self) -> dict[str, Any]:
        lines_out = []
        mapped_ids: set[int] = set()
        for b in self.budget_lines:
            accounts = [a for a in (b.get("accounts") or []) if a]
            if accounts:
                def pick(ln: dict[str, Any], accounts: list[str] = accounts) -> bool:
                    return any(account_matches(a, ln["account_code"], ln["account"]) for a in accounts)
                daily = self.daily_costs(pick)
                for ln, _, _, _ in self.cost_lines:
                    if pick(ln):
                        mapped_ids.add(id(ln))
                ro = self.runout(float(b["amount"] or 0), daily)
                actual = ro["spent"]
            else:
                ro, actual = None, None
            lines_out.append({"id": b.get("id"), "label": b["label"], "amount": r2(float(b["amount"] or 0)),
                              "accounts": accounts, "source": b.get("source") or {}, "actual": actual,
                              "remaining": r2(float(b["amount"] or 0) - actual) if actual is not None else None,
                              "used_pct": round(actual / float(b["amount"]) * 100, 1)
                              if actual is not None and b["amount"] else None,
                              "run_out_date": ro["run_out_date"] if ro else None,
                              "exceeded_on": ro["exceeded_on"] if ro else None, "status": ro["status"] if ro else
                              "unmapped"})
        outside = sum(net_cost(ln) * s for ln, s, _, _ in self.cost_lines if id(ln) not in mapped_ids)
        return {"lines": lines_out, "unbudgeted_cost": r2(outside) if any(b["accounts"] for b in lines_out) else None,
                "unmapped": sum(1 for b in lines_out if not b["accounts"])}

    # -- everything -------------------------------------------------------------------------------------------------
    def report(self) -> dict[str, Any]:
        totals = self.totals()
        cash = self.cash()
        months_data = cash.pop("_months")
        first = self.start or (min((ln["date"] for ln, _, _, _ in self.cost_lines), default=None))
        series = []
        if first:
            cum_cost = cum_cash = 0.0
            for mk in months_between(date.fromisoformat(first), self.as_of):
                v = months_data.get(mk, {"cost": 0.0, "cash": 0.0})
                cum_cost += v["cost"]
                cum_cash += v["cash"]
                series.append({"month": mk, "cost": r2(v["cost"]), "cash": r2(v["cash"]),
                               "cumulative_cost": r2(cum_cost), "cumulative_cash": r2(cum_cash)})
        return {"as_of": self.as_of.isoformat(), "totals": totals, "cash": cash, "monthly": series,
                "runout": self.runout(totals["budget"], self.daily_costs()), "budget": self.budget_report(),
                "possibly_missing": self.possibly_missing(), "unassigned_bank": self.unassigned_bank(),
                "overlaps": self.overlaps()}

    def matched_lines(self) -> list[dict[str, Any]]:
        out = []
        for ln, s, rule, tagged in sorted(self.matched, key=lambda m: (m[0]["date"], m[0]["entry"])):
            sign = 1 if is_cost(ln["account_class"], self.include_assets) else \
                -1 if ln["account_class"] in INCOME_CLASSES else 0
            out.append(public_line(ln) | {"share": round(s, 4), "rule": rule, "basis": "tag" if tagged else "rule",
                                          "counts_as": "cost" if sign == 1 else "revenue" if sign == -1 else "other",
                                          "amount": r2(net_cost(ln) * s * sign) if sign else None})
        return out


def public_line(ln: dict[str, Any]) -> dict[str, Any]:
    return {k: ln.get(k) for k in ("row", "date", "entry", "type", "account_code", "account", "account_class",
                                   "debit", "credit", "description", "party", "tags")}
