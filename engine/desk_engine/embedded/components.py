"""Generative-UI component registry (server side).

The model never produces UI. Tools return typed ``blocks`` (kpis, table, sources); this module turns a block into
zero or more component payloads by *structure* and validates each against the component's versioned schema. The
React SDK carries the same schemas (``sdk/embedded-agent/schemas/v1``) and validates again before rendering. A
block that matches no component is still delivered as the tool's text; nothing is dropped silently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..agent.tools import validate_args

VERSION = 1

_STR = {"type": "string", "maxLength": 200}
_NUM_OR_NULL = {"type": ["number", "null"]}
_FORMAT = {"type": "string", "enum": ["text", "number", "money", "percent", "date"]}
_COLUMN = {"type": "object", "properties": {"key": _STR, "label": _STR, "format": _FORMAT}, "required": ["key", "label"],
           "additionalProperties": False}
_CELL = {"type": ["string", "number", "boolean", "null"]}
_ROWS = {"type": "array", "maxItems": 500, "items": {"type": "array", "maxItems": 40, "items": _CELL}}
_LABELS = {"type": "object", "properties": {"en": _STR, "ar": _STR}, "required": ["en"], "additionalProperties": False}


def _schema(component: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://desk.ofintech.ai/schemas/embedded/v{VERSION}/{component}.json",
            "title": component, "type": "object", "additionalProperties": False,
            "properties": {"version": {"type": "integer", "enum": [VERSION]}, **properties},
            "required": ["version", *required]}


SCHEMAS: dict[str, dict[str, Any]] = {
    "FinancialKpiCard": _schema("FinancialKpiCard", {
        "label": _STR, "value": _NUM_OR_NULL, "format": _FORMAT, "currency": {"type": "string", "maxLength": 3},
        "period": _STR, "change": _NUM_OR_NULL, "tone": {"type": "string", "enum": ["neutral", "good", "warn", "bad"]},
    }, ["label", "value"]),
    "CashFlowChart": _schema("CashFlowChart", {
        "title": _STR, "currency": {"type": "string", "maxLength": 3},
        "points": {"type": "array", "maxItems": 120, "items": {"type": "object", "additionalProperties": False, "properties": {
            "period": _STR, "inflow": _NUM_OR_NULL, "outflow": _NUM_OR_NULL, "net": _NUM_OR_NULL, "balance": _NUM_OR_NULL},
            "required": ["period"]}},
    }, ["points"]),
    "RevenueExpenseChart": _schema("RevenueExpenseChart", {
        "title": _STR, "currency": {"type": "string", "maxLength": 3},
        "points": {"type": "array", "maxItems": 120, "items": {"type": "object", "additionalProperties": False, "properties": {
            "period": _STR, "revenue": _NUM_OR_NULL, "expenses": _NUM_OR_NULL, "profit": _NUM_OR_NULL}, "required": ["period"]}},
    }, ["points"]),
    "FinancialDataTable": _schema("FinancialDataTable", {
        "title": _STR, "currency": {"type": "string", "maxLength": 3},
        "columns": {"type": "array", "maxItems": 40, "items": _COLUMN}, "rows": _ROWS,
        "truncated": {"type": "boolean"}, "total_rows": {"type": "integer", "minimum": 0},
    }, ["columns", "rows"]),
    "BankBalanceSummary": _schema("BankBalanceSummary", {
        "as_of": _STR, "currency": {"type": "string", "maxLength": 3},
        "accounts": {"type": "array", "maxItems": 50, "items": {"type": "object", "additionalProperties": False, "properties": {
            "name": _STR, "balance": _NUM_OR_NULL, "currency": {"type": "string", "maxLength": 3}}, "required": ["name", "balance"]}},
        "total": _NUM_OR_NULL,
    }, ["accounts"]),
    "ReconciliationTable": _schema("ReconciliationTable", {
        "title": _STR, "currency": {"type": "string", "maxLength": 3},
        "columns": {"type": "array", "maxItems": 40, "items": _COLUMN}, "rows": _ROWS,
        "matched": {"type": "integer", "minimum": 0}, "unmatched": {"type": "integer", "minimum": 0},
    }, ["columns", "rows"]),
    "UnmatchedTransactions": _schema("UnmatchedTransactions", {
        "title": _STR, "currency": {"type": "string", "maxLength": 3},
        "items": {"type": "array", "maxItems": 500, "items": {"type": "object", "additionalProperties": False, "properties": {
            "reference": _STR, "party": _STR, "date": _STR, "amount": _NUM_OR_NULL, "currency": {"type": "string", "maxLength": 3},
            "days_overdue": _NUM_OR_NULL, "status": _STR}, "required": ["reference", "amount"]}},
    }, ["items"]),
    "FinancialSourceReference": _schema("FinancialSourceReference", {
        "items": {"type": "array", "maxItems": 50, "items": {"type": "object", "additionalProperties": False, "properties": {
            "n": {"type": "integer", "minimum": 1}, "name": _STR, "page": {"type": ["integer", "null"]},
            "snippet": {"type": "string", "maxLength": 300}, "source": _STR, "document_id": _STR},
            "required": ["n", "name"]}},
    }, ["items"]),
    "ApprovalRequestCard": _schema("ApprovalRequestCard", {
        "approval_id": _STR, "tool": _STR, "summary": {"type": "string", "maxLength": 500}, "expires_at": _STR,
        "decided_in": {"type": "string", "enum": ["desk"]}, "approval_url": {"type": "string", "maxLength": 500},
    }, ["approval_id", "tool", "summary", "decided_in"]),
    "FinancialRiskIndicator": _schema("FinancialRiskIndicator", {
        "level": {"type": "string", "enum": ["info", "warn", "high"]}, "title": _STR,
        "detail": {"type": "string", "maxLength": 500},
        "figures": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 80}},
    }, ["level", "title"]),
}

COMPONENTS = tuple(SCHEMAS)


def validate(component: str, props: dict[str, Any]) -> list[str]:
    schema = SCHEMAS.get(component)
    if schema is None:
        return [f"unknown component {component}"]
    return validate_args(schema, props)


def payload(component: str, props: dict[str, Any], *, tool_call_id: str | None = None) -> dict[str, Any] | None:
    """A validated AG-UI CUSTOM value, or None when the props do not fit the component."""
    full = {"version": VERSION, **props}
    if validate(component, full):
        return None
    return {"version": VERSION, "component": component, "props": full, **({"toolCallId": tool_call_id} if tool_call_id else {})}


# -- block → component mapping ---------------------------------------------------------------------------------------

_DATE_KEYS = {"period", "month", "date", "as_of", "day", "week", "quarter", "year"}
_REVENUE_KEYS = {"revenue", "sales", "income"}
_EXPENSE_KEYS = {"expenses", "expense", "costs", "cost", "opex"}
_PROFIT_KEYS = {"profit", "net_profit", "net"}
_INFLOW_KEYS = {"inflow", "inflows", "receipts", "cash_in"}
_OUTFLOW_KEYS = {"outflow", "outflows", "payments", "cash_out"}
_BALANCE_KEYS = {"balance", "ending_cash", "cash"}


def _col_keys(block: dict[str, Any]) -> list[str]:
    return [str(c.get("key", "")).lower() for c in block.get("columns", []) if isinstance(c, dict)]


def _index(keys: list[str], candidates: set[str]) -> int | None:
    for i, k in enumerate(keys):
        if k in candidates:
            return i
    return None


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _clean_table(block: dict[str, Any]) -> dict[str, Any]:
    columns = []
    for c in block.get("columns", []):
        if not isinstance(c, dict) or "key" not in c:
            continue
        col = {"key": str(c["key"])[:200], "label": str(c.get("label", c["key"]))[:200]}
        if c.get("format") in {"text", "number", "money", "percent", "date"}:
            col["format"] = c["format"]
        columns.append(col)
    rows = []
    for r in block.get("rows", [])[:500]:
        if isinstance(r, list):
            rows.append([x if isinstance(x, (str, int, float, bool)) or x is None else str(x) for x in r[:40]])
    out: dict[str, Any] = {"columns": columns, "rows": rows}
    if block.get("title"):
        out["title"] = str(block["title"])[:200]
    if "truncated" in block:
        out["truncated"] = bool(block["truncated"])
    if isinstance(block.get("total_rows"), int):
        out["total_rows"] = block["total_rows"]
    return out


def from_block(block: dict[str, Any], *, tool_name: str | None = None, currency: str | None = None,
               tool_call_id: str | None = None) -> list[dict[str, Any]]:
    """Component payloads for one Desk block. Pure and defensive: anything odd produces fewer components."""
    if not isinstance(block, dict):
        return []
    kind = block.get("type")
    out: list[dict[str, Any]] = []
    cur = {"currency": currency[:3]} if isinstance(currency, str) and currency else {}

    if kind == "kpis" and isinstance(block.get("items"), list):
        for it in block["items"][:12]:
            if not isinstance(it, dict) or "label" not in it:
                continue
            props: dict[str, Any] = {"label": str(it["label"])[:200], "value": _num(it.get("value")), **cur}
            if it.get("format") in {"text", "number", "money", "percent", "date"}:
                props["format"] = it["format"]
            if it.get("tone") in {"neutral", "good", "warn", "bad"}:
                props["tone"] = it["tone"]
            if isinstance(it.get("period"), str):
                props["period"] = it["period"][:200]
            if (p := payload("FinancialKpiCard", props, tool_call_id=tool_call_id)):
                out.append(p)
            if it.get("tone") in {"warn", "bad"}:
                risk = payload("FinancialRiskIndicator", {"level": "high" if it["tone"] == "bad" else "warn",
                                                           "title": str(it["label"])[:200]}, tool_call_id=tool_call_id)
                if risk:
                    out.append(risk)
        return out

    if kind == "table" and isinstance(block.get("columns"), list) and isinstance(block.get("rows"), list):
        keys = _col_keys(block)
        rows = [r for r in block["rows"] if isinstance(r, list)]
        date_i = _index(keys, _DATE_KEYS)
        rev_i, exp_i, prof_i = _index(keys, _REVENUE_KEYS), _index(keys, _EXPENSE_KEYS), _index(keys, _PROFIT_KEYS)
        in_i, out_i, bal_i = _index(keys, _INFLOW_KEYS), _index(keys, _OUTFLOW_KEYS), _index(keys, _BALANCE_KEYS)
        table = _clean_table(block)

        if date_i is not None and rev_i is not None and exp_i is not None and len(rows) <= 120:
            points = [{"period": str(r[date_i])[:200], "revenue": _num(r[rev_i]), "expenses": _num(r[exp_i]),
                       **({"profit": _num(r[prof_i])} if prof_i is not None else {})} for r in rows if len(r) > max(date_i, rev_i, exp_i)]
            p = payload("RevenueExpenseChart", {"points": points, **({"title": table["title"]} if "title" in table else {}), **cur},
                        tool_call_id=tool_call_id)
            if p:
                out.append(p)
        elif date_i is not None and (in_i is not None or out_i is not None) and len(rows) <= 120:
            points = []
            for r in rows:
                pt: dict[str, Any] = {"period": str(r[date_i])[:200] if len(r) > date_i else ""}
                for name, i in (("inflow", in_i), ("outflow", out_i), ("balance", bal_i)):
                    if i is not None and len(r) > i:
                        pt[name] = _num(r[i])
                points.append(pt)
            p = payload("CashFlowChart", {"points": points, **({"title": table["title"]} if "title" in table else {}), **cur},
                        tool_call_id=tool_call_id)
            if p:
                out.append(p)

        if tool_name == "list_invoices" and {"number", "balance"} <= set(keys):
            n_i, b_i = keys.index("number"), keys.index("balance")
            party_i, due_i, days_i, cur_i = (_index(keys, {"party"}), _index(keys, {"due"}), _index(keys, {"days"}),
                                             _index(keys, {"currency"}))
            items = []
            for r in rows[:500]:
                if len(r) <= max(n_i, b_i):
                    continue
                it: dict[str, Any] = {"reference": str(r[n_i])[:200], "amount": _num(r[b_i])}
                if party_i is not None and len(r) > party_i and r[party_i] is not None:
                    it["party"] = str(r[party_i])[:200]
                if due_i is not None and len(r) > due_i and r[due_i] is not None:
                    it["date"] = str(r[due_i])[:200]
                if days_i is not None and len(r) > days_i:
                    it["days_overdue"] = _num(r[days_i])
                if cur_i is not None and len(r) > cur_i and isinstance(r[cur_i], str):
                    it["currency"] = r[cur_i][:3]
                items.append(it)
            p = payload("UnmatchedTransactions", {"items": items, **({"title": table["title"]} if "title" in table else {}), **cur},
                        tool_call_id=tool_call_id)
            if p:
                out.append(p)
        elif tool_name == "accounting_report" and bal_i is not None and "balance sheet" in str(block.get("title", "")).lower():
            name_i = _index(keys, {"account", "name", "line"})
            if name_i is not None:
                accounts = []
                for r in rows:
                    if len(r) > max(name_i, bal_i) and isinstance(r[name_i], str) and any(
                            w in r[name_i].lower() for w in ("bank", "cash", "بنك", "نقد")):
                        accounts.append({"name": r[name_i][:200], "balance": _num(r[bal_i])})
                if accounts:
                    p = payload("BankBalanceSummary", {"accounts": accounts, "total": sum(a["balance"] or 0 for a in accounts),
                                                       "as_of": str(block.get("title", ""))[:200], **cur}, tool_call_id=tool_call_id)
                    if p:
                        out.append(p)
        elif tool_name == "query_dataset" and {"matched", "status"} & set(keys) and "reconcil" in str(block.get("title", "")).lower():
            st_i = _index(keys, {"status", "matched"})
            matched = sum(1 for r in rows if st_i is not None and len(r) > st_i and str(r[st_i]).lower() in {"matched", "true", "1", "yes"})
            p = payload("ReconciliationTable", {**table, "matched": matched, "unmatched": len(rows) - matched, **cur},
                        tool_call_id=tool_call_id)
            if p:
                out.append(p)
                return out

        p = payload("FinancialDataTable", {**table, **cur}, tool_call_id=tool_call_id)
        if p:
            out.append(p)
        return out

    if kind == "sources" and isinstance(block.get("items"), list):
        items = []
        for it in block["items"][:50]:
            if not isinstance(it, dict) or "name" not in it:
                continue
            entry: dict[str, Any] = {"n": int(it.get("n") or len(items) + 1), "name": str(it["name"])[:200]}
            if isinstance(it.get("page"), int):
                entry["page"] = it["page"]
            if it.get("snippet"):
                entry["snippet"] = str(it["snippet"])[:300]
            if it.get("source"):
                entry["source"] = str(it["source"])[:200]
            if it.get("document_id"):
                entry["document_id"] = str(it["document_id"])[:200]
            items.append(entry)   # links (it["link"]) are deliberately not forwarded: the SDK renders text only
        p = payload("FinancialSourceReference", {"items": items}, tool_call_id=tool_call_id)
        if p:
            out.append(p)
        return out

    return out


def approval_card(approval: dict[str, Any], *, desk_url: str) -> dict[str, Any] | None:
    props = {"approval_id": str(approval["id"]), "tool": str(approval["tool"])[:200],
             "summary": str(approval.get("summary", ""))[:500], "decided_in": "desk",
             "approval_url": f"{desk_url.rstrip('/')}/w/{approval['workspace_id']}/approvals/{approval['id']}"[:500]}
    if approval.get("expires_at"):
        import datetime as dt
        props["expires_at"] = dt.datetime.fromtimestamp(float(approval["expires_at"]), dt.UTC).isoformat()
    return payload("ApprovalRequestCard", props)


def unverified_card(figures: list[str], *, locale: str = "en") -> dict[str, Any] | None:
    title = {"en": "Figures without a source", "ar": "أرقام بلا مصدر"}.get(locale, "Figures without a source")
    detail = {"en": "These numbers in the answer were not found in any tool result or document; verify before use.",
              "ar": "هذه الأرقام في الإجابة لم تُعثر في نتائج الأدوات أو المستندات؛ تحقّق منها قبل الاعتماد عليها."}.get(locale)
    return payload("FinancialRiskIndicator", {"level": "warn", "title": title, "detail": detail,
                                              "figures": [str(f)[:80] for f in figures[:20]]})


def write_schemas(directory: Path) -> None:
    """Writes the schemas as JSON files for the SDK (single source of truth is this module)."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, schema in SCHEMAS.items():
        (directory / f"{name}.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
