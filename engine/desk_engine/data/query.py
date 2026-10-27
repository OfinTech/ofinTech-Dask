"""Compiles a declarative query spec (used by dashboard widgets and by the agent) into a
parameterised SQL query. Only columns that exist in the dataset can be referenced, every value is
a bound parameter, and the model never writes SQL."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ..errors import Invalid
from .store import get_dataset, table_name

if TYPE_CHECKING:
    from ..app import Engine

Agg = Literal["sum", "avg", "min", "max", "count", "count_distinct"]
Grain = Literal["day", "week", "month", "quarter", "year", "fiscal_year"]
Op = Literal["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "contains", "between", "is_null", "not_null",
             "last_n_months", "this_fiscal_year"]


class Measure(BaseModel):
    column: str | None = None  # None with agg=count → COUNT(*)
    agg: Agg = "sum"
    label: str | None = None


class GroupBy(BaseModel):
    column: str
    grain: Grain | None = None


class Filter(BaseModel):
    column: str
    op: Op = "eq"
    value: Any = None


class Sort(BaseModel):
    by: str
    dir: Literal["asc", "desc"] = "desc"


class QuerySpec(BaseModel):
    dataset: str
    measures: list[Measure] = Field(default_factory=lambda: [Measure(agg="count")])
    group_by: list[GroupBy] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    sort: list[Sort] = Field(default_factory=list)
    limit: int = Field(default=1000, ge=1, le=10000)


SQL_AGG = {"sum": "SUM({})", "avg": "AVG({})", "min": "MIN({})", "max": "MAX({})", "count": "COUNT({})",
           "count_distinct": "COUNT(DISTINCT {})"}


def _grain_sql(col: str, grain: str | None, fy_start: int) -> str:
    if grain is None:
        return col
    if grain == "day":
        return f"substr({col}, 1, 10)"
    if grain == "week":
        return f"strftime('%Y-W%W', {col})"
    if grain == "month":
        return f"substr({col}, 1, 7)"
    if grain == "quarter":
        return f"(substr({col}, 1, 4) || '-Q' || ((CAST(substr({col}, 6, 2) AS INTEGER) + 2) / 3))"
    if grain == "year":
        return f"substr({col}, 1, 4)"
    # fiscal year named by the calendar year in which it ends
    if fy_start == 1:
        return f"('FY' || substr({col}, 1, 4))"
    return (f"('FY' || (CAST(substr({col}, 1, 4) AS INTEGER) + "
            f"CASE WHEN CAST(substr({col}, 6, 2) AS INTEGER) >= {int(fy_start)} THEN 1 ELSE 0 END))")


class Compiled:
    def __init__(self, sql: str, params: list[Any], columns: list[dict[str, str]]):
        self.sql = sql
        self.params = params
        self.columns = columns


def _where(ds: dict[str, Any], col: Any, filters: list[Filter], fy_start: int) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    for f in filters:
        c = col(f.column)
        k = c["key"]
        v = f.value
        if f.op == "eq":
            where.append(f"{k} = ?")
            params.append(v)
        elif f.op == "ne":
            where.append(f"({k} IS NULL OR {k} != ?)")
            params.append(v)
        elif f.op in {"in", "not_in"}:
            values = v if isinstance(v, list) else [v]
            if not values:
                where.append("0" if f.op == "in" else "1")
                continue
            values = values[:500]
            where.append(f"{k} {'NOT ' if f.op == 'not_in' else ''}IN ({', '.join('?' for _ in values)})")
            params.extend(values)
        elif f.op in {"gt", "gte", "lt", "lte"}:
            where.append(f"{k} {dict(gt='>', gte='>=', lt='<', lte='<=')[f.op]} ?")
            params.append(v)
        elif f.op == "contains":
            where.append(f"{k} LIKE ? ESCAPE '\\'")
            params.append("%" + str(v).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        elif f.op == "between":
            if not isinstance(v, list) or len(v) != 2:
                raise Invalid("between needs a list of two values.")
            where.append(f"{k} BETWEEN ? AND ?")
            params.extend(v)
        elif f.op == "is_null":
            where.append(f"{k} IS NULL")
        elif f.op == "not_null":
            where.append(f"{k} IS NOT NULL")
        elif f.op == "last_n_months":
            if c["type"] != "date":
                raise Invalid("last_n_months needs a date column.")
            n = int(v or 12)
            where.append(f"{k} >= date((SELECT MAX({k}) FROM {table_name(ds['id'])}), 'start of month', ?)")
            params.append(f"-{max(n - 1, 0)} months")
        elif f.op == "this_fiscal_year":
            if c["type"] != "date":
                raise Invalid("this_fiscal_year needs a date column.")
            fy_expr = _grain_sql(k, "fiscal_year", fy_start)
            latest = _grain_sql(f"(SELECT MAX({k}) FROM {table_name(ds['id'])})", "fiscal_year", fy_start)
            where.append(f"{fy_expr} = {latest}")
    return where, params


def compile_query(engine: Engine, workspace: dict[str, Any], spec: QuerySpec) -> Compiled:
    from .store import find_dataset

    ds = find_dataset(engine, workspace["id"], spec.dataset)
    cols = ds["columns"]
    by_name = {c["name"].lower(): c for c in cols} | {c["key"]: c for c in cols}

    def col(name: str) -> dict[str, str]:
        c = by_name.get(name.lower()) or by_name.get(name)
        if c is None:
            raise Invalid(f"Dataset '{ds['name']}' has no column '{name}'. Columns: "
                          + ", ".join(x["name"] for x in cols))
        return c

    fy_start = int(workspace.get("fiscal_year_start_month") or 1)
    select: list[str] = []
    out_cols: list[dict[str, str]] = []
    group_exprs: list[str] = []
    for i, g in enumerate(spec.group_by):
        c = col(g.column)
        if g.grain and c["type"] != "date":
            raise Invalid(f"'{c['name']}' is not a date column, so it cannot be grouped by {g.grain}.")
        expr = _grain_sql(c["key"], g.grain, fy_start)
        select.append(f"{expr} AS g{i}")
        group_exprs.append(f"g{i}")
        out_cols.append({"key": f"g{i}", "label": c["name"] + (f" ({g.grain.replace('_', ' ')})" if g.grain else ""),
                         "format": "text" if g.grain else ("date" if c["type"] == "date" else
                                                           "number" if c["type"] == "number" else "text")})
    for i, m in enumerate(spec.measures):
        if m.column is None:
            if m.agg != "count":
                raise Invalid("Only count can be used without a column.")
            expr = "COUNT(*)"
            label = m.label or "Count"
        else:
            c = col(m.column)
            if m.agg in {"sum", "avg"} and c["type"] != "number":
                raise Invalid(f"'{c['name']}' is not numeric, so it cannot be summed or averaged.")
            expr = SQL_AGG[m.agg].format(c["key"])
            label = m.label or (f"{m.agg.replace('_', ' ').title()} of {c['name']}" if m.agg != "sum" else c["name"])
        select.append(f"{expr} AS m{i}")
        out_cols.append({"key": f"m{i}", "label": label,
                         "format": "number" if m.agg in {"count", "count_distinct"} else "money"})

    where, params = _where(ds, col, spec.filters, fy_start)

    sql = f"SELECT {', '.join(select)} FROM {table_name(ds['id'])}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group_exprs:
        sql += " GROUP BY " + ", ".join(group_exprs)
    order: list[str] = []
    for s in spec.sort:
        target = None
        for oc in out_cols:
            if s.by in (oc["key"], oc["label"]) or s.by.lower() == oc["label"].lower():
                target = oc["key"]
        if target is None:
            try:
                idx = [g.column.lower() for g in spec.group_by].index(s.by.lower())
                target = f"g{idx}"
            except ValueError:
                idx_m = [(m.column or "").lower() for m in spec.measures]
                if s.by.lower() in idx_m:
                    target = f"m{idx_m.index(s.by.lower())}"
        if target is None:
            raise Invalid(f"Cannot sort by '{s.by}': sort by a grouped column or a measure.")
        order.append(f"{target} {s.dir.upper()}")
    if not order and group_exprs:
        order = [f"{g} ASC" for g in group_exprs]
    if order:
        sql += " ORDER BY " + ", ".join(order)
    sql += " LIMIT ?"
    params.append(spec.limit)
    return Compiled(sql, params, out_cols)


def run_query(engine: Engine, workspace: dict[str, Any], spec: QuerySpec) -> dict[str, Any]:
    compiled = compile_query(engine, workspace, spec)
    cur = engine.db.conn.execute(compiled.sql, compiled.params)
    rows = [list(r) for r in cur.fetchall()]
    return {"columns": compiled.columns, "rows": rows}


def detail_rows(engine: Engine, workspace: dict[str, Any], dataset: str, filters: list[Filter],
                limit: int = 200) -> dict[str, Any]:
    """The underlying rows behind an aggregated figure (drill-down)."""
    from .store import find_dataset

    ds = find_dataset(engine, workspace["id"], dataset)
    by_name = {c["name"].lower(): c for c in ds["columns"]} | {c["key"]: c for c in ds["columns"]}

    def col(name: str) -> dict[str, str]:
        c = by_name.get(name.lower()) or by_name.get(name)
        if c is None:
            raise Invalid(f"Dataset '{ds['name']}' has no column '{name}'.")
        return c

    where, params = _where(ds, col, filters, int(workspace.get("fiscal_year_start_month") or 1))
    keys = ", ".join(c["key"] for c in ds["columns"])
    sql = f"SELECT {keys} FROM {table_name(ds['id'])}" + (" WHERE " + " AND ".join(where) if where else "")
    total = engine.db.scalar(f"SELECT COUNT(*) FROM ({sql})", params)
    rows = [list(r) for r in engine.db.conn.execute(sql + " LIMIT ?", [*params, limit]).fetchall()]
    return {"dataset": {"id": ds["id"], "name": ds["name"]},
            "columns": [{"key": c["key"], "label": c["name"], "format": c["type"]} for c in ds["columns"]],
            "rows": rows, "total_rows": total}


def dataset_summary(engine: Engine, ws_id: str, dataset_id: str) -> dict[str, Any]:
    ds = get_dataset(engine, ws_id, dataset_id)
    tname = table_name(ds["id"])
    stats = []
    for c in ds["columns"]:
        k = c["key"]
        if c["type"] == "number":
            r = engine.db.one(f"SELECT MIN({k}) AS mn, MAX({k}) AS mx, SUM({k}) AS total, COUNT({k}) AS n FROM {tname}")
        elif c["type"] == "date":
            r = engine.db.one(f"SELECT MIN({k}) AS mn, MAX({k}) AS mx, COUNT({k}) AS n FROM {tname}")
        else:
            r = engine.db.one(f"SELECT COUNT(DISTINCT {k}) AS distinct_values, COUNT({k}) AS n FROM {tname}")
            top = engine.db.all(f"SELECT {k} AS v, COUNT(*) AS n FROM {tname} WHERE {k} IS NOT NULL "
                                f"GROUP BY {k} ORDER BY n DESC LIMIT 8")
            r = (r or {}) | {"top_values": [t["v"] for t in top]}
        stats.append({"name": c["name"], "type": c["type"], **(r or {})})
    return {"dataset": ds["name"], "row_count": ds["row_count"], "columns": stats}
