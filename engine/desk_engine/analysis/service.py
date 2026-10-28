"""Financial analysis: the ledger it reads, account roles, the KPI scorecard and forecasts."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, new_id, now
from ..errors import Forbidden, Invalid, NotFound
from ..web import role_at_least
from . import forecast as fc
from .kpis import BY_ID, CATEGORIES, UNITS, Evaluator, check_formula, definitions
from .ledger import MEASURES, Ledger, opening_from_balance_sheet
from .roles import ROLES, accounts_with_roles, role_fits

if TYPE_CHECKING:
    from ..app import Engine

STATUS_FLOW = {"draft": {"submitted"}, "submitted": {"draft", "approved"}, "approved": {"archived"}, "archived": set()}


class Analysis:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._cache: dict[tuple[Any, ...], tuple[Ledger, dict[str, Any]]] = {}

    # -- which ledger ------------------------------------------------------------------------------------------------
    def ledger_id(self, ws_id: str, dataset_id: str | None = None) -> str:
        ledgers = self.engine.costcentres.ledgers(ws_id)
        if not ledgers:
            raise Invalid("There is no general ledger yet. Sync an accounting connection, push journal lines, or import "
                          "an Excel or CSV file with the general-ledger columns.", code="no_ledger")
        if dataset_id:
            if dataset_id not in {x["id"] for x in ledgers}:
                raise NotFound("That general ledger was not found.")
            return dataset_id
        row = self.engine.db.one("SELECT dataset_id FROM analysis_settings WHERE workspace_id = ?", (ws_id,))
        if row and row["dataset_id"] in {x["id"] for x in ledgers}:
            return row["dataset_id"]
        return ledgers[0]["id"]

    def choose_ledger(self, ws_id: str, user_id: str, dataset_id: str) -> None:
        self.ledger_id(ws_id, dataset_id)
        self.engine.db.execute("INSERT INTO analysis_settings (workspace_id, dataset_id, updated_by, updated_at) VALUES "
                               "(?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET dataset_id = excluded.dataset_id, "
                               "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                               (ws_id, dataset_id, user_id, now()))

    def _companion(self, ws_id: str, gl: dict[str, Any], suffix: str) -> list[list[Any]] | None:
        """Rows of the balance sheet, invoices or bills synced from the same connection as the ledger."""
        from ..data.store import table_name

        name = gl["name"]
        if not name.endswith(" · General ledger"):
            return None
        other = self.engine.db.one("SELECT id, columns FROM datasets WHERE workspace_id = ? AND name = ? AND source = ?",
                                   (ws_id, name[: -len("General ledger")] + suffix, gl["source"]))
        if other is None:
            return None
        keys = ", ".join(c["key"] for c in loads(other["columns"], []))
        return [list(r) for r in self.engine.db.conn.execute(f"SELECT {keys} FROM {table_name(other['id'])}")]

    def _roles(self, ws_id: str, did: str) -> dict[str, dict[str, Any]]:
        rows = self.engine.db.all("SELECT r.account_key, r.role, u.name AS by_name, r.updated_at FROM account_roles r "
                                  "LEFT JOIN users u ON u.id = r.confirmed_by WHERE r.workspace_id = ? AND r.dataset_id = ?",
                                  (ws_id, did))
        return {r["account_key"]: {"role": r["role"], "by": r["by_name"], "at": r["updated_at"]} for r in rows}

    def _plan(self, ws_id: str, did: str) -> dict[str, Any] | None:
        row = self.engine.db.one("SELECT id, name, scenario, version, result, approved_at FROM forecasts WHERE "
                                 "workspace_id = ? AND dataset_id = ? AND is_plan = 1 AND status = 'approved'", (ws_id, did))
        return row

    def load(self, ws_id: str, dataset_id: str | None = None) -> tuple[Ledger, dict[str, Any]]:
        from ..data.store import get_dataset

        did = self.ledger_id(ws_id, dataset_id)
        gl = get_dataset(self.engine, ws_id, did)
        stamp = self.engine.db.one("SELECT MAX(updated_at) AS r FROM account_roles WHERE workspace_id = ? AND "
                                   "dataset_id = ?", (ws_id, did))
        plan = self._plan(ws_id, did)
        key = (ws_id, did, gl["refreshed_at"], stamp["r"] if stamp else None, plan["id"] if plan else None)
        if key in self._cache:
            return self._cache[key]
        _, lines = self.engine.costcentres.lines(ws_id, did)
        chosen = self._roles(ws_id, did)
        roles = {k: v["role"] for k, v in chosen.items()}
        opening, opening_info = opening_from_balance_sheet(lines, self._companion(ws_id, gl, "Balance sheet") or [])
        led = Ledger(lines, roles, opening=opening)
        led.snapshot["ar_overdue_pct"] = _overdue(self._companion(ws_id, gl, "Invoices"))
        led.snapshot["ap_overdue_pct"] = _overdue(self._companion(ws_id, gl, "Bills"))
        if plan and plan["result"]:
            res = loads(plan["result"], {})
            for m, v in zip(res.get("months", []), res.get("lines", {}).get("revenue", []), strict=False):
                led.flows["plan_revenue"][m] = v
        accounts = accounts_with_roles(lines, chosen)
        meta = {"ledger": {"id": did, "name": gl["name"], "refreshed_at": gl["refreshed_at"],
                           "first_date": led.first_date, "last_date": led.last_date, "lines": len(led.lines)},
                "opening": opening_info, "balance": led.balance_check(),
                "roles": {"accounts": len(accounts), "confirmed": sum(1 for a in accounts if a["confirmed"])},
                "plan": {k: plan[k] for k in ("id", "name", "scenario", "version", "approved_at")} if plan else None}
        if len(self._cache) > 32:
            self._cache.clear()
        self._cache[key] = (led, meta)
        return led, meta

    # -- KPI settings ------------------------------------------------------------------------------------------------
    def settings(self, ws_id: str) -> dict[str, dict[str, Any]]:
        rows = self.engine.db.all("SELECT * FROM kpi_settings WHERE workspace_id = ?", (ws_id,))
        return {r["kpi_id"]: {k: r[k] for k in ("name", "category", "formula", "unit", "direction", "target", "warn")
                              if r[k] is not None} | {"enabled": bool(r["enabled"])} for r in rows}

    def evaluator(self, ws_id: str, dataset_id: str | None = None) -> tuple[Evaluator, dict[str, Any]]:
        led, meta = self.load(ws_id, dataset_id)
        return Evaluator(led, definitions(self.settings(ws_id))), meta

    async def scorecard(self, ws_id: str, month: str | None = None, dataset_id: str | None = None) -> dict[str, Any]:
        ev, meta = await asyncio.to_thread(self.evaluator, ws_id, dataset_id)
        cards = await asyncio.to_thread(ev.scorecard, month)
        return meta | {"months": ev.ledger.months, "month": cards[0]["month"] if cards else None,
                       "categories": CATEGORIES, "kpis": cards,
                       "ledgers": self.engine.costcentres.ledgers(ws_id)}

    async def detail(self, ws_id: str, kid: str, month: str | None = None) -> dict[str, Any]:
        ev, meta = await asyncio.to_thread(self.evaluator, ws_id)
        return meta | await asyncio.to_thread(ev.explain, kid, month)

    def set_kpi(self, ws_id: str, user_id: str, kid: str, body: dict[str, Any]) -> dict[str, Any]:
        defs = {k["id"]: k for k in definitions(self.settings(ws_id))}
        custom = kid not in BY_ID
        if custom and kid in MEASURES:
            raise Invalid(f"“{kid}” is a ledger measure; choose another id for the KPI.")
        if custom and kid not in defs and not body.get("name"):
            raise Invalid("A new KPI needs a name and a formula.")
        if body.get("formula"):
            check_formula(body["formula"], set(defs) | {kid})
        if custom and not (body.get("formula") or defs.get(kid, {}).get("formula")):
            raise Invalid("A new KPI needs a formula.")
        if body.get("unit") and body["unit"] not in UNITS:
            raise Invalid(f"Unit is one of: {', '.join(sorted(UNITS))}.")
        if body.get("category") and body["category"] not in CATEGORIES:
            raise Invalid("Unknown category.")
        if body.get("direction") and body["direction"] not in ("up", "down"):
            raise Invalid("Direction is up or down.")
        cur = self.engine.db.one("SELECT * FROM kpi_settings WHERE workspace_id = ? AND kpi_id = ?", (ws_id, kid)) or {}
        vals = {k: cur.get(k) for k in ("name", "category", "formula", "unit", "direction", "target", "warn")}
        vals["enabled"] = cur.get("enabled", 1)
        for k in ("name", "category", "formula", "unit", "direction", "target", "warn", "enabled"):
            if k in body:
                vals[k] = int(bool(body[k])) if k == "enabled" else body[k]
        if not custom:
            vals["name"] = vals["category"] = vals["unit"] = vals["direction"] = None
            if vals.get("formula") == BY_ID[kid]["formula"]:
                vals["formula"] = None
        self.engine.db.execute(
            "INSERT INTO kpi_settings (workspace_id, kpi_id, name, category, formula, unit, direction, target, warn, "
            "enabled, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id, kpi_id) DO "
            "UPDATE SET name = excluded.name, category = excluded.category, formula = excluded.formula, unit = "
            "excluded.unit, direction = excluded.direction, target = excluded.target, warn = excluded.warn, enabled = "
            "excluded.enabled, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (ws_id, kid, vals["name"], vals["category"], vals["formula"], vals["unit"], vals["direction"], vals["target"],
             vals["warn"], vals["enabled"], user_id, now()))
        self.engine.audit(ws_id, user_id, "kpi.update", kid, {k: body[k] for k in body})
        return next(k for k in definitions(self.settings(ws_id)) if k["id"] == kid)

    def reset_kpi(self, ws_id: str, user_id: str, kid: str) -> None:
        self.engine.db.execute("DELETE FROM kpi_settings WHERE workspace_id = ? AND kpi_id = ?", (ws_id, kid))
        self.engine.audit(ws_id, user_id, "kpi.reset", kid)

    # -- account roles -----------------------------------------------------------------------------------------------
    def roles(self, ws_id: str, dataset_id: str | None = None) -> dict[str, Any]:
        did = self.ledger_id(ws_id, dataset_id)
        _, lines = self.engine.costcentres.lines(ws_id, did)
        _, meta = self.load(ws_id, did)
        return {"dataset_id": did, "roles": ROLES, "accounts": accounts_with_roles(lines, self._roles(ws_id, did)),
                "opening": meta["opening"], "balance": meta["balance"]}

    def set_roles(self, ws_id: str, user_id: str, dataset_id: str | None, items: list[dict[str, str]]) -> dict[str, Any]:
        did = self.ledger_id(ws_id, dataset_id)
        _, lines = self.engine.costcentres.lines(ws_id, did)
        classes = {f"{ln['account_code'] or ''}|{ln['account']}": ln["account_class"] for ln in lines}
        ts = now()
        with self.engine.db.tx() as c:
            for it in items:
                key, role = it["key"], it["role"]
                if key not in classes:
                    raise Invalid(f"No account “{key.split('|', 1)[-1]}” in this ledger.")
                if role not in ROLES or not role_fits(role, classes[key]):
                    raise Invalid(f"“{role}” does not fit a {classes[key]} account.", code="bad_role")
                c.execute("INSERT INTO account_roles (workspace_id, dataset_id, account_key, role, confirmed_by, updated_at) "
                          "VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id, dataset_id, account_key) DO UPDATE SET role = "
                          "excluded.role, confirmed_by = excluded.confirmed_by, updated_at = excluded.updated_at",
                          (ws_id, did, key, role, user_id, ts))
        self.engine.audit(ws_id, user_id, "account_roles.update", did, {"accounts": len(items)})
        return self.roles(ws_id, did)

    # -- forecasts ---------------------------------------------------------------------------------------------------
    def _forecast(self, ws_id: str, fid: str) -> dict[str, Any]:
        row = self.engine.db.one("SELECT f.*, a.name AS approved_by_name, c.name AS created_by_name, s.name AS "
                                 "submitted_by_name FROM forecasts f LEFT JOIN users a ON a.id = f.approved_by LEFT JOIN "
                                 "users c ON c.id = f.created_by LEFT JOIN users s ON s.id = f.submitted_by WHERE "
                                 "f.workspace_id = ? AND f.id = ?", (ws_id, fid))
        if row is None:
            raise NotFound("Forecast not found.")
        row["assumptions"] = loads(row["assumptions"], {})
        row["result"] = loads(row["result"], None)
        row["is_plan"] = bool(row["is_plan"])
        return row

    def list_forecasts(self, ws_id: str) -> list[dict[str, Any]]:
        rows = self.engine.db.all("SELECT f.id, f.group_id, f.name, f.scenario, f.version, f.status, f.is_plan, "
                                  "f.start_month, f.months, f.dataset_id, f.created_at, f.updated_at, f.approved_at, "
                                  "u.name AS created_by_name, a.name AS approved_by_name FROM forecasts f LEFT JOIN users u "
                                  "ON u.id = f.created_by LEFT JOIN users a ON a.id = f.approved_by WHERE f.workspace_id = ? "
                                  "ORDER BY f.updated_at DESC", (ws_id,))
        return [r | {"is_plan": bool(r["is_plan"])} for r in rows]

    def _kpi_now(self, ws_id: str, did: str) -> dict[str, float | None]:
        ev, _ = self.evaluator(ws_id, did)
        i = len(ev.ledger.months) - 1
        return {k: ev.value(k, i) for k in ("dso", "dpo", "dio")}

    def create_forecast(self, ws_id: str, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        did = self.ledger_id(ws_id, body.get("dataset_id"))
        led, _ = self.load(ws_id, did)
        start = body.get("start_month") or fc.default_start(led)
        months = int(body.get("months") or 12)
        if not 1 <= months <= 36:
            raise Invalid("A forecast covers 1 to 36 months.")
        a = body.get("assumptions") or fc.defaults(led, self._kpi_now(ws_id, did))
        fc.project(led, a, start, months)  # checks the assumptions
        fid, ts = new_id("fc_"), now()
        self.engine.db.insert("forecasts", {
            "id": fid, "workspace_id": ws_id, "dataset_id": did, "group_id": body.get("group_id") or fid,
            "name": (body.get("name") or "Forecast").strip()[:120], "scenario": (body.get("scenario") or "Base")[:60],
            "version": int(body.get("version") or 1), "status": "draft", "is_plan": 0, "start_month": start,
            "months": months, "assumptions": dumps(a), "result": None, "note": body.get("note"), "created_by": user_id,
            "created_at": ts, "updated_at": ts})
        self.engine.audit(ws_id, user_id, "forecast.create", fid, {"name": body.get("name")})
        return self._forecast(ws_id, fid)

    def update_forecast(self, ws_id: str, user_id: str, fid: str, body: dict[str, Any]) -> dict[str, Any]:
        f = self._forecast(ws_id, fid)
        if f["status"] != "draft":
            raise Invalid("Only a draft can be changed. Make a new version to change a submitted or approved forecast.",
                          code="not_draft")
        sets: dict[str, Any] = {}
        for k in ("name", "scenario", "note", "start_month", "months"):
            if k in body and body[k] is not None:
                sets[k] = body[k]
        a = body.get("assumptions", f["assumptions"])
        led, _ = self.load(ws_id, f["dataset_id"])
        fc.project(led, a, sets.get("start_month", f["start_month"]), int(sets.get("months", f["months"])))
        sets["assumptions"] = dumps(a)
        sets["updated_at"] = now()
        self.engine.db.update("forecasts", fid, sets, workspace_id=ws_id)
        return self._forecast(ws_id, fid)

    def transition(self, ws_id: str, user_id: str, role: str, fid: str, to: str, note: str | None = None) -> dict[str, Any]:
        """draft → submitted (editor) → approved (admin, the finance lead) or back to draft; approved → archived."""
        f = self._forecast(ws_id, fid)
        if to not in STATUS_FLOW.get(f["status"], set()):
            raise Invalid(f"A {f['status']} forecast cannot become {to}.", code="bad_status")
        if to in {"approved", "archived"} and not role_at_least(role, "admin"):
            raise Forbidden("Only an administrator (the finance lead) can approve or archive a forecast.")
        sets: dict[str, Any] = {"status": to, "updated_at": now()}
        if to == "submitted":
            sets["submitted_by"] = user_id
        if to == "approved":
            led, _ = self.load(ws_id, f["dataset_id"])
            res = fc.project(led, f["assumptions"], f["start_month"], f["months"])
            ref = fc.reference(fc.history(led, "revenue")[: len([m for m in led.months if m < f["start_month"]])],
                               f["months"])
            sets |= {"approved_by": user_id, "approved_at": now(), "result": dumps(res | {"reference": ref})}
        if to == "archived":
            sets["is_plan"] = 0
        if note:
            sets["note"] = note
        self.engine.db.update("forecasts", fid, sets, workspace_id=ws_id)
        self.engine.audit(ws_id, user_id, f"forecast.{to}", fid)
        return self._forecast(ws_id, fid)

    def make_plan(self, ws_id: str, user_id: str, fid: str) -> dict[str, Any]:
        f = self._forecast(ws_id, fid)
        if f["status"] != "approved":
            raise Invalid("Only an approved forecast can be the plan.", code="not_approved")
        with self.engine.db.tx() as c:
            c.execute("UPDATE forecasts SET is_plan = 0 WHERE workspace_id = ? AND dataset_id = ?", (ws_id, f["dataset_id"]))
            c.execute("UPDATE forecasts SET is_plan = 1, updated_at = ? WHERE id = ?", (now(), fid))
        self.engine.audit(ws_id, user_id, "forecast.plan", fid)
        return self._forecast(ws_id, fid)

    def copy_forecast(self, ws_id: str, user_id: str, fid: str, scenario: str | None = None) -> dict[str, Any]:
        """A new draft from any forecast: the next version of the same scenario, or a new scenario."""
        f = self._forecast(ws_id, fid)
        if scenario:
            version = 1
        else:
            top = self.engine.db.one("SELECT MAX(version) AS v FROM forecasts WHERE workspace_id = ? AND group_id = ? AND "
                                     "scenario = ?", (ws_id, f["group_id"], f["scenario"]))
            version = int(top["v"] or 0) + 1
        return self.create_forecast(ws_id, user_id, {
            "dataset_id": f["dataset_id"], "group_id": f["group_id"], "name": f["name"], "scenario": scenario or f["scenario"],
            "version": version, "start_month": f["start_month"], "months": f["months"], "assumptions": f["assumptions"]})

    def delete_forecast(self, ws_id: str, user_id: str, role: str, fid: str) -> None:
        f = self._forecast(ws_id, fid)
        if f["status"] == "approved" or f["is_plan"]:
            raise Invalid("An approved forecast is kept; archive it instead.", code="approved")
        self.engine.db.execute("DELETE FROM forecasts WHERE id = ? AND workspace_id = ?", (fid, ws_id))
        self.engine.audit(ws_id, user_id, "forecast.delete", fid)

    async def forecast_view(self, ws_id: str, fid: str, preview: dict[str, Any] | None = None) -> dict[str, Any]:
        f = self._forecast(ws_id, fid)
        led, meta = await asyncio.to_thread(self.load, ws_id, f["dataset_id"])
        if f["result"] and f["status"] in {"approved", "archived"}:
            res = f["result"]
            acc = await asyncio.to_thread(fc.accuracy, led, res)
        else:
            a = (preview or {}).get("assumptions", f["assumptions"])
            start = (preview or {}).get("start_month", f["start_month"])
            months = int((preview or {}).get("months", f["months"]))
            res = await asyncio.to_thread(fc.project, led, a, start, months)
            hist = fc.history(led, "revenue")[: len([m for m in led.months if m < start])]
            res["reference"] = await asyncio.to_thread(fc.reference, hist, months)
            acc = None
        hist_months = [m for m in led.months if m < res["start"]][-12:]
        actual = {"months": hist_months, "revenue": [round(led.flows["revenue"].get(m, 0.0), 2) for m in hist_months],
                  "net_profit": [round(led.flows["net_profit"].get(m, 0.0), 2) for m in hist_months],
                  "cash": [round(led.stocks["cash"].get(m, 0.0), 2) for m in hist_months]}
        siblings = [x for x in self.list_forecasts(ws_id) if x["group_id"] == f["group_id"]]
        return {"forecast": f, "result": res, "actual": actual, "accuracy": acc, "siblings": siblings,
                "ledger": meta["ledger"], "methods": sorted(fc.METHODS), "lines": fc.LINES}

    async def compare(self, ws_id: str, group_id: str) -> list[dict[str, Any]]:
        out = []
        for x in [x for x in self.list_forecasts(ws_id) if x["group_id"] == group_id and x["status"] != "archived"]:
            v = await self.forecast_view(ws_id, x["id"])
            out.append({k: x[k] for k in ("id", "name", "scenario", "version", "status", "is_plan")} | v["result"]["summary"])
        return out


def _overdue(rows: list[list[Any]] | None) -> float | None:
    """Share of the open balance more than 30 days overdue, from synced invoices or bills
    (columns: number, party, issue, due, currency, total, balance, status, days overdue, aging)."""
    if not rows:
        return None
    open_ = [(float(r[6] or 0), float(r[8] or 0)) for r in rows if (r[6] or 0) > 0]
    total = sum(b for b, _ in open_)
    if total <= 0:
        return 0.0
    return sum(b for b, d in open_ if d > 30) / total


def today() -> str:
    return date.today().isoformat()
