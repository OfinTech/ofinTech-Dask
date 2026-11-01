"""The accountant's and the accounting manager's pages.

* ``entries`` and ``accounts`` are a general-ledger browser over the same ledger the statements read: journal entries
  with their lines, filtered by month, text, account, party and type; accounts with their role and month-end balance.
* ``validation`` checks the ledger the way a reviewer would before relying on it: entries that do not balance, lines
  with no account or no usable date, lines dated in the future, lines with both a debit and a credit, zero lines,
  single-line entries, balances left on suspense or clearing accounts, accounts whose role is still a suggestion.
* ``oversight`` is the accounting manager's desk: reconciliation status by month, unmatched bank items, pending
  approvals, exceptions, team activity, documents, reporting progress, outstanding tasks and operational measures.

Nothing here changes data.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from ..db import loads
from ..errors import Invalid
from .ledger import Ledger
from .roles import BALANCE_ROLES, INCOME_ROLES, PL_ROLES, account_key
from .service import today as service_today

if TYPE_CHECKING:
    from ..app import Engine

SUSPENSE_WORDS = ("suspense", "clearing", "uncategori", "unallocated", "unapplied", "to be allocated", "معلق", "مؤقت", "suspens", "pendiente de aplicar", "attente")


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


class Operations:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _load(self, ws_id: str, month: str | None) -> tuple[Ledger, dict[str, Any], int]:
        ev, meta = self.engine.analysis.evaluator(ws_id)
        months = ev.ledger.months
        if not months:
            raise Invalid("The general ledger has no dated lines yet.", code="no_ledger")
        i = months.index(month) if month in months else len(months) - 1
        return ev.ledger, meta, i

    # -- general ledger browser ----------------------------------------------------------------------------------------
    def entries(self, ws_id: str, workspace: dict[str, Any], *, month: str | None = None, q: str | None = None,
                account: str | None = None, party: str | None = None, kind: str | None = None, min_amount: float | None = None,
                unbalanced_only: bool = False, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        led, meta, i = self._load(ws_id, month)
        m = led.months[i] if month else None
        needle = (q or "").strip().lower()
        pn = (party or "").strip().lower()
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for ln in led.lines:
            if m and ln["date"][:7] != m:
                continue
            groups[ln["entry"]].append(ln)
        out = []
        for entry, lines in groups.items():
            if account and not any(account_key(x["account_code"], x["account"]) == account for x in lines):
                continue
            if pn and not any(pn in (x.get("party") or "").lower() for x in lines):
                continue
            if kind and not any((x.get("type") or "").lower() == kind.lower() for x in lines):
                continue
            debit = sum(x["debit"] for x in lines)
            credit = sum(x["credit"] for x in lines)
            if min_amount is not None and max(debit, credit) < min_amount:
                continue
            balanced = abs(debit - credit) < 0.005
            if unbalanced_only and balanced:
                continue
            if needle:
                hay = " ".join(f"{entry} {x.get('description') or ''} {x.get('party') or ''} {x['account']} {x['account_code'] or ''}"
                               for x in lines).lower()
                if needle not in hay:
                    continue
            dates = sorted(x["date"] for x in lines)
            desc = next((x.get("description") for x in lines if x.get("description")), None)
            parties = sorted({x.get("party") for x in lines if x.get("party")})
            out.append({"entry": entry, "date": dates[0], "type": next((x.get("type") for x in lines if x.get("type")), None),
                        "description": desc, "party": parties[0] if len(parties) == 1 else (f"{parties[0]} +{len(parties) - 1}" if parties else None),
                        "lines": len(lines), "debit": _r(debit), "credit": _r(credit), "balanced": balanced,
                        "accounts": [led.names.get(account_key(x["account_code"], x["account"]), x["account"]) for x in lines[:4]]})
        out.sort(key=lambda e: (e["date"], e["entry"]), reverse=True)
        total_debit = sum(e["debit"] or 0 for e in out)
        return {"month": m, "months": led.months, "currency": workspace.get("base_currency"), "count": len(out),
                "total_debit": _r(total_debit), "unbalanced": sum(1 for e in out if not e["balanced"]),
                "offset": offset, "limit": limit, "rows": out[offset: offset + limit],
                "types": sorted({(x.get("type") or "") for x in led.lines if x.get("type")}),
                "provenance": self.engine.overview._provenance(meta)}

    def accounts(self, ws_id: str, workspace: dict[str, Any], month: str | None = None) -> dict[str, Any]:
        led, meta, i = self._load(ws_id, month)
        months = led.months
        m = months[i]
        chosen = self.engine.analysis._roles(ws_id, meta["ledger"]["id"])
        counts: Counter[str] = Counter()
        month_counts: Counter[str] = Counter()
        classes: dict[str, str] = {}
        debit: dict[str, float] = defaultdict(float)
        credit: dict[str, float] = defaultdict(float)
        role_of: dict[str, str] = {}
        for ln in led.lines:
            key = account_key(ln["account_code"], ln["account"])
            counts[key] += 1
            classes.setdefault(key, ln["account_class"])
            role_of.setdefault(key, led.role_of(ln))
            if ln["date"][:7] == m:
                month_counts[key] += 1
                debit[key] += ln["debit"]
                credit[key] += ln["credit"]
        rows = []
        for key, n in counts.items():
            role = role_of.get(key)
            if role in BALANCE_ROLES:
                balance = led.balances.get(key, {}).get(m)
            elif role in PL_ROLES:
                v = led.by_account.get(role, {}).get(m, {}).get(key, 0.0)
                balance = v
            else:
                balance = None
            rows.append({"key": key, "code": key.split("|", 1)[0] or None, "name": led.names.get(key, key), "class": classes.get(key),
                         "role": role, "confirmed": key in chosen, "lines": n, "lines_month": month_counts.get(key, 0),
                         "debit_month": _r(debit.get(key, 0.0)), "credit_month": _r(credit.get(key, 0.0)),
                         "balance": _r(balance), "kind": "balance" if role in BALANCE_ROLES else "pl" if role in PL_ROLES else None,
                         "suspense": any(w in (led.names.get(key, key)).lower() for w in SUSPENSE_WORDS)})
        rows.sort(key=lambda r: (r["code"] or "~", r["name"]))
        return {"month": m, "months": months, "currency": workspace.get("base_currency"), "rows": rows,
                "confirmed": sum(1 for r in rows if r["confirmed"]), "provenance": self.engine.overview._provenance(meta)}

    # -- validation ----------------------------------------------------------------------------------------------------
    def validation(self, ws_id: str, workspace: dict[str, Any], month: str | None = None) -> dict[str, Any]:
        led, meta, i = self._load(ws_id, month)
        months = led.months
        m = months[i] if month else None
        today = service_today()
        _, raw = self.engine.costcentres.lines(ws_id, meta["ledger"]["id"])
        lines = [ln for ln in raw if not m or (ln["date"] or "")[:7] == m]
        checks: list[dict[str, Any]] = []

        def add(kind: str, level: str, items: list[dict[str, Any]], count: int | None = None) -> None:
            checks.append({"kind": kind, "level": level, "count": count if count is not None else len(items), "samples": items[:8]})

        sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
        for ln in lines:
            s = sums[ln["entry"]]
            s[0] += ln["debit"]; s[1] += ln["credit"]; s[2] += 1
        unbalanced = [{"entry": e, "debit": _r(d), "credit": _r(c), "difference": _r(d - c)} for e, (d, c, _) in sums.items() if abs(d - c) > 0.01]
        add("unbalanced_entries", "high", sorted(unbalanced, key=lambda x: -abs(x["difference"])))
        add("single_line_entries", "medium", [{"entry": e, "debit": _r(d), "credit": _r(c)} for e, (d, c, n) in sums.items() if n == 1])
        add("no_account", "high", [{"entry": ln["entry"], "row": ln.get("row"), "date": ln["date"]} for ln in lines if not ln["account"]])
        add("no_date", "high", [{"entry": ln["entry"], "row": ln.get("row")} for ln in raw if not ln["date"]])
        add("future_date", "medium", [{"entry": ln["entry"], "row": ln.get("row"), "date": ln["date"]} for ln in lines if ln["date"] > today])
        add("debit_and_credit", "medium", [{"entry": ln["entry"], "row": ln.get("row"), "debit": _r(ln["debit"]), "credit": _r(ln["credit"])}
                                           for ln in lines if ln["debit"] > 0 and ln["credit"] > 0])
        add("zero_lines", "low", [{"entry": ln["entry"], "row": ln.get("row"), "account": ln["account"]} for ln in lines if ln["debit"] == 0 and ln["credit"] == 0])
        # suspense and clearing accounts with a balance at the month end
        mm = months[i]
        susp = []
        for key, series in led.balances.items():
            name = led.names.get(key, key).lower()
            bal = series.get(mm, 0.0)
            if any(w in name for w in SUSPENSE_WORDS) and abs(bal) > 0.005:
                susp.append({"key": key, "account": led.names.get(key, key), "balance": _r(bal)})
        add("suspense_balances", "high", sorted(susp, key=lambda x: -abs(x["balance"])))
        chosen = self.engine.analysis._roles(ws_id, meta["ledger"]["id"])
        used = {account_key(ln["account_code"], ln["account"]) for ln in lines}
        unconfirmed = [{"key": k, "account": led.names.get(k, k), "role": led.roles.get(k) or ""} for k in used if k not in chosen]
        add("unconfirmed_roles", "medium", sorted(unconfirmed, key=lambda x: x["account"]))
        # negative balances where none is expected: cash, receivables, payables
        neg = []
        for key, series in led.balances.items():
            role = led.roles.get(key)
            if role is None:
                continue
            bal = series.get(mm, 0.0)
            if (role in ("cash", "receivables") and bal < -0.005) or (role == "payables" and bal > 0.005):
                neg.append({"key": key, "account": led.names.get(key, key), "role": role, "balance": _r(bal if role != "payables" else -bal)})
        add("unexpected_balance_sign", "medium", neg)
        # income or expense posted to the wrong side in the month (a credit on an expense account, a debit on revenue)
        wrong = []
        for ln in lines:
            role = led.role_of(ln)
            if role in INCOME_ROLES and ln["debit"] > 0 and ln["credit"] == 0:
                wrong.append({"entry": ln["entry"], "row": ln.get("row"), "account": ln["account"], "amount": _r(ln["debit"]), "side": "debit"})
            elif role in PL_ROLES and role not in INCOME_ROLES and ln["credit"] > 0 and ln["debit"] == 0:
                wrong.append({"entry": ln["entry"], "row": ln.get("row"), "account": ln["account"], "amount": _r(ln["credit"]), "side": "credit"})
        add("contra_postings", "low", wrong)
        order = {"high": 0, "medium": 1, "low": 2}
        checks.sort(key=lambda c: (order[c["level"]], -c["count"]))
        issues = sum(c["count"] for c in checks if c["level"] != "low")
        return {"month": m, "months": months, "currency": workspace.get("base_currency"), "lines": len(lines), "entries": len(sums),
                "checks": checks, "issues": issues, "clean": issues == 0, "provenance": self.engine.overview._provenance(meta),
                "balance": meta.get("balance"), "ledger": meta["ledger"]}

    # -- the accounting manager's desk ----------------------------------------------------------------------------------
    async def oversight(self, ws_id: str, workspace: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        import time

        db = self.engine.db
        try:
            led, meta, i = await asyncio.to_thread(self._load, ws_id, None)
            months = led.months
            last = months[i]
            ledger_meta = meta["ledger"]
        except Invalid:
            led, meta, months, last, ledger_meta = None, {}, [], None, None
        # reconciliation and reporting progress: one row per month of the last six, from close runs
        runs = db.all("SELECT r.id, r.month, r.status, r.result, r.prepared_at, r.closed_at, r.statement, u.name AS closed_by FROM close_runs r "
                      "LEFT JOIN users u ON u.id = r.closed_by WHERE r.workspace_id = ? ORDER BY r.month DESC, r.created_at DESC", (ws_id,))
        by_month: dict[str, dict[str, Any]] = {}
        for r in runs:
            if r["month"] in by_month:
                continue
            res = loads(r["result"], {}) or {}
            ready = res.get("readiness") or {}
            bank = res.get("bank") or {}
            by_month[r["month"]] = {"run_id": r["id"], "month": r["month"], "status": r["status"], "score": ready.get("score"),
                                    "open_items": ready.get("open_items"), "statement": bool(r["statement"]),
                                    "matched": bank.get("matched"), "statement_lines": bank.get("statement_lines"),
                                    "statement_only": len(bank.get("statement_only") or []), "ledger_only": len(bank.get("ledger_only") or []),
                                    "unexplained": bank.get("difference"), "closed_at": r["closed_at"], "closed_by": r["closed_by"],
                                    "prepared_at": r["prepared_at"]}
        progress = []
        for mth in reversed(months[-6:]):
            row = by_month.get(mth) or {"run_id": None, "month": mth, "status": "none", "score": None, "open_items": None, "statement": False,
                                        "matched": None, "statement_lines": None, "statement_only": 0, "ledger_only": 0, "unexplained": None,
                                        "closed_at": None, "closed_by": None, "prepared_at": None}
            progress.append(row)
        # unmatched bank items from the latest run that has a statement
        unmatched: dict[str, Any] | None = None
        for r in runs:
            res = loads(r["result"], {}) or {}
            bank = res.get("bank") or {}
            if bank.get("statement_lines"):
                unmatched = {"run_id": r["id"], "month": r["month"], "statement_only": (bank.get("statement_only") or [])[:20],
                             "ledger_only": (bank.get("ledger_only") or [])[:20], "statement_only_total": bank.get("statement_only_total"),
                             "ledger_only_total": bank.get("ledger_only_total"), "matched": bank.get("matched"),
                             "statement_lines": bank.get("statement_lines"), "unexplained": bank.get("difference")}
                break
        # open close items by kind, and who is sitting on them
        open_items = db.all("SELECT i.kind, i.status, COUNT(*) AS n FROM close_items i JOIN close_runs r ON r.id = i.run_id WHERE "
                            "i.workspace_id = ? AND r.status = 'open' AND i.status IN ('proposed', 'open') GROUP BY i.kind, i.status", (ws_id,))
        approvals = db.all("SELECT a.id, a.summary, a.tool, a.created_at, u.name AS requested_by FROM approvals a LEFT JOIN users u ON "
                           "u.id = a.requested_by WHERE a.workspace_id = ? AND a.status = 'pending' ORDER BY a.created_at ASC LIMIT 20", (ws_id,))
        forecasts = db.all("SELECT id, name, scenario, version, updated_at FROM forecasts WHERE workspace_id = ? AND status = 'submitted' "
                           "ORDER BY updated_at DESC LIMIT 10", (ws_id,))
        failed_runs = db.all("SELECT r.id, r.workflow_id, w.name, r.status, r.started_at FROM workflow_runs r JOIN workflows w ON w.id = r.workflow_id "
                             "WHERE w.workspace_id = ? AND r.status IN ('failed', 'waiting_approval') ORDER BY r.started_at DESC LIMIT 10", (ws_id,))
        # exceptions: validation of the last month and anomalies
        exceptions: dict[str, Any] = {"validation": None, "anomalies": None}
        if led is not None:
            v = await asyncio.to_thread(self.validation, ws_id, workspace, last)
            exceptions["validation"] = {"issues": v["issues"], "checks": [{"kind": c["kind"], "level": c["level"], "count": c["count"]}
                                                                          for c in v["checks"] if c["count"]]}
            a = await asyncio.to_thread(self.engine.statements.anomalies, ws_id, workspace, last)
            exceptions["anomalies"] = {"count": a["count"], "items": a["items"][:5]}
        # team activity over the last 14 days
        since = time.time() - 14 * 86400
        activity = db.all("SELECT u.name AS user_name, a.action, COUNT(*) AS n, MAX(a.created_at) AS last_at FROM audit_log a LEFT JOIN users u ON "
                          "u.id = a.user_id WHERE a.workspace_id = ? AND a.created_at >= ? GROUP BY u.name, a.action ORDER BY n DESC", (ws_id, since))
        people: dict[str, dict[str, Any]] = {}
        for row in activity:
            p = people.setdefault(row["user_name"] or "—", {"name": row["user_name"] or "—", "actions": 0, "last_at": 0, "top": []})
            p["actions"] += row["n"]
            p["last_at"] = max(p["last_at"], row["last_at"])
            if len(p["top"]) < 4:
                p["top"].append({"action": row["action"], "n": row["n"]})
        recent = db.all("SELECT a.action, a.target, a.created_at, u.name AS user_name FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
                        "WHERE a.workspace_id = ? ORDER BY a.created_at DESC LIMIT 25", (ws_id,))
        docs = db.one("SELECT COUNT(*) AS n, SUM(CASE WHEN status = 'indexed' THEN 1 ELSE 0 END) AS ok, "
                      "SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS failed FROM documents WHERE workspace_id = ?", (ws_id,))
        month_entries = 0
        if led is not None and last:
            month_entries = len({ln["entry"] for ln in led.lines if ln["date"][:7] == last})
        # operational measures: how soon after the month end the close was prepared, lines in the last month, ledger freshness
        prep_lag = []
        for row in progress:
            if row["prepared_at"] and row["month"]:
                from .statements import month_end

                lag = (row["prepared_at"] - time.mktime(month_end(row["month"]).timetuple())) / 86400
                prep_lag.append(max(0.0, lag))
        return {
            "ledger": ledger_meta, "month": last, "currency": workspace.get("base_currency"),
            "reconciliation": progress, "unmatched": unmatched,
            "approvals": [{"id": a["id"], "summary": a["summary"], "tool": a["tool"], "at": a["created_at"], "by": a["requested_by"]} for a in approvals],
            "forecasts": [{"id": f["id"], "name": f["name"], "scenario": f["scenario"], "version": f["version"], "at": f["updated_at"]} for f in forecasts],
            "workflow_runs": [{"id": r["id"], "workflow": r["name"], "status": r["status"], "at": r["started_at"]} for r in failed_runs],
            "open_close_items": [{"kind": r["kind"], "status": r["status"], "count": r["n"]} for r in open_items],
            "exceptions": exceptions,
            "team": sorted(people.values(), key=lambda p: -p["actions"]), "recent": [{"action": r["action"], "target": r["target"],
                                                                                      "at": r["created_at"], "by": r["user_name"]} for r in recent],
            "documents": {"total": (docs or {}).get("n") or 0, "ready": (docs or {}).get("ok") or 0, "failed": (docs or {}).get("failed") or 0,
                          "linked_to_entries": 0},
            "operations": {"entries_last_month": month_entries, "lines": (ledger_meta or {}).get("lines"),
                           "refreshed_at": (ledger_meta or {}).get("refreshed_at"),
                           "avg_days_to_prepare": _r(sum(prep_lag) / len(prep_lag)) if prep_lag else None,
                           "months_closed": sum(1 for r in progress if r["status"] == "closed"),
                           "months_open": sum(1 for r in progress if r["status"] == "open"),
                           "months_not_started": sum(1 for r in progress if r["status"] == "none")},
        }
