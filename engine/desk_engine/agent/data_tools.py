"""Agent tools over the workspace's datasets and dashboards."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..data import store
from ..data.dashboards import DashboardIn, TEMPLATES, Widget, save_dashboard, template_widgets
from ..data.query import QuerySpec, dataset_summary, run_query
from .tools import ToolContext, ToolOutput, registry, schema, table_block, table_text

QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "dataset": {"type": "string", "description": "dataset name or id"},
        "measures": {"type": "array", "items": {"type": "object", "properties": {
            "column": {"type": "string"}, "agg": {"type": "string",
                                                  "enum": ["sum", "avg", "min", "max", "count", "count_distinct"]},
            "label": {"type": "string"}}}},
        "group_by": {"type": "array", "items": {"type": "object", "properties": {
            "column": {"type": "string"},
            "grain": {"type": "string", "enum": ["day", "week", "month", "quarter", "year", "fiscal_year"]}},
            "required": ["column"]}},
        "filters": {"type": "array", "items": {"type": "object", "properties": {
            "column": {"type": "string"},
            "op": {"type": "string", "enum": ["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "contains",
                                              "between", "is_null", "not_null", "last_n_months",
                                              "this_fiscal_year"]},
            "value": {}}, "required": ["column", "op"]}},
        "sort": {"type": "array", "items": {"type": "object", "properties": {
            "by": {"type": "string"}, "dir": {"type": "string", "enum": ["asc", "desc"]}}, "required": ["by"]}},
        "limit": {"type": "integer"},
    },
    "required": ["dataset"],
}


@registry.tool("list_datasets", "List the workspace's datasets (imported spreadsheets and synced accounting data) "
               "with their columns.", schema({}), category="data")
async def _list(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    items = store.list_datasets(ctx.engine, ctx.ws_id)
    if not items:
        return ToolOutput("There are no datasets yet. The user can import an Excel or CSV file under Data, or sync "
                          "QuickBooks, Zoho Books, Xero or Odoo, or link another system, under Connections.")
    lines = [f"- {d['name']} (source {d['source']}, {d['row_count']} rows): "
             + ", ".join(f"{c['name']} [{c['type']}]" for c in d["columns"]) for d in items]
    return ToolOutput("\n".join(lines), [], [store.public(d) for d in items])


@registry.tool("describe_dataset", "Column statistics and the most frequent values of a dataset; use before "
               "querying an unfamiliar dataset.", schema({"dataset": {"type": "string"}}, ["dataset"]),
               category="data")
async def _describe(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    ds = store.find_dataset(ctx.engine, ctx.ws_id, a["dataset"])
    summary = dataset_summary(ctx.engine, ctx.ws_id, ds["id"])
    lines = [f"Dataset {summary['dataset']}: {summary['row_count']} rows"]
    for c in summary["columns"]:
        if c["type"] == "number":
            lines.append(f"- {c['name']} (number): min {c.get('mn')}, max {c.get('mx')}, total {c.get('total')}")
        elif c["type"] == "date":
            lines.append(f"- {c['name']} (date): {c.get('mn')} to {c.get('mx')}")
        else:
            lines.append(f"- {c['name']} (text): {c.get('distinct_values')} distinct, e.g. "
                         + ", ".join(str(v) for v in c.get("top_values", [])[:8]))
    return ToolOutput("\n".join(lines), [], summary)


@registry.tool("query_dataset", "Aggregate a dataset: measures (sum/avg/...), optional grouping (dates by month, "
               "quarter, year or fiscal_year), filters and sort. Returns a table.", QUERY_SCHEMA, category="data")
async def _query(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        spec = QuerySpec.model_validate(a)
    except ValidationError as exc:
        return ToolOutput(f"Invalid query: {exc.errors(include_url=False)[:3]}")
    result = run_query(ctx.engine, ctx.workspace, spec)
    text = table_text(result["columns"], result["rows"])
    return ToolOutput(text, [table_block(result["columns"], result["rows"], spec.dataset)], result)


@registry.tool("create_dashboard", "Create a dashboard in the workspace. Either pass a template "
               f"({', '.join(TEMPLATES)}) with a dataset, or explicit widgets. Widget types: kpi, line, bar, "
               "stacked_bar, area, table; each widget has a title and a query like query_dataset's.",
               schema({"name": {"type": "string"}, "description": {"type": "string"},
                       "template": {"type": "string", "enum": list(TEMPLATES)},
                       "dataset": {"type": "string"},
                       "widgets": {"type": "array", "items": {"type": "object", "properties": {
                           "title": {"type": "string"},
                           "type": {"type": "string", "enum": ["kpi", "line", "bar", "stacked_bar", "area", "table"]},
                           "width": {"type": "integer", "minimum": 1, "maximum": 3},
                           "query": QUERY_SCHEMA}, "required": ["title", "type", "query"]}}},
                      ["name"]), category="data",
               summarize=lambda a: f"Create dashboard '{a.get('name')}'")
async def _create_dashboard(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    widgets: list[Widget] = []
    if a.get("template"):
        if not a.get("dataset"):
            return ToolOutput("A template needs a dataset.")
        ds = store.find_dataset(ctx.engine, ctx.ws_id, a["dataset"])
        widgets = template_widgets(a["template"], ds)
    for w in a.get("widgets") or []:
        try:
            widgets.append(Widget.model_validate(w))
        except ValidationError as exc:
            return ToolOutput(f"Invalid widget '{w.get('title')}': {exc.errors(include_url=False)[:2]}")
    if not widgets:
        return ToolOutput("Give a template and dataset, or at least one widget.")
    # validate every query before saving
    for w in widgets:
        run_query(ctx.engine, ctx.workspace, w.query)
    dash = save_dashboard(ctx.engine, ctx.ws_id, DashboardIn(name=a["name"], description=a.get("description") or "",
                                                            widgets=widgets), user_id=ctx.user_id)
    ctx.engine.audit(ctx.ws_id, ctx.user_id, "dashboard.create", dash["id"], {"via": ctx.source})
    return ToolOutput(f"Created dashboard '{dash['name']}' with {len(widgets)} widgets (id {dash['id']}).",
                      [{"type": "link", "kind": "dashboard", "id": dash["id"], "label": dash["name"]}],
                      {"dashboard_id": dash["id"]})
