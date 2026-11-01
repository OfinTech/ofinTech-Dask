"""The CFO's pages, read from the same ledger as the KPIs: monthly financial statements, budget against actual by
line, receivables and payables by party with ageing, and anomalies in what was posted.

Everything is derived from ``Ledger`` (analysis/ledger.py) so the statements agree with the scorecard and the
executive overview to the cent. Nothing here changes data.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from itertools import pairwise
from statistics import mean, pstdev
from typing import TYPE_CHECKING, Any

from ..errors import Invalid
from .ledger import Ledger
from .overview import PL_LINES, fiscal_start
from .roles import PL_ROLES, account_key

if TYPE_CHECKING:
    from ..app import Engine

BS_SECTIONS: list[tuple[str, list[str]]] = [
    ("current_assets", ["cash", "receivables", "inventory", "other_current_assets"]),
    ("non_current_assets", ["fixed_assets", "other_non_current_assets"]),
    ("current_liabilities", ["payables", "other_current_liabilities", "short_term_debt"]),
    ("non_current_liabilities", ["long_term_debt", "other_non_current_liabilities"]),
]
AGE_BUCKETS = [(0, 30), (31, 60), (61, 90), (91, None)]


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _variance(actual: float, planned: float | None, cost: bool) -> dict[str, Any]:
    """Actual less plan; favourable when a cost is under plan or income is over it."""
    if planned is None:
        return {"amount": None, "pct": None, "favourable": None}
    d = actual - planned
    return {"amount": _r(d), "pct": round(d / abs(planned), 4) if abs(planned) > 0.005 else None,
            "favourable": (d <= 0) if cost else (d >= 0)}


def month_end(m: str) -> date:
    y, mo = int(m[:4]), int(m[5:7])
    nxt = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
    return nxt - timedelta(days=1)


class Statements:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _load(self, ws_id: str, month: str | None) -> tuple[Ledger, dict[str, Any], int]:
        ev, meta = self.engine.analysis.evaluator(ws_id)
        months = ev.ledger.months
        if not months:
            raise Invalid("The general ledger has no dated lines yet.", code="no_ledger")
        i = months.index(month) if month in months else len(months) - 1
        return ev.ledger, meta, i

    # -- statements -------------------------------------------------------------------------------------------------------
    def statements(self, ws_id: str, workspace: dict[str, Any], month: str | None = None, periods: int = 12) -> dict[str, Any]:
        led, meta, i = self._load(ws_id, month)
        months = led.months
        cols = months[max(0, i - periods + 1): i + 1]
        m = months[i]
        fy_first = fiscal_start(m, int(workspace.get("fiscal_year_start_month") or 1))
        ytd = [x for x in months[: i + 1] if x >= fy_first]
        plan = self.engine.overview._plan_lines(ws_id, meta)

        def flow(name: str, x: str) -> float:
            return led.value(name, x) or 0.0

        pl = []
        for name, kind, drill in PL_LINES:
            pl.append({"measure": name, "kind": kind, "drillable": drill, "values": [_r(flow(name, x)) for x in cols],
                       "ytd": _r(sum(flow(name, x) for x in ytd)),
                       "plan": [_r(plan[name].get(x)) if name in plan else None for x in cols] if name in plan else None,
                       "accounts": len(led.by_account.get(name, {}).get(m, {})) if drill else 0})
        rev = [flow("revenue", x) for x in cols]
        margins = {name: [round(flow(name, x) / r, 4) if r else None for x, r in zip(cols, rev, strict=False)]
                   for name, kind, _ in PL_LINES if kind in ("subtotal", "total")}

        def stock(name: str, x: str) -> float:
            return led.value(name, x) or 0.0

        bs = []
        for section, roles in BS_SECTIONS:
            rows = [{"measure": r, "values": [_r(stock(r, x)) for x in cols],
                     "accounts": len(led.by_account.get(r, {}).get(m, {}))} for r in roles]
            bs.append({"section": section, "rows": rows, "total": [_r(sum(stock(r, x) for r in roles)) for x in cols]})
        totals = {k: [_r(stock(k, x)) for x in cols] for k in ("total_assets", "total_liabilities", "equity", "working_capital", "net_debt")}

        cf = []
        first = months.index(cols[0])
        prev_cash: float | None = stock("cash", months[first - 1]) if first > 0 else None
        for x in cols:
            ocf, icf, fin = flow("ocf", x), flow("icf", x), flow("fcf_fin", x)
            closing = stock("cash", x)
            opening = prev_cash if prev_cash is not None else closing - (ocf + icf + fin)
            cf.append({"month": x, "operating": _r(ocf), "investing": _r(icf), "financing": _r(fin), "net": _r(ocf + icf + fin),
                       "opening": _r(opening), "closing": _r(closing), "unexplained": _r(closing - opening - (ocf + icf + fin)),
                       "cash_in": _r(flow("cash_in", x)), "cash_out": _r(flow("cash_out", x)), "free": _r(ocf + icf)})
            prev_cash = closing
        return {"month": m, "months": months, "columns": cols, "fiscal_year_start": fy_first, "currency": workspace.get("base_currency"),
                "provenance": self.engine.overview._provenance(meta), "plan": meta.get("plan"),
                "profit_and_loss": {"lines": pl, "margins": margins}, "balance_sheet": {"sections": bs, "totals": totals},
                "cash_flow": cf, "ledger": meta["ledger"]}

    # -- budget against actual --------------------------------------------------------------------------------------------
    def budget(self, ws_id: str, workspace: dict[str, Any], month: str | None = None) -> dict[str, Any]:
        led, meta, i = self._load(ws_id, month)
        months = led.months
        m = months[i]
        fy_first = fiscal_start(m, int(workspace.get("fiscal_year_start_month") or 1))
        ytd = [x for x in months[: i + 1] if x >= fy_first]
        plan = self.engine.overview._plan_lines(ws_id, meta)
        lines = []
        for name, kind, drill in PL_LINES:
            actual = led.value(name, m) or 0.0
            actual_ytd = sum(led.value(name, x) or 0.0 for x in ytd)
            p = plan.get(name)
            planned = p.get(m) if p else None
            planned_ytd = sum(p.get(x, 0.0) for x in ytd if x in p) if p else None
            lines.append({"measure": name, "kind": kind, "drillable": drill, "actual": _r(actual), "plan": _r(planned),
                          "variance": _variance(actual, planned, kind == "cost"), "actual_ytd": _r(actual_ytd),
                          "plan_ytd": _r(planned_ytd), "variance_ytd": _variance(actual_ytd, planned_ytd, kind == "cost")})
        # the accounts behind the largest unfavourable cost variances this month, so the CFO sees where
        drivers = []
        for ln in lines:
            if ln["kind"] == "cost" and ln["variance"]["amount"] and not ln["variance"]["favourable"]:
                prev = months[i - 1] if i >= 1 else None
                accts = led.by_account.get(ln["measure"], {}).get(m, {})
                before = led.by_account.get(ln["measure"], {}).get(prev, {}) if prev else {}
                top = sorted(accts.items(), key=lambda kv: -(kv[1] - before.get(kv[0], 0.0)))[:3]
                drivers.append({"measure": ln["measure"], "over_by": ln["variance"]["amount"],
                                "accounts": [{"key": k, "account": led.names.get(k, k), "value": _r(v), "previous": _r(before.get(k))}
                                             for k, v in top]})
        return {"month": m, "months": months, "fiscal_year_start": fy_first, "currency": workspace.get("base_currency"),
                "provenance": self.engine.overview._provenance(meta), "plan": meta.get("plan"), "has_plan": bool(plan),
                "lines": lines, "drivers": sorted(drivers, key=lambda d: -(d["over_by"] or 0))[:4]}

    # -- receivables and payables by party, with ageing --------------------------------------------------------------------
    def parties(self, ws_id: str, workspace: dict[str, Any], kind: str, month: str | None = None, limit: int = 60) -> dict[str, Any]:
        if kind not in ("receivables", "payables"):
            raise Invalid("kind is receivables or payables.")
        led, meta, i = self._load(ws_id, month)
        months = led.months
        m = months[i]
        end = month_end(m)
        end_s = end.isoformat()
        start_m = m
        # open items per party: FIFO allocation of settlements against the documents that raised the balance
        docs: dict[str, list[list[Any]]] = defaultdict(list)      # party -> [[date, entry, remaining]]
        settled: dict[str, float] = defaultdict(float)             # party -> settlements not matched to a document
        month_raised: dict[str, float] = defaultdict(float)
        month_settled: dict[str, float] = defaultdict(float)
        opening: dict[str, float] = defaultdict(float)
        for ln in sorted(led.lines, key=lambda x: (x["date"], x.get("row") or 0)):
            if led.role_of(ln) != kind or ln["date"] > end_s:
                continue
            party = (ln.get("party") or "").strip() or "—"
            raised = ln["debit"] if kind == "receivables" else ln["credit"]
            paid = ln["credit"] if kind == "receivables" else ln["debit"]
            if ln["date"] < start_m:
                opening[party] += raised - paid
            else:
                month_raised[party] += raised
                month_settled[party] += paid
            if raised > 0:
                docs[party].append([ln["date"], ln["entry"], raised])
            if paid > 0:
                left = paid
                for d in docs[party]:
                    if left <= 0.005:
                        break
                    take = min(d[2], left)
                    d[2] -= take
                    left -= take
                if left > 0.005:
                    settled[party] += left
        rows = []
        for party in set(docs) | set(settled) | set(opening):
            open_docs = [d for d in docs.get(party, []) if d[2] > 0.005]
            buckets = [0.0, 0.0, 0.0, 0.0]
            oldest = None
            for d in open_docs:
                age = (end - date.fromisoformat(d[0])).days
                idx = next(n for n, (lo, hi) in enumerate(AGE_BUCKETS) if hi is None or age <= hi)
                buckets[idx] += d[2]
                oldest = d[0] if oldest is None or d[0] < oldest else oldest
            closing = sum(buckets) - settled.get(party, 0.0)
            if abs(closing) < 0.005 and month_raised[party] == 0 and month_settled[party] == 0:
                continue
            rows.append({"party": party, "opening": _r(opening[party]), "raised": _r(month_raised[party]),
                         "settled": _r(month_settled[party]), "closing": _r(closing),
                         "buckets": [_r(b) for b in buckets], "unapplied": _r(settled.get(party, 0.0)),
                         "overdue_30": _r(sum(buckets[1:])), "oldest": oldest, "open_documents": len(open_docs)})
        rows.sort(key=lambda r: -abs(r["closing"] or 0))
        total = sum(r["closing"] or 0 for r in rows)
        totals = {"closing": _r(total), "buckets": [_r(sum(r["buckets"][n] or 0 for r in rows)) for n in range(4)],
                  "overdue_30": _r(sum(r["overdue_30"] or 0 for r in rows)), "unapplied": _r(sum(r["unapplied"] or 0 for r in rows)),
                  "raised": _r(sum(r["raised"] or 0 for r in rows)), "settled": _r(sum(r["settled"] or 0 for r in rows))}
        ledger_balance = led.value(kind, m) or 0.0
        return {"month": m, "months": months, "kind": kind, "currency": workspace.get("base_currency"),
                "provenance": self.engine.overview._provenance(meta), "as_of": end_s,
                "ledger_balance": _r(ledger_balance), "difference": _r(ledger_balance - total),
                "with_party": sum(1 for r in rows if r["party"] != "—"), "rows": rows[:limit], "count": len(rows), "totals": totals,
                "buckets": [{"from": lo, "to": hi} for lo, hi in AGE_BUCKETS]}

    # -- anomalies ----------------------------------------------------------------------------------------------------------
    def anomalies(self, ws_id: str, workspace: dict[str, Any], month: str | None = None) -> dict[str, Any]:
        led, meta, i = self._load(ws_id, month)
        months = led.months
        m = months[i]
        rev = [led.value("revenue", x) or 0.0 for x in months[max(0, i - 2): i + 1]]
        materiality = max(500.0, 0.01 * (sum(rev) / len(rev) if rev else 0.0))
        out: list[dict[str, Any]] = []
        history = months[max(0, i - 12): i]
        for role in PL_ROLES:
            keys = {k for x in months[: i + 1] for k in led.by_account.get(role, {}).get(x, {})}
            for key in keys:
                series = [led.by_account[role].get(x, {}).get(key, 0.0) for x in history]
                now = led.by_account[role].get(m, {}).get(key, 0.0)
                posted_before = [v for v in series if abs(v) > 0.005]
                name = led.names.get(key, key)
                if not posted_before and abs(now) >= materiality:
                    out.append({"kind": "new_account", "measure": role, "key": key, "account": name, "value": _r(now), "level": "medium"})
                    continue
                if len(posted_before) >= 4:
                    recent = series[-3:]
                    if len(recent) == 3 and all(abs(v) > 0.005 for v in recent) and abs(now) < 0.005 and abs(mean(recent)) >= materiality:
                        out.append({"kind": "missing_posting", "measure": role, "key": key, "account": name, "average": _r(mean(recent)),
                                    "level": "medium"})
                        continue
                    mu, sd = mean(series[-6:]), pstdev(series[-6:])
                    diff = now - mu
                    if abs(diff) >= materiality and (sd == 0 or abs(diff) / sd >= 2.5) and abs(diff) >= 0.3 * max(abs(mu), 1):
                        out.append({"kind": "unusual_amount", "measure": role, "key": key, "account": name, "value": _r(now),
                                    "average": _r(mu), "change": round(diff / abs(mu), 4) if abs(mu) > 0.005 else None,
                                    "level": "high" if abs(diff) >= 3 * materiality else "medium"})
        # possible duplicates: same account, party and amount within seven days, in different entries
        seen: dict[tuple[str, str, float], list[dict[str, Any]]] = defaultdict(list)
        for ln in led.lines:
            if ln["date"][:7] != m:
                continue
            role = led.role_of(ln)
            if role not in PL_ROLES:
                continue
            amt = round(abs(ln["debit"] - ln["credit"]), 2)
            if amt < materiality / 2:
                continue
            key = (account_key(ln["account_code"], ln["account"]), (ln.get("party") or "").strip().lower(), amt)
            seen[key].append(ln)
        for (akey, party, amt), lines in seen.items():
            lines.sort(key=lambda x: x["date"])
            for a, b in pairwise(lines):
                if a["entry"] != b["entry"] and (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days <= 7:
                    out.append({"kind": "possible_duplicate", "measure": led.role_of(a), "key": akey, "account": led.names.get(akey, akey),
                                "value": _r(amt), "party": party or None, "entries": [a["entry"], b["entry"]],
                                "dates": [a["date"], b["date"]], "level": "high"})
        order = {"high": 0, "medium": 1}
        out.sort(key=lambda a: (order[a["level"]], -abs(a.get("value") or a.get("average") or 0)))
        return {"month": m, "months": months, "currency": workspace.get("base_currency"), "materiality": _r(materiality),
                "provenance": self.engine.overview._provenance(meta), "items": out[:40], "count": len(out),
                "history_months": len(history)}
