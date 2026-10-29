"""Month-end close: one run per ledger and month, refreshed from the ledger as often as needed.

A run keeps people's decisions across refreshes: an item is identified by a stable key (the account, the customer,
the statement line), so re-preparing after a sync keeps what was approved, rejected or explained. An item the ledger
no longer calls for (the depreciation was posted, the customer was invoiced) becomes *resolved*. Nothing is ever
posted to the accounting system: approved entries are exported as a journal file for a person to import.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from ..analysis.roles import account_key
from ..db import dumps, loads, new_id, now
from ..errors import Conflict, Invalid, NotFound
from . import compute
from .pack import ENTRY_KINDS
from .statement import read_statement, reconcile, statement_proposals

if TYPE_CHECKING:
    from ..app import Engine

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
OPEN_STATES = {"proposed", "open"}
CHECK_LABELS = {
    "balanced": "Every entry in the month balances",
    "roles": "Account roles confirmed",
    "entries": "Missing recurring costs and bank items decided",
    "bank": "Bank reconciled to the statement",
    "variances": "Large variances explained",
    "unbilled": "Customers not invoiced this month reviewed",
}


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


class Close:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # -- loading ---------------------------------------------------------------------------------------------------
    def _lines(self, ws_id: str, dataset_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
        ds, lines = self.engine.costcentres.lines(ws_id, dataset_id)
        chosen = {k: v["role"] for k, v in self.engine.analysis._roles(ws_id, ds["id"]).items()}
        return ds, [ln for ln in lines if ln["date"]], chosen

    def overview(self, ws_id: str) -> dict[str, Any]:
        ledgers = self.engine.costcentres.ledgers(ws_id)
        runs = self.engine.db.all("SELECT r.id, r.dataset_id, r.month, r.status, r.result, r.prepared_at, r.prepared_via, "
                                  "r.closed_at, u.name AS closed_by_name FROM close_runs r LEFT JOIN users u ON "
                                  "u.id = r.closed_by WHERE r.workspace_id = ? ORDER BY r.month DESC, r.created_at DESC",
                                  (ws_id,))
        names = {x["id"]: x["name"] for x in ledgers}
        out = []
        for r in runs:
            res = loads(r.pop("result"), {}) or {}
            out.append(r | {"ledger_name": names.get(r["dataset_id"], "(removed ledger)"),
                            "score": (res.get("readiness") or {}).get("score"),
                            "open_items": (res.get("readiness") or {}).get("open_items")})
        suggested = None
        if ledgers:
            try:
                did = self.engine.analysis.ledger_id(ws_id)
                suggested = {"dataset_id": did, "month": self.suggested_month(ws_id, did)}
            except Exception:  # noqa: BLE001 - a broken ledger should not hide the page
                suggested = None
        return {"runs": out, "ledgers": ledgers, "suggested": suggested}

    def suggested_month(self, ws_id: str, dataset_id: str) -> str | None:
        """The last complete month in the ledger: the ledger's last month, unless that is the current month."""
        _, lines, _ = self._lines(ws_id, dataset_id)
        if not lines:
            return None
        last = max(ln["date"] for ln in lines)[:7]
        if last >= _today()[:7]:
            return compute.prev_months(_today()[:7], 1)[0]
        return last

    def _run(self, ws_id: str, run_id: str) -> dict[str, Any]:
        row = self.engine.db.one("SELECT * FROM close_runs WHERE id = ? AND workspace_id = ?", (run_id, ws_id))
        if row is None:
            raise NotFound("That month-end close was not found.")
        return row

    def find(self, ws_id: str, month: str, dataset_id: str | None = None) -> dict[str, Any] | None:
        did = dataset_id or self.engine.analysis.ledger_id(ws_id)
        return self.engine.db.one("SELECT * FROM close_runs WHERE workspace_id = ? AND dataset_id = ? AND month = ?",
                                  (ws_id, did, month))

    # -- preparing ---------------------------------------------------------------------------------------------------
    def prepare(self, ws_id: str, user_id: str | None, *, month: str | None = None, dataset_id: str | None = None,
                via: str = "person", materiality: float | None = None) -> dict[str, Any]:
        did = self.engine.analysis.ledger_id(ws_id, dataset_id)
        month = month or self.suggested_month(ws_id, did)
        if not month or not MONTH.match(month):
            raise Invalid("Choose a month as YYYY-MM.", code="bad_month")
        run = self.engine.db.one("SELECT * FROM close_runs WHERE workspace_id = ? AND dataset_id = ? AND month = ?",
                                 (ws_id, did, month))
        if run is None:
            run = {"id": new_id("cl_"), "workspace_id": ws_id, "dataset_id": did, "month": month, "status": "open",
                   "settings": "{}", "created_at": now(), "updated_at": now()}
            self.engine.db.insert("close_runs", run)
        elif run["status"] == "closed":
            raise Conflict(f"{month} is closed. Reopen it to prepare it again.", code="closed")
        settings = loads(run["settings"], {}) or {}
        if materiality is not None:
            if materiality < 0:
                raise Invalid("Materiality is zero or more.")
            settings["materiality"] = float(materiality)
        self._compute(ws_id, run, settings, user_id, via)
        self.engine.audit(ws_id, user_id, "close.prepare", run["id"], {"month": month, "via": via})
        return self.view(ws_id, run["id"])

    def _compute(self, ws_id: str, run: dict[str, Any], settings: dict[str, Any], user_id: str | None, via: str) -> None:
        ds, lines, chosen = self._lines(ws_id, run["dataset_id"])
        month = run["month"]
        months = {ln["date"][:7] for ln in lines}
        if month not in months:
            first, last = (min(months), max(months)) if months else ("—", "—")
            raise Invalid(f"The ledger “{ds['name']}” has no lines in {month} (it covers {first} to {last}).",
                          code="month_not_in_ledger")
        res = compute.prepare(lines, chosen, month, materiality=settings.get("materiality"))
        accts = compute.Accounts(lines, chosen)
        fresh: list[dict[str, Any]] = [*res["proposals"], *res["variances"], *res["unbilled"]]
        bank = None
        stmt = loads(run.get("statement"), None) if run.get("statement") else None
        if stmt:
            cash = {a["key"]: a for a in res["cash_accounts"]}
            acc = cash.get(stmt.get("account_key") or "") or self._default_cash(lines, accts, month)
            if acc:
                acc_lines = [ln for ln in lines if account_key(ln["account_code"], ln["account"]) == acc["key"]]
                book = sum(ln["debit"] - ln["credit"] for ln in acc_lines if ln["date"] <= res["month_end"])
                bank = reconcile(stmt["parsed"], acc_lines, month=month, month_end=res["month_end"],
                                 account={"key": acc["key"], "code": acc["code"], "name": acc["name"]},
                                 book_balance=book, closing_balance=stmt.get("closing_balance"))
                entries, review = statement_proposals(bank, accts, res["month_end"])
                fresh.extend(entries)
                fresh.extend(review)
        self._merge(ws_id, run["id"], fresh)
        result = {"health": res["health"], "materiality": res["materiality"], "month_end": res["month_end"],
                  "cash_accounts": res["cash_accounts"], "bank": bank,
                  "ledger": {"id": ds["id"], "name": ds["name"], "refreshed_at": ds["refreshed_at"]}}
        result["readiness"] = self._readiness(run["id"], result)
        self.engine.db.update("close_runs", run["id"], {
            "settings": dumps(settings), "result": dumps(result), "prepared_by": user_id, "prepared_via": via,
            "prepared_at": now(), "updated_at": now()})

    @staticmethod
    def _default_cash(lines: list[dict[str, Any]], accts: compute.Accounts, month: str) -> dict[str, Any] | None:
        counts: dict[str, int] = {}
        for ln in lines:
            if ln["date"][:7] == month and accts.role(ln) == "cash":
                k = account_key(ln["account_code"], ln["account"])
                counts[k] = counts.get(k, 0) + 1
        if not counts:
            return None
        a = accts.info[max(counts, key=lambda k: counts[k])]
        return {"key": a["key"], "code": a["code"], "name": a["name"]}

    def _merge(self, ws_id: str, run_id: str, fresh: list[dict[str, Any]]) -> None:
        existing = {r["key"]: r for r in self.engine.db.all("SELECT * FROM close_items WHERE run_id = ?", (run_id,))}
        ts = now()
        seen = set()
        for pos, f in enumerate(fresh):
            key = f["key"]
            if key in seen:
                continue
            seen.add(key)
            old = existing.get(key)
            first_status = "proposed" if f["kind"] in ENTRY_KINDS else "open"
            if old is None:
                self.engine.db.insert("close_items", {
                    "id": new_id("ci_"), "run_id": run_id, "workspace_id": ws_id, "kind": f["kind"], "key": key,
                    "status": first_status, "position": pos, "data": dumps(f), "created_at": ts, "updated_at": ts})
                continue
            data = loads(old["data"], {}) or {}
            if data.get("edited"):  # keep what a person changed: amount and accounts
                f = f | {"lines": data["lines"], "amount": data["amount"], "edited": True, "warning": data.get("warning")}
            status = old["status"]
            if status == "resolved":
                status = data.get("was") or first_status
            self.engine.db.update("close_items", old["id"], {"data": dumps(f), "position": pos, "status": status,
                                                             "updated_at": ts})
        for key, old in existing.items():
            if key in seen or old["status"] == "resolved":
                continue
            data = loads(old["data"], {}) or {}
            data["was"] = old["status"]
            self.engine.db.update("close_items", old["id"], {"status": "resolved", "data": dumps(data), "updated_at": ts})

    def _items(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.engine.db.all("SELECT i.*, u.name AS decided_by_name FROM close_items i LEFT JOIN users u ON "
                                  "u.id = i.decided_by WHERE i.run_id = ? ORDER BY i.position, i.created_at", (run_id,))
        for r in rows:
            r["data"] = loads(r["data"], {}) or {}
        return rows

    def _readiness(self, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        items = [i for i in self._items(run_id) if i["status"] != "resolved"]
        h = result["health"]
        entries = [i for i in items if i["kind"] in ENTRY_KINDS]
        bank_entries = [i for i in entries if i["kind"] in ("bank_fee", "bank_interest")]
        var = [i for i in items if i["kind"] == "variance"]
        unb = [i for i in items if i["kind"] == "unbilled"]
        unknown = [i for i in items if i["kind"] == "bank_unknown"]
        b = result.get("bank")

        def n_open(xs: list[dict[str, Any]]) -> int:
            return sum(1 for x in xs if x["status"] in OPEN_STATES)

        checks = []

        def add(key: str, status: str, detail: str, **data: Any) -> None:
            checks.append({"key": key, "label": CHECK_LABELS[key], "status": status, "detail": detail, "data": data})

        add("balanced", "ok" if not h["unbalanced_count"] else "todo",
            f"{h['entries']} entries, {h['lines']} lines; debits {h['debit']:,.2f}, credits {h['credit']:,.2f}."
            if not h["unbalanced_count"] else
            f"{h['unbalanced_count']} entries do not balance: " + ", ".join(u["entry"] for u in h["unbalanced"][:5]) + ".",
            entries=h["entries"], lines=h["lines"], debit=h["debit"], credit=h["credit"], unbalanced=h["unbalanced_count"],
            examples=[u["entry"] for u in h["unbalanced"][:5]])
        add("roles", "ok" if not h["unconfirmed_accounts"] else "warn",
            "Every account used this month has a confirmed role." if not h["unconfirmed_accounts"] else
            f"{h['unconfirmed_accounts']} accounts used this month have a suggested role only (Financial analysis → "
            "Account roles).", count=h["unconfirmed_accounts"])
        open_e = n_open(entries)
        add("entries", "ok" if not open_e else "todo",
            ("Nothing is missing." if not entries else
             f"{len(entries)} proposed entries decided.") if not open_e else
            f"{open_e} of {len(entries)} proposed entries wait for a decision.", open=open_e, total=len(entries))
        bd = {"matched": b["matched"], "lines": b["statement_lines"], "difference": b.get("difference"),
              "open": n_open(bank_entries) + n_open(unknown), "outstanding": len(b["ledger_only"])} if b else {}
        if not b:
            add("bank", "todo", "Add the month's bank statement to match it to the ledger.", state="none")
        elif b.get("difference") is None:
            add("bank", "warn", f"{b['matched']} of {b['statement_lines']} statement lines matched. Add the closing "
                                "balance from the statement to finish the reconciliation.", state="no_balance", **bd)
        elif abs(b["difference"]) > 0.005:
            add("bank", "warn", f"Unexplained difference {b['difference']:,.2f} after the reconciling items.",
                state="difference", **bd)
        elif n_open(bank_entries) or n_open(unknown):
            add("bank", "todo", f"Reconciles; {n_open(bank_entries) + n_open(unknown)} statement items still to "
                                "decide or review.", state="open", **bd)
        else:
            add("bank", "ok", f"Reconciled: {b['matched']} lines matched, {len(b['ledger_only'])} in the ledger "
                              f"not yet on the statement, no unexplained difference.", state="ok", **bd)
        add("variances", "ok" if not n_open(var) else "todo",
            (f"{len(var)} variances explained." if var else "No variance above materiality "
             f"({result['materiality']:,.2f}).") if not n_open(var) else
            f"{n_open(var)} of {len(var)} variances above {result['materiality']:,.2f} need an explanation.",
            open=n_open(var), total=len(var), materiality=result["materiality"])
        add("unbilled", "ok" if not n_open(unb) else "todo",
            ("Reviewed." if unb else "Every regular customer was invoiced.") if not n_open(unb) else
            f"{n_open(unb)} regular customers have no invoice this month.", open=n_open(unb), total=len(unb))
        weight = {"ok": 1.0, "warn": 0.5, "todo": 0.0}
        score = round(100 * sum(weight[c["status"]] for c in checks) / len(checks))
        return {"score": score, "checks": checks, "open_items": sum(1 for i in items if i["status"] in OPEN_STATES)}

    def _refresh_readiness(self, run: dict[str, Any]) -> None:
        result = loads(run["result"], {}) or {}
        if not result:
            return
        result["readiness"] = self._readiness(run["id"], result)
        self.engine.db.update("close_runs", run["id"], {"result": dumps(result), "updated_at": now()})

    # -- the bank statement ------------------------------------------------------------------------------------------
    def add_statement(self, ws_id: str, user_id: str, run_id: str, filename: str, data: bytes, *,
                      account_key_: str | None = None, closing_balance: float | None = None) -> dict[str, Any]:
        run = self._open_run(ws_id, run_id)
        parsed = read_statement(data, filename)
        stmt = {"filename": filename[:200], "uploaded_by": user_id, "uploaded_at": now(), "parsed": parsed,
                "account_key": account_key_, "closing_balance": closing_balance}
        self.engine.db.update("close_runs", run_id, {"statement": dumps(stmt), "updated_at": now()})
        run["statement"] = dumps(stmt)
        self._compute(ws_id, run, loads(run["settings"], {}) or {}, user_id, "person")
        self.engine.audit(ws_id, user_id, "close.statement", run_id, {"filename": filename[:200],
                                                                     "lines": len(parsed["lines"])})
        return self.view(ws_id, run_id)

    def set_statement_options(self, ws_id: str, user_id: str, run_id: str, *, account_key_: str | None,
                              closing_balance: float | None) -> dict[str, Any]:
        run = self._open_run(ws_id, run_id)
        stmt = loads(run["statement"], None) if run["statement"] else None
        if not stmt:
            raise Invalid("Add the bank statement first.")
        stmt["account_key"] = account_key_
        stmt["closing_balance"] = closing_balance
        self.engine.db.update("close_runs", run_id, {"statement": dumps(stmt), "updated_at": now()})
        run["statement"] = dumps(stmt)
        self._compute(ws_id, run, loads(run["settings"], {}) or {}, user_id, "person")
        return self.view(ws_id, run_id)

    def _open_run(self, ws_id: str, run_id: str) -> dict[str, Any]:
        run = self._run(ws_id, run_id)
        if run["status"] == "closed":
            raise Conflict(f"{run['month']} is closed. Reopen it to make changes.", code="closed")
        return run

    # -- decisions ---------------------------------------------------------------------------------------------------
    def decide(self, ws_id: str, user_id: str, run_id: str, item_id: str, body: dict[str, Any]) -> dict[str, Any]:
        run = self._open_run(ws_id, run_id)
        item = self.engine.db.one("SELECT * FROM close_items WHERE id = ? AND run_id = ?", (item_id, run_id))
        if item is None:
            raise NotFound("That item was not found.")
        if item["status"] == "resolved":
            raise Conflict("The ledger no longer calls for this item.", code="resolved")
        data = loads(item["data"], {}) or {}
        sets: dict[str, Any] = {"updated_at": now()}
        note = body.get("note")
        if note is not None:
            sets["note"] = str(note).strip()[:1000] or None
        status = body.get("status")
        allowed = ({"proposed", "approved", "rejected"} if item["kind"] in ENTRY_KINDS
                   else {"open", "explained"} if item["kind"] == "variance" else {"open", "reviewed"})
        if status is not None:
            if status not in allowed:
                raise Invalid(f"Status is one of: {', '.join(sorted(allowed))}.")
            if status == "explained" and not (sets.get("note") or (note is None and item["note"])):
                raise Invalid("Write the explanation for this variance.", code="note_required")
            if status == "rejected" and not (sets.get("note") or (note is None and item["note"])):
                raise Invalid("Say why the entry is not needed (it is kept in the close pack).", code="note_required")
            sets["status"] = status
            sets["decided_by"] = user_id if status not in OPEN_STATES else None
            sets["decided_at"] = now() if status not in OPEN_STATES else None
        if item["kind"] in ENTRY_KINDS and ("amount" in body or "lines" in body):
            if item["status"] == "approved" and status is None:
                raise Conflict("Return the entry to proposed before changing it.", code="approved")
            lines = [dict(x) for x in data["lines"]]
            if body.get("amount") is not None:
                amt = round(float(body["amount"]), 2)
                if amt <= 0 or amt > 1e12:
                    raise Invalid("The amount is a positive number.")
                for ln in lines:
                    if ln["debit"]:
                        ln["debit"] = amt
                    if ln["credit"]:
                        ln["credit"] = amt
                data["amount"] = amt
            for change in body.get("lines") or []:
                i = int(change.get("index", -1))
                if not 0 <= i < len(lines):
                    raise Invalid("Unknown line of the entry.")
                name = str(change.get("account") or "").strip()
                if not name:
                    raise Invalid("Name the account.")
                lines[i]["account"] = name[:200]
                lines[i]["account_code"] = (str(change.get("account_code") or "").strip()[:40] or None)
                data["warning"] = None
            data["lines"] = lines
            data["edited"] = True
            sets["data"] = dumps(data)
        self.engine.db.update("close_items", item_id, sets)
        self._refresh_readiness(self._run(ws_id, run_id))
        self.engine.audit(ws_id, user_id, "close.item", item_id, {"run_id": run_id, "kind": item["kind"],
                                                                  "status": sets.get("status"), "month": run["month"]})
        return self.view(ws_id, run_id)

    def set_status(self, ws_id: str, user_id: str, run_id: str, status: str, note: str | None = None) -> dict[str, Any]:
        run = self._run(ws_id, run_id)
        if status not in ("open", "closed"):
            raise Invalid("Status is open or closed.")
        if status == run["status"]:
            return self.view(ws_id, run_id)
        if status == "closed":
            self.engine.db.update("close_runs", run_id, {"status": "closed", "closed_by": user_id, "closed_at": now(),
                                                         "updated_at": now()})
            self.engine.audit(ws_id, user_id, "close.closed", run_id, {"month": run["month"], "note": note})
        else:
            self.engine.db.update("close_runs", run_id, {"status": "open", "closed_by": None, "closed_at": None,
                                                         "updated_at": now()})
            self.engine.audit(ws_id, user_id, "close.reopened", run_id, {"month": run["month"], "note": note})
        return self.view(ws_id, run_id)

    def delete(self, ws_id: str, user_id: str, run_id: str) -> None:
        run = self._run(ws_id, run_id)
        self.engine.db.execute("DELETE FROM close_runs WHERE id = ?", (run_id,))
        self.engine.audit(ws_id, user_id, "close.delete", run_id, {"month": run["month"]})

    # -- reading -----------------------------------------------------------------------------------------------------
    def view(self, ws_id: str, run_id: str) -> dict[str, Any]:
        run = self._run(ws_id, run_id)
        result = loads(run["result"], {}) or {}
        stmt = loads(run["statement"], None) if run["statement"] else None
        items = self._items(run_id)
        names = {r["id"]: r["name"] for r in self.engine.db.all(
            "SELECT id, name FROM users WHERE id IN (?, ?, ?)", (run["prepared_by"], run["closed_by"],
                                                                 (stmt or {}).get("uploaded_by")))}
        approved = [i for i in items if i["kind"] in ENTRY_KINDS and i["status"] == "approved"]
        return {
            "id": run["id"], "month": run["month"], "status": run["status"], "dataset_id": run["dataset_id"],
            "month_end": result.get("month_end") or compute.month_end(run["month"]),
            "ledger": result.get("ledger") or {"id": run["dataset_id"], "name": ""},
            "materiality": result.get("materiality"), "health": result.get("health"),
            "cash_accounts": result.get("cash_accounts") or [], "bank": result.get("bank"),
            "readiness": result.get("readiness") or {"score": 0, "checks": [], "open_items": 0},
            "statement": {"filename": stmt["filename"], "uploaded_at": stmt["uploaded_at"],
                          "uploaded_by_name": names.get(stmt["uploaded_by"]), "account_key": stmt.get("account_key"),
                          "closing_balance": stmt.get("closing_balance"),
                          "file_closing": stmt["parsed"].get("closing"), "lines": len(stmt["parsed"]["lines"]),
                          "first_date": stmt["parsed"]["first_date"], "last_date": stmt["parsed"]["last_date"]}
            if stmt else None,
            "items": [{k: i[k] for k in ("id", "kind", "status", "note", "data", "decided_at")}
                      | {"decided_by_name": i["decided_by_name"]} for i in items],
            "journal": {"entries": len(approved), "total": round(sum(i["data"]["amount"] for i in approved), 2)},
            "prepared_at": run["prepared_at"], "prepared_via": run["prepared_via"],
            "prepared_by_name": names.get(run["prepared_by"]),
            "prepared_at_text": datetime.fromtimestamp(run["prepared_at"], tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
            if run["prepared_at"] else None,
            "closed_at": run["closed_at"], "closed_by_name": names.get(run["closed_by"]),
        }

    def summary_text(self, view: dict[str, Any]) -> str:
        """A few lines for notifications, workflows and the assistant."""
        r = view["readiness"]
        items = [i for i in view["items"] if i["status"] != "resolved"]
        entries = [i for i in items if i["kind"] in ENTRY_KINDS]
        var = [i for i in items if i["kind"] == "variance"]
        lines = [f"Month-end close {view['month']} for the ledger “{view['ledger']['name']}”: {r['score']}% ready, "
                 f"{r['open_items']} items to decide."]
        if entries:
            lines.append(f"{len(entries)} proposed entries, {sum(i['data']['amount'] for i in entries):,.2f} in total: "
                         + "; ".join(f"{i['data']['title']} {i['data']['amount']:,.2f} ({i['status']})"
                                     for i in entries[:6]) + ".")
        if var:
            lines.append("Largest variances: " + "; ".join(i["data"]["title"] for i in var[:4]) + ".")
        for c in r["checks"]:
            if c["status"] != "ok":
                lines.append(f"- {c['label']}: {c['detail']}")
        return "\n".join(lines)


def month_of(value: str | None) -> str | None:
    if not value:
        return None
    if MONTH.match(value):
        return value
    try:
        return date.fromisoformat(value).isoformat()[:7]
    except ValueError:
        return None
