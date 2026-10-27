"""Dashboards: a list of widgets, each a chart type plus a query spec over one dataset."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ..db import dumps, loads, new_id, now
from ..errors import AppError, Invalid, NotFound
from .query import QuerySpec, run_query

if TYPE_CHECKING:
    from ..app import Engine

WidgetType = Literal["kpi", "line", "bar", "stacked_bar", "area", "table"]


class Widget(BaseModel):
    id: str = Field(default_factory=lambda: new_id("w_"))
    title: str
    type: WidgetType
    query: QuerySpec
    width: int = Field(default=1, ge=1, le=3)
    note: str | None = None


class DashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    widgets: list[Widget] = Field(default_factory=list, max_length=40)


def save_dashboard(engine: Engine, ws_id: str, data: DashboardIn, *, user_id: str | None,
                   dashboard_id: str | None = None) -> dict[str, Any]:
    widgets = [w.model_dump() for w in data.widgets]
    ts = now()
    if dashboard_id:
        n = engine.db.update("dashboards", dashboard_id, {"name": data.name, "description": data.description,
                                                         "widgets": dumps(widgets), "updated_at": ts},
                             workspace_id=ws_id)
        if not n:
            raise NotFound("Dashboard not found.")
        return get_dashboard(engine, ws_id, dashboard_id)
    did = new_id("db_")
    engine.db.insert("dashboards", {"id": did, "workspace_id": ws_id, "name": data.name,
                                    "description": data.description, "widgets": dumps(widgets),
                                    "created_by": user_id, "created_at": ts, "updated_at": ts})
    return get_dashboard(engine, ws_id, did)


def get_dashboard(engine: Engine, ws_id: str, dashboard_id: str) -> dict[str, Any]:
    row = engine.db.one("SELECT * FROM dashboards WHERE id = ? AND workspace_id = ?", (dashboard_id, ws_id))
    if row is None:
        raise NotFound("Dashboard not found.")
    row["widgets"] = loads(row["widgets"], [])
    row["filters"] = loads(row["filters"], {})
    return row


def render_dashboard(engine: Engine, workspace: dict[str, Any], dashboard_id: str) -> dict[str, Any]:
    dash = get_dashboard(engine, workspace["id"], dashboard_id)
    out = []
    for w in dash["widgets"]:
        try:
            result = run_query(engine, workspace, QuerySpec.model_validate(w["query"]))
            out.append({**w, "data": result})
        except AppError as exc:
            out.append({**w, "error": exc.message})
    return {**dash, "widgets": out, "currency": workspace.get("base_currency")}


# --- templates ---------------------------------------------------------------------------------


def _w(title: str, typ: str, dataset: str, *, measures: list[dict[str, Any]], group_by: list[dict[str, Any]] | None = None,
       filters: list[dict[str, Any]] | None = None, sort: list[dict[str, Any]] | None = None, limit: int = 1000,
       width: int = 1) -> Widget:
    return Widget(title=title, type=typ, width=width, query=QuerySpec(
        dataset=dataset, measures=measures, group_by=group_by or [], filters=filters or [], sort=sort or [],
        limit=limit))


TEMPLATES: dict[str, dict[str, str]] = {
    "profit_and_loss": {"label": "Profit and loss overview", "needs": "profit_and_loss"},
    "receivables": {"label": "Receivables and collections", "needs": "invoices"},
    "payables": {"label": "Payables", "needs": "bills"},
    "dataset_overview": {"label": "Dataset overview", "needs": "any"},
}


def template_widgets(template: str, dataset: dict[str, Any]) -> list[Widget]:
    name = dataset["name"]
    cols = {c["name"].lower(): c for c in dataset["columns"]}
    if template == "profit_and_loss":
        for need in ("period", "section", "amount"):
            if need not in cols:
                raise Invalid("This template needs a dataset with Period, Section, Account and Amount columns "
                              "(created by a QuickBooks, Zoho Books or Xero sync).")
        amt = [{"column": "Amount", "agg": "sum"}]
        return [
            _w("Revenue (last 12 months)", "kpi", name, measures=[{"column": "Amount", "agg": "sum", "label": "Revenue"}],
               filters=[{"column": "Section", "op": "in", "value": ["Income", "Revenue"]},
                        {"column": "Period", "op": "last_n_months", "value": 12}]),
            _w("Expenses (last 12 months)", "kpi", name,
               measures=[{"column": "Amount", "agg": "sum", "label": "Expenses"}],
               filters=[{"column": "Section", "op": "in", "value": ["Cost of sales", "Expenses", "Other expenses"]},
                        {"column": "Period", "op": "last_n_months", "value": 12}]),
            _w("Net profit (last 12 months)", "kpi", name,
               measures=[{"column": "Amount", "agg": "sum", "label": "Net profit"}],
               filters=[{"column": "Section", "op": "eq", "value": "Net profit"},
                        {"column": "Period", "op": "last_n_months", "value": 12}]),
            _w("Revenue and expenses by month", "stacked_bar", name, measures=amt, width=2,
               group_by=[{"column": "Period", "grain": "month"}, {"column": "Section"}],
               filters=[{"column": "Section", "op": "in", "value": ["Income", "Cost of sales", "Expenses"]},
                        {"column": "Period", "op": "last_n_months", "value": 12}]),
            _w("Largest expense accounts", "bar", name, measures=amt, group_by=[{"column": "Account"}],
               filters=[{"column": "Section", "op": "in", "value": ["Cost of sales", "Expenses"]},
                        {"column": "Period", "op": "last_n_months", "value": 12}],
               sort=[{"by": "Amount", "dir": "desc"}], limit=10),
            _w("Net profit by quarter", "line", name, measures=amt, width=3,
               group_by=[{"column": "Period", "grain": "quarter"}],
               filters=[{"column": "Section", "op": "eq", "value": "Net profit"}]),
        ]
    if template in {"receivables", "payables"}:
        party = "Customer" if template == "receivables" else "Vendor"
        for need in ("balance", "aging bucket", party.lower()):
            if need not in cols:
                raise Invalid(f"This template needs an invoices/bills dataset with {party}, Balance and Aging bucket "
                              "columns (created by a connector sync).")
        bal = [{"column": "Balance", "agg": "sum"}]
        open_f = [{"column": "Balance", "op": "gt", "value": 0}]
        return [
            _w("Outstanding", "kpi", name, measures=[{"column": "Balance", "agg": "sum", "label": "Outstanding"}],
               filters=open_f),
            _w("Overdue", "kpi", name, measures=[{"column": "Balance", "agg": "sum", "label": "Overdue"}],
               filters=[*open_f, {"column": "Days overdue", "op": "gt", "value": 0}]),
            _w("Open documents", "kpi", name, measures=[{"agg": "count", "label": "Open"}], filters=open_f),
            _w("Aging", "bar", name, measures=bal, group_by=[{"column": "Aging bucket"}], filters=open_f,
               sort=[{"by": "Aging bucket", "dir": "asc"}], width=2),
            _w(f"Largest balances by {party.lower()}", "table", name, measures=bal, group_by=[{"column": party}],
               filters=open_f, sort=[{"by": "Balance", "dir": "desc"}], limit=15),
        ]
    if template == "dataset_overview":
        widgets: list[Widget] = []
        nums = [c for c in dataset["columns"] if c["type"] == "number"]
        dates = [c for c in dataset["columns"] if c["type"] == "date"]
        texts = [c for c in dataset["columns"] if c["type"] == "text"]
        widgets.append(_w("Rows", "kpi", name, measures=[{"agg": "count", "label": "Rows"}]))
        for c in nums[:2]:
            widgets.append(_w(f"Total {c['name']}", "kpi", name, measures=[{"column": c["name"], "agg": "sum"}]))
        if nums and dates:
            widgets.append(_w(f"{nums[0]['name']} by month", "line", name, width=3,
                              measures=[{"column": nums[0]["name"], "agg": "sum"}],
                              group_by=[{"column": dates[0]["name"], "grain": "month"}]))
        if nums and texts:
            widgets.append(_w(f"{nums[0]['name']} by {texts[0]['name']}", "bar", name, width=2,
                              measures=[{"column": nums[0]["name"], "agg": "sum"}],
                              group_by=[{"column": texts[0]["name"]}],
                              sort=[{"by": nums[0]["name"], "dir": "desc"}], limit=12))
        return widgets
    raise Invalid(f"Unknown template: {template}")
