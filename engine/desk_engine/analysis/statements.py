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

    # -- bank reconciliation ----------------------------------------------------------------------------------------------
    def bank_reconciliation(self, ws_id: str, workspace: dict[str, Any], statement: str | None = None,
                            month: str | None = None, account: str | None = None,
                            columns: dict[str, Any] | None = None) -> dict[str, Any]:
        """One month of a bank statement dataset against the ledger's bank and cash lines (analysis/reconcile.py).
        Reads only."""
        from ..costcentres.service import is_ledger
        from ..data.store import find_dataset, list_datasets, table_name
        from .reconcile import MAX_ROWS, detect_columns, reconcile, statement_balances, statement_lines

        if statement:
            ds = find_dataset(self.engine, ws_id, statement)
            if is_ledger(ds):
                raise Invalid(f"“{ds['name']}” is a general ledger, not a bank statement. Name the dataset that holds "
                              "the bank's statement.", code="not_a_statement")
            cols = detect_columns(ds["columns"], columns)
        else:
            found = []
            for d in list_datasets(self.engine, ws_id):
                if is_ledger(d):
                    continue
                try:
                    found.append((d, detect_columns(d["columns"], columns)))
                except Invalid:
                    continue
            if not found:
                raise Invalid("No dataset in this workspace reads as a bank statement (a date column, and money in "
                              "and money out or one amount column). Import the bank's statement under Data, then "
                              "ask again.", code="no_statement")
            if len(found) > 1:
                raise Invalid("More than one dataset reads as a bank statement: "
                              + ", ".join(f"“{d['name']}”" for d, _ in found[:8]) + ". Say which one with statement.",
                              code="which_statement")
            ds, cols = found[0]
        keys = ", ".join(c["key"] for c in ds["columns"])
        rows = [list(r) for r in self.engine.db.conn.execute(
            f"SELECT {keys} FROM {table_name(ds['id'])} ORDER BY _row LIMIT ?", (MAX_ROWS,)).fetchall()]
        lines, unread = statement_lines(rows, cols)
        if not lines:
            raise Invalid(f"The statement “{ds['name']}” has no row with a date that can be read.", code="no_statement")
        covered = sorted({x["date"][:7] for x in lines})
        m = month or covered[-1]
        if m not in covered:
            raise Invalid(f"The statement “{ds['name']}” has no rows in {m}: it covers {covered[0]} to {covered[-1]}.",
                          code="month_not_covered")
        led, meta, _ = self._load(ws_id, m)
        if m not in led.months:
            raise Invalid(f"The general ledger has no lines in {m}: it covers {led.months[0]} to {led.months[-1]}.",
                          code="month_not_covered")
        cash: dict[str, str] = {}
        for ln in led.lines:
            if led.role_of(ln) == "cash":
                cash.setdefault(account_key(ln["account_code"], ln["account"]),
                                f"{ln['account_code']} {ln['account']}".strip() if ln["account_code"] else ln["account"])
        if account:
            want = account.strip().lower()
            chosen = {k: n for k, n in cash.items() if want == k.split("|")[0].lower() or want in n.lower()}
            if not chosen:
                raise Invalid(f"No bank or cash account in the ledger matches “{account}”. The ledger's bank and cash "
                              "accounts are: " + ", ".join(sorted(cash.values())) + ".", code="no_such_account")
        else:
            chosen = cash
        if not chosen:
            raise Invalid("The general ledger has no account with the bank and cash role. Finance sets account roles "
                          "under Financial analysis.", code="no_cash_account")
        book = []
        for ln in led.lines:
            if ln["date"][:7] != m or account_key(ln["account_code"], ln["account"]) not in chosen:
                continue
            net = ln["debit"] - ln["credit"]
            book.append({"date": ln["date"], "entry": ln["entry"], "in": round(max(net, 0.0), 2),
                         "out": round(max(-net, 0.0), 2),
                         "text": " · ".join(x for x in (ln.get("description"), ln.get("party")) if x)})
        i = led.months.index(m)
        closing = sum(led.balances.get(k, {}).get(m, 0.0) for k in chosen)
        movement = sum(x["in"] - x["out"] for x in book)
        opening = (sum(led.balances.get(k, {}).get(led.months[i - 1], 0.0) for k in chosen) if i > 0
                   else closing - movement)
        s_open, s_close, s_date = statement_balances(lines, m)
        in_month = [x for x in lines if x["date"][:7] == m]
        r = reconcile(in_month, book, statement_opening=s_open, statement_closing=s_close,
                      book_opening=_r(opening), book_closing=_r(closing))
        return r | {"month": m, "months": led.months, "as_of": month_end(m).isoformat(), "statement_date": s_date,
                    "statement": {"id": ds["id"], "name": ds["name"], "rows": len(rows), "unread_rows": unread,
                                  "truncated": len(rows) >= MAX_ROWS,
                                  "columns": {k: ds["columns"][v]["name"] for k, v in cols.items()}},
                    "accounts": sorted(chosen.values()), "other_cash_accounts": sorted(set(cash.values()) - set(chosen.values())),
                    "currency": workspace.get("base_currency"), "provenance": self.engine.overview._provenance(meta),
                    "ledger": meta["ledger"]}

    # -- an account's lines, whole entries, and what moved a month's profit (analysis/activity.py) -------------------------
    def _span(self, led: Ledger, first: str | None, last: str | None) -> tuple[str, str]:
        """The months asked for, inside what the ledger covers: all of it when neither is given, one month when
        only one is."""
        lo, hi = first or last or led.months[0], last or first or led.months[-1]
        if lo > hi:
            raise Invalid(f"The period runs backwards: {lo} is after {hi}.", code="bad_period")
        for m in (lo, hi):
            if m not in led.months:
                raise Invalid(f"No ledger data for {m}: the ledger covers {led.months[0]} to {led.months[-1]}.",
                              code="month_not_covered")
        return lo, hi

    def _about(self, workspace: dict[str, Any], led: Ledger, meta: dict[str, Any]) -> dict[str, Any]:
        return {"months": led.months, "currency": workspace.get("base_currency"),
                "provenance": self.engine.overview._provenance(meta), "ledger": meta["ledger"]}

    def account_activity(self, ws_id: str, workspace: dict[str, Any], account: str, first: str | None = None,
                         last: str | None = None, party: str | None = None) -> dict[str, Any]:
        from .activity import account_activity, chart, match_accounts

        led, meta, _ = self._load(ws_id, None)
        lo, hi = self._span(led, first, last)
        accounts = chart(led)
        keys = match_accounts(accounts, account)
        listed = sorted(accounts.values(), key=lambda a: (a["code"], a["name"]))
        return self._about(workspace, led, meta) | {
            "asked": account, "first": lo, "last": hi, "chart": listed if len(keys) != 1 else [],
            "matches": [accounts[k] for k in keys],
            "accounts": [account_activity(led, k, lo, hi, party) for k in keys[:4]]}

    def ledger_entries(self, ws_id: str, workspace: dict[str, Any], entry: str | None = None, search: str | None = None,
                       first: str | None = None, last: str | None = None) -> dict[str, Any]:
        from .activity import find_entries

        led, meta, _ = self._load(ws_id, None)
        lo, hi = self._span(led, first, last)
        return self._about(workspace, led, meta) | find_entries(led, lo, hi, entry, search)

    def profit_drivers(self, ws_id: str, workspace: dict[str, Any], month: str | None = None, against: str = "average",
                       span: int = 6) -> dict[str, Any]:
        from .activity import profit_drivers

        led, meta, i = self._load(ws_id, month)
        return self._about(workspace, led, meta) | profit_drivers(led, led.months[i], against, span)

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
