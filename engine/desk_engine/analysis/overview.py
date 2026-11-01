"""The executive overview and the drill-down behind it.

The overview answers five questions from the general ledger in plain business terms: how the business is
performing, how much money it has, whether it is profitable, which risks need attention and what to decide next.
Every figure says what kind of number it is (``provenance``): ``actual`` when it is booked in the ledger with
account roles the finance team confirmed, ``unverified`` while the roles are still the engine's suggestion,
``budget`` for the approved plan, ``estimate`` for anything a model wrote. Nothing here changes data.

The drill-down keeps one path: an executive figure → the profit and loss lines behind it → the accounts behind a
line → the ledger lines of one account in the month → one journal entry with its source and audit trail. The
reporting month travels with every step.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..db import loads
from ..errors import Invalid, NotFound
from .kpis import Evaluator
from .ledger import Ledger
from .roles import PL_ROLES, account_key
from .service import today as service_today

if TYPE_CHECKING:
    from ..app import Engine

EXPENSE_ROLES = ["cogs", "payroll", "opex", "depreciation", "interest", "tax", "other_expense"]
PL_LINES: list[tuple[str, str, bool]] = [  # (measure, kind, drillable to accounts)
    ("revenue", "income", True), ("cogs", "cost", True), ("gross_profit", "subtotal", False),
    ("payroll", "cost", True), ("opex", "cost", True), ("depreciation", "cost", True),
    ("operating_profit", "subtotal", False), ("other_income", "income", True), ("other_expense", "cost", True),
    ("interest", "cost", True), ("tax", "cost", True), ("net_profit", "total", False),
]
# which KPIs the overview reads as risks, and how serious a "watch" or "act" on them is
RISK_KPIS = ["days_cash", "cash_runway", "current_ratio", "net_margin", "operating_cash_flow", "ar_overdue",
             "collection_rate", "dso", "customer_concentration", "debt_to_equity", "interest_cover", "dscr",
             "opex_ratio", "payroll_ratio", "plan_variance", "revenue_growth", "gross_margin"]
HEALTH_LABELS = [(80, "strong"), (60, "stable"), (40, "watch"), (0, "at_risk")]
# (kpi, higher is better, good at, watch at) — reference thresholds used until the finance lead sets a target
HEALTH_SIGNALS: list[tuple[str, bool, float, float]] = [
    ("net_margin", True, 0.05, 0.0), ("revenue_growth", True, 0.0, -0.05), ("operating_cash_flow", True, 0.0, -1e-9),
    ("days_cash", True, 60.0, 30.0), ("current_ratio", True, 1.2, 1.0), ("ar_overdue", False, 0.10, 0.25),
    ("collection_rate", True, 0.85, 0.7), ("debt_to_equity", False, 1.0, 2.0), ("interest_cover", True, 3.0, 1.5),
    ("customer_concentration", False, 0.5, 0.7),
]


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _pct(now: float | None, before: float | None) -> float | None:
    if now is None or before is None or abs(before) < 0.005:
        return None
    return round((now - before) / abs(before), 4)


def fiscal_start(month: str, start_month: int) -> str:
    """First month of the fiscal year that contains ``month``."""
    y, m = int(month[:4]), int(month[5:7])
    if m < start_month:
        y -= 1
    return f"{y:04d}-{start_month:02d}"


class Overview:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # -- shared --------------------------------------------------------------------------------------------------------
    def _load(self, ws_id: str, month: str | None, dataset_id: str | None = None) -> tuple[Evaluator, dict[str, Any], int]:
        ev, meta = self.engine.analysis.evaluator(ws_id, dataset_id)
        months = ev.ledger.months
        if not months:
            raise Invalid("The general ledger has no dated lines yet.", code="no_ledger")
        i = months.index(month) if month in months else len(months) - 1
        return ev, meta, i

    @staticmethod
    def _provenance(meta: dict[str, Any]) -> dict[str, Any]:
        roles = meta.get("roles") or {}
        confirmed = roles.get("confirmed", 0) >= roles.get("accounts", 0) and roles.get("accounts", 0) > 0
        balanced = (meta.get("balance") or {}).get("balanced", True)
        kind = "actual" if confirmed and balanced else "unverified"
        notes = []
        if not confirmed:
            notes.append("roles_unconfirmed")
        if not balanced:
            notes.append("unbalanced")
        return {"kind": kind, "notes": notes, "roles": roles, "balance": meta.get("balance")}

    def _plan_lines(self, ws_id: str, meta: dict[str, Any]) -> dict[str, dict[str, float]]:
        """Plan values by measure and month, from the approved forecast marked as the plan."""
        plan = meta.get("plan")
        if not plan:
            return {}
        row = self.engine.db.one("SELECT result FROM forecasts WHERE id = ?", (plan["id"],))
        res = loads(row["result"], {}) if row and row["result"] else {}
        months = res.get("months", [])
        out: dict[str, dict[str, float]] = {}
        for line, values in (res.get("lines") or {}).items():
            out[line] = dict(zip(months, values, strict=False))
        if "revenue" in out and "cogs" in out:
            out["gross_profit"] = {m: out["revenue"].get(m, 0.0) - out["cogs"].get(m, 0.0) for m in months}
        if all(k in out for k in ("revenue", "cogs", "payroll", "opex", "depreciation")):
            out["operating_profit"] = {m: out["gross_profit"][m] - out["payroll"].get(m, 0.0) - out["opex"].get(m, 0.0)
                                       - out["depreciation"].get(m, 0.0) for m in months}
        if "operating_profit" in out:
            out["net_profit"] = {m: out["operating_profit"][m] + out.get("other_income", {}).get(m, 0.0)
                                 - out.get("other_expense", {}).get(m, 0.0) - out.get("interest", {}).get(m, 0.0)
                                 - out.get("tax", {}).get(m, 0.0) for m in months}
        if "cogs" in out:
            out["expenses"] = {m: sum(out.get(r, {}).get(m, 0.0) for r in EXPENSE_ROLES) for m in months}
        return out

    @staticmethod
    def _sum(led: Ledger, name: str, months: list[str]) -> float:
        return sum(led.value(name, m) or 0.0 for m in months)

    # -- the overview ----------------------------------------------------------------------------------------------------
    def overview(self, ws_id: str, workspace: dict[str, Any], month: str | None = None,
                 dataset_id: str | None = None) -> dict[str, Any]:
        ev, meta, i = self._load(ws_id, month, dataset_id)
        led = ev.ledger
        months = led.months
        m = months[i]
        prev = months[i - 1] if i >= 1 else None
        last_year = months[i - 12] if i >= 12 else None
        fy_first = fiscal_start(m, int(workspace.get("fiscal_year_start_month") or 1))
        ytd_months = [x for x in months[: i + 1] if x >= fy_first]
        window = months[max(0, i - 11): i + 1]
        plan = self._plan_lines(ws_id, meta)

        def val(name: str, x: str | None) -> float | None:
            """A measure in a month; total expenses and free cash flow are composed here, the rest come from the ledger."""
            if x is None:
                return None
            if name == "expenses":
                return sum(led.value(r, x) or 0.0 for r in EXPENSE_ROLES)
            if name == "fcf":
                return (led.value("ocf", x) or 0.0) + (led.value("icf", x) or 0.0)
            return led.value(name, x)

        def measure(name: str) -> dict[str, Any]:
            kind = "stock" if name in ("cash", "receivables", "payables", "working_capital") else "flow"
            now, before, yoy = val(name, m), val(name, prev), val(name, last_year)
            out = {"value": _r(now), "previous": _r(before), "change": _pct(now, before),
                   "last_year": _r(yoy), "change_year": _pct(now, yoy), "series": [_r(val(name, x)) for x in window]}
            if kind == "flow":
                out["ytd"] = _r(sum(val(name, x) or 0.0 for x in ytd_months))
            if name in plan:
                out["plan"] = _r(plan[name].get(m))
                out["plan_ytd"] = _r(sum(plan[name].get(x, 0.0) for x in ytd_months if x in plan[name])) if kind == "flow" else None
            return out

        headline = {name: measure(name) for name in ("revenue", "expenses", "net_profit", "cash", "ocf", "fcf", "receivables", "payables")}
        rev3 = sum(val("revenue", x) or 0.0 for x in months[max(0, i - 2): i + 1])
        np3 = sum(val("net_profit", x) or 0.0 for x in months[max(0, i - 2): i + 1])
        headline["net_profit"]["margin"] = round(np3 / rev3, 4) if rev3 else None

        cards = ev.scorecard(m)
        by_id = {c["id"]: c for c in cards}
        health = self._health(cards, headline, by_id)
        risks = self._risks(by_id, meta, health["signals"])
        insights = self._insights(led, months, i, headline, by_id, plan)
        decisions = self._decisions(ws_id, meta)
        return {
            "month": m, "months": months, "ledger": meta["ledger"], "currency": workspace.get("base_currency"),
            "fiscal_year_start": fy_first, "provenance": self._provenance(meta), "plan": meta.get("plan"),
            "health": health, "headline": headline, "trend": {"months": window},
            "kpis": {k: {x: by_id[k].get(x) for x in ("value", "previous", "status", "unit", "target", "warn", "missing")}
                     for k in ("gross_margin", "net_margin", "days_cash", "cash_runway", "dso", "ar_overdue", "current_ratio",
                               "customer_concentration", "plan_variance", "revenue_growth", "collection_rate", "opex_ratio")
                     if k in by_id},
            "risks": risks, "insights": insights, "decisions": decisions,
        }

    def _health(self, cards: list[dict[str, Any]], headline: dict[str, Any], by_id: dict[str, Any]) -> dict[str, Any]:
        """A 0–100 score with its reasons and a one-word label.

        Each signal is judged by the finance lead's target when one is set, otherwise by a reference threshold
        (``HEALTH_SIGNALS``), so the score exists from the first ledger and tightens as targets are set."""
        weights = {"good": 1.0, "watch": 0.5, "act": 0.0}
        signals: list[dict[str, Any]] = []
        for kid, up, good, watch in HEALTH_SIGNALS:
            c = by_id.get(kid)
            if not c or c.get("value") is None:
                continue
            v = c["value"]
            if c.get("status") in weights:
                st, basis = c["status"], "target"
            else:
                ok = v >= good if up else v <= good
                near = v >= watch if up else v <= watch
                st, basis = ("good" if ok else "watch" if near else "act"), "reference"
            signals.append({"kpi": kid, "status": st, "basis": basis, "value": v})
        # every other KPI with a target counts too
        seen = {x["kpi"] for x in signals}
        for c in cards:
            if c["id"] not in seen and c.get("status") in weights:
                signals.append({"kpi": c["id"], "status": c["status"], "basis": "target", "value": c.get("value")})
        reasons: list[dict[str, Any]] = []
        score = round(100 * sum(weights[x["status"]] for x in signals) / len(signals)) if signals else None
        np_ = headline["net_profit"]
        if np_.get("value") is not None and np_["value"] < 0:
            reasons.append({"kind": "loss_month"})
            if score is not None:
                score = min(score, 59)
        days = (by_id.get("days_cash") or {}).get("value")
        if days is not None and days < 30:
            reasons.append({"kind": "cash_thin", "days": round(days)})
            score = min(score if score is not None else 39, 39)
        if headline["ocf"].get("value") is not None and headline["ocf"]["value"] < 0:
            reasons.append({"kind": "cash_out_month"})
        for x in signals:
            if x["status"] == "act":
                reasons.append({"kind": "kpi_act", "kpi": x["kpi"]})
        label = None if score is None else next(name for floor, name in HEALTH_LABELS if score >= floor)
        return {"score": score, "label": label, "kpis_scored": len(signals),
                "good": sum(1 for x in signals if x["status"] == "good"),
                "watch": sum(1 for x in signals if x["status"] == "watch"),
                "act": sum(1 for x in signals if x["status"] == "act"),
                "targets_set": sum(1 for x in signals if x["basis"] == "target"), "signals": signals, "reasons": reasons[:6]}

    def _risks(self, by_id: dict[str, Any], meta: dict[str, Any], signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """KPIs off target (or off their reference threshold), then what the KPIs cannot see."""
        out: list[dict[str, Any]] = []
        judged = {x["kpi"]: x for x in signals}
        for kid in RISK_KPIS:
            c = by_id.get(kid)
            st = judged.get(kid, {}).get("status") or (c or {}).get("status")
            if c and st in ("act", "watch"):
                out.append({"kind": "kpi", "kpi": kid, "level": "high" if st == "act" else "medium",
                            "basis": judged.get(kid, {}).get("basis", "target"),
                            "value": c.get("value"), "target": c.get("target"), "unit": c.get("unit"),
                            "link": f"analysis/kpis/{kid}"})
        # risks the KPIs cannot see
        bal = meta.get("balance") or {}
        if not bal.get("balanced", True):
            out.append({"kind": "unbalanced", "level": "high", "difference": bal.get("difference"), "link": "data"})
        roles = meta.get("roles") or {}
        if roles.get("accounts") and roles.get("confirmed", 0) < roles["accounts"]:
            out.append({"kind": "roles_unconfirmed", "level": "medium", "confirmed": roles["confirmed"],
                        "accounts": roles["accounts"], "link": "analysis/roles"})
        order = {"high": 0, "medium": 1}
        out.sort(key=lambda r: order[r["level"]])
        return out[:8]

    def _insights(self, led: Ledger, months: list[str], i: int, headline: dict[str, Any], by_id: dict[str, Any],
                  plan: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
        """Plain facts from the ledger, each with the figures it rests on. Not a model's opinion."""
        m = months[i]
        out: list[dict[str, Any]] = []
        rev = headline["revenue"]
        if rev.get("change_year") is not None:
            out.append({"kind": "revenue_yoy", "change": rev["change_year"], "value": rev["value"], "last_year": rev["last_year"]})
        elif rev.get("change") is not None:
            out.append({"kind": "revenue_mom", "change": rev["change"], "value": rev["value"], "previous": rev["previous"]})
        gm = by_id.get("gross_margin") or {}
        if gm.get("value") is not None and gm.get("previous") is not None and abs(gm["value"] - gm["previous"]) >= 0.01:
            out.append({"kind": "margin_moved", "value": gm["value"], "previous": gm["previous"]})
        # the expense line that grew most against the previous three months
        if i >= 3:
            best = None
            for role in EXPENSE_ROLES:
                now = led.flows[role].get(m, 0.0)
                base = sum(led.flows[role].get(x, 0.0) for x in months[i - 3: i]) / 3
                if base > 0 and now - base > 0 and (best is None or now - base > best[1]):
                    best = (role, now - base, base, now)
            if best and best[1] / best[2] >= 0.15:
                out.append({"kind": "expense_up", "measure": best[0], "value": _r(best[3]), "average": _r(best[2]),
                            "change": _pct(best[3], best[2]), "link": f"overview/breakdown/{best[0]}"})
        days = (by_id.get("days_cash") or {}).get("value")
        if days is not None:
            out.append({"kind": "days_cash", "days": round(days), "cash": headline["cash"]["value"]})
        ar = by_id.get("ar_overdue") or {}
        if ar.get("value") is not None and ar["value"] > 0:
            out.append({"kind": "ar_overdue", "share": ar["value"], "receivables": headline["receivables"]["value"]})
        conc = by_id.get("customer_concentration") or {}
        if conc.get("value") is not None:
            out.append({"kind": "concentration", "share": conc["value"]})
        if "revenue" in plan and plan["revenue"].get(m) and rev.get("value") is not None:
            out.append({"kind": "plan_variance", "value": rev["value"], "plan": _r(plan["revenue"][m]),
                        "change": _pct(rev["value"], plan["revenue"][m])})
        return out[:6]

    def _decisions(self, ws_id: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
        """What is waiting for a person: approvals, forecasts to sign off, open close items, budgets running out."""
        db = self.engine.db
        out: list[dict[str, Any]] = []
        for r in db.all("SELECT id, summary, created_at FROM approvals WHERE workspace_id = ? AND status = 'pending' "
                        "ORDER BY created_at DESC LIMIT 5", (ws_id,)):
            out.append({"kind": "approval", "id": r["id"], "title": r["summary"], "at": r["created_at"], "link": "inbox"})
        for r in db.all("SELECT id, name, scenario, version, updated_at FROM forecasts WHERE workspace_id = ? AND "
                        "status = 'submitted' ORDER BY updated_at DESC LIMIT 5", (ws_id,)):
            out.append({"kind": "forecast", "id": r["id"], "title": r["name"], "scenario": r["scenario"], "version": r["version"],
                        "at": r["updated_at"], "link": f"analysis/forecasts/{r['id']}"})
        for r in db.all("SELECT id, month, result FROM close_runs WHERE workspace_id = ? AND status = 'open' ORDER BY month DESC "
                        "LIMIT 3", (ws_id,)):
            res = loads(r["result"], {}) or {}
            ready = res.get("readiness") or {}
            out.append({"kind": "close", "id": r["id"], "month": r["month"], "open_items": ready.get("open_items"),
                        "score": ready.get("score"), "link": f"close/{r['id']}"})
        return out

    async def get(self, ws_id: str, workspace: dict[str, Any], month: str | None = None,
                  dataset_id: str | None = None) -> dict[str, Any]:
        """The overview plus the decisions that need the cost-centre reports (budgets running out early)."""
        ov = await asyncio.to_thread(self.overview, ws_id, workspace, month, dataset_id)
        today = service_today()
        try:
            summaries = await self.engine.costcentres.summaries(ws_id)
        except Exception:  # noqa: BLE001 - cost centres must not hide the overview
            summaries = []
        for cc in summaries:
            runout = cc.get("run_out_date")
            if cc.get("status") == "active" and runout and cc.get("end_date") and runout < cc["end_date"] and runout >= today:
                ov["decisions"].append({"kind": "budget", "id": cc["id"], "title": cc["name"], "runout": runout,
                                        "end": cc["end_date"], "link": f"costcentres/{cc['id']}"})
            elif cc.get("status") == "active" and cc.get("exceeded_on"):
                ov["decisions"].append({"kind": "budget_exceeded", "id": cc["id"], "title": cc["name"],
                                        "exceeded_on": cc["exceeded_on"], "link": f"costcentres/{cc['id']}"})
        ov["decisions"] = ov["decisions"][:10]
        return ov

    # -- drill-down ----------------------------------------------------------------------------------------------------
    def profitability(self, ws_id: str, workspace: dict[str, Any], month: str | None = None) -> dict[str, Any]:
        ev, meta, i = self._load(ws_id, month)
        led = ev.ledger
        months = led.months
        m = months[i]
        prev = months[i - 1] if i >= 1 else None
        last_year = months[i - 12] if i >= 12 else None
        fy_first = fiscal_start(m, int(workspace.get("fiscal_year_start_month") or 1))
        ytd = [x for x in months[: i + 1] if x >= fy_first]
        plan = self._plan_lines(ws_id, meta)
        lines = []
        for name, kind, drill in PL_LINES:
            v = led.value(name, m) or 0.0
            lines.append({"measure": name, "kind": kind, "drillable": drill, "value": _r(v),
                          "previous": _r(led.value(name, prev)) if prev else None,
                          "last_year": _r(led.value(name, last_year)) if last_year else None,
                          "ytd": _r(self._sum(led, name, ytd)),
                          "plan": _r(plan[name].get(m)) if name in plan else None,
                          "plan_ytd": _r(sum(plan[name].get(x, 0.0) for x in ytd)) if name in plan else None,
                          "accounts": len(led.by_account.get(name, {}).get(m, {})) if drill else 0})
        rev = led.value("revenue", m) or 0.0
        return {"month": m, "months": months, "previous": prev, "last_year": last_year, "fiscal_year_start": fy_first,
                "currency": workspace.get("base_currency"), "provenance": self._provenance(meta), "plan": meta.get("plan"),
                "lines": lines, "margins": {ln["measure"]: (round(ln["value"] / rev, 4) if rev else None) for ln in lines
                                            if ln["kind"] in ("subtotal", "total")}}

    def breakdown(self, ws_id: str, workspace: dict[str, Any], measure: str, month: str | None = None) -> dict[str, Any]:
        if measure not in PL_ROLES:
            raise NotFound("That line has no accounts behind it.")
        ev, meta, i = self._load(ws_id, month)
        led = ev.ledger
        months = led.months
        m = months[i]
        prev = months[i - 1] if i >= 1 else None
        rows = led.by_account.get(measure, {}).get(m, {})
        before = led.by_account.get(measure, {}).get(prev, {}) if prev else {}
        total = sum(rows.values())
        items = []
        for key, v in sorted(rows.items(), key=lambda kv: -abs(kv[1])):
            if abs(v) < 0.005 and abs(before.get(key, 0.0)) < 0.005:
                continue
            items.append({"key": key, "account": led.names.get(key, key), "value": _r(v), "previous": _r(before.get(key)) if prev else None,
                          "change": _pct(v, before.get(key)) if prev else None,
                          "share": round(v / total, 4) if total else None,
                          "lines": sum(1 for ln in led.lines if ln["date"][:7] == m and account_key(ln["account_code"], ln["account"]) == key)})
        return {"month": m, "months": months, "measure": measure, "currency": workspace.get("base_currency"),
                "provenance": self._provenance(meta), "total": _r(total),
                "previous_total": _r(sum(before.values())) if prev else None, "items": items}

    def transactions(self, ws_id: str, workspace: dict[str, Any], measure: str, key: str, month: str | None = None,
                     limit: int = 500) -> dict[str, Any]:
        ev, meta, i = self._load(ws_id, month)
        led = ev.ledger
        m = led.months[i]
        income = measure in ("revenue", "other_income")
        rows = []
        total = 0.0
        for ln in led.lines:
            if ln["date"][:7] != m or account_key(ln["account_code"], ln["account"]) != key:
                continue
            amt = ln["debit"] - ln["credit"]
            v = -amt if income else amt
            total += v
            rows.append({"row": ln.get("row"), "date": ln["date"], "entry": ln["entry"], "type": ln.get("type"),
                         "description": ln.get("description"), "party": ln.get("party"), "debit": _r(ln["debit"]),
                         "credit": _r(ln["credit"]), "amount": _r(v)})
        rows.sort(key=lambda r: (r["date"], r["entry"], r["row"] or 0))
        return {"month": m, "months": led.months, "measure": measure, "key": key, "account": led.names.get(key, key),
                "currency": workspace.get("base_currency"), "provenance": self._provenance(meta), "total": _r(total),
                "count": len(rows), "rows": rows[:limit]}

    def entry(self, ws_id: str, workspace: dict[str, Any], entry: str, month: str | None = None) -> dict[str, Any]:
        ev, meta, _ = self._load(ws_id, month)
        led = ev.ledger
        lines = [ln for ln in led.lines if ln["entry"] == entry]
        if not lines:
            raise NotFound("That journal entry was not found in the ledger.")
        did = meta["ledger"]["id"]
        audit = self.engine.db.all("SELECT a.action, a.detail, a.created_at, u.name AS user_name FROM audit_log a LEFT JOIN users u "
                                   "ON u.id = a.user_id WHERE a.workspace_id = ? AND a.target = ? ORDER BY a.created_at DESC LIMIT 20",
                                   (ws_id, did))
        ds = self.engine.db.one("SELECT id, name, source, refreshed_at, row_count FROM datasets WHERE id = ?", (did,))
        return {"entry": entry, "date": min(ln["date"] for ln in lines), "type": lines[0].get("type"),
                "currency": workspace.get("base_currency"), "provenance": self._provenance(meta),
                "lines": [{"row": ln.get("row"), "date": ln["date"], "account_code": ln["account_code"], "account": ln["account"],
                           "role": led.role_of(ln), "debit": _r(ln["debit"]), "credit": _r(ln["credit"]),
                           "description": ln.get("description"), "party": ln.get("party"), "tags": ln.get("tags")} for ln in lines],
                "balanced": abs(sum(ln["debit"] - ln["credit"] for ln in lines)) < 0.005,
                "source": {"dataset_id": did, "name": ds["name"] if ds else None, "kind": ds["source"] if ds else None,
                           "refreshed_at": ds["refreshed_at"] if ds else None, "rows": ds["row_count"] if ds else None},
                "audit": [{"action": a["action"], "by": a["user_name"], "at": a["created_at"], "detail": loads(a["detail"], None)}
                          for a in audit],
                "documents": []}

    # -- AI insights ------------------------------------------------------------------------------------------------------
    async def ai_insights(self, ws_id: str, workspace: dict[str, Any], month: str | None, language: str) -> dict[str, Any]:
        """Three to five short observations written by the workspace's model from the overview figures only.
        Always labelled as an estimate; never stored as a figure."""
        from ..llm.base import ChatMessage
        from ..llm.registry import build_model, resolve_choice

        ov = await self.get(ws_id, workspace, month)
        facts = {k: {x: v.get(x) for x in ("value", "previous", "last_year", "ytd", "plan") if v.get(x) is not None}
                 for k, v in ov["headline"].items()}
        payload = {"month": ov["month"], "currency": ov["currency"], "health": ov["health"], "figures": facts,
                   "kpis": ov["kpis"], "risks": ov["risks"], "facts": ov["insights"], "provenance": ov["provenance"]["kind"]}
        pid, model_name = resolve_choice(self.engine, workspace, workspace.get("utility_provider_id"), workspace.get("utility_model"))
        llm = build_model(self.engine, ws_id, pid, model_name)
        result = None
        async for ev in llm.stream([ChatMessage("system", INSIGHTS_PROMPT.format(language=language)),
                                    ChatMessage("user", "Figures (JSON):\n" + _json(payload))], []):
            if ev.type == "done":
                result = ev.result
        assert result is not None
        self.engine.record_usage(ws_id, pid, model_name, result.usage, "overview")
        text = result.text.strip()
        items = [ln.lstrip("-•* ").strip() for ln in text.splitlines() if ln.strip()]
        items = [x for x in items if len(x) > 8][:5]
        return {"month": ov["month"], "model": model_name, "provenance": "estimate", "items": items}


INSIGHTS_PROMPT = """You are writing for the chief executive of a company, who has no accounting background.
From the figures given, write three to five observations, one per line, each one sentence in plain business
language in {language}. Use only the figures given; do not invent numbers, do not give accounting jargon, and say
what management might look at next where the figures justify it. Every line starts with "- ". No heading, no
closing remark."""


def _json(x: Any) -> str:
    import json

    return json.dumps(x, ensure_ascii=False, default=str)
