"""Agent tools that produce evidence-linked Excel workbooks and Word reports."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..files.spec import FileSpec
from .data_tools import QUERY_SCHEMA
from .tools import ToolContext, ToolOutput, registry, schema

FORMAT = {"type": "string", "enum": ["text", "number", "integer", "money", "percent", "date"]}
COLUMN = {"type": "object", "properties": {
    "label": {"type": "string"},
    "key": {"type": "string", "description": "optional; default is the label in lower case with underscores"},
    "format": FORMAT,
    "formula": {"type": "string", "description": "computed column, e.g. 'actual - budget' or '(actual - budget) / budget'"},
    "total": {"type": "string", "enum": ["sum", "avg", "min", "max", "count", "formula", "none"]},
}, "required": ["label"]}
BLOCK = {"type": "object", "properties": {
    "type": {"type": "string", "enum": ["heading", "text", "note", "table", "kpis"]},
    "text": {"type": "string", "description": "heading, text and note blocks"},
    "id": {"type": "string", "description": "table id used in references, e.g. pl"},
    "title": {"type": "string"},
    "query": {**QUERY_SCHEMA, "description": "rows from a dataset (preferred: exact source and drill-down)"},
    "rows": {"type": "array", "items": {"type": "array", "items": {}},
             "description": "typed rows; every number must come from a tool result in this conversation"},
    "columns": {"type": "array", "items": COLUMN,
                "description": "query tables: optional relabelling of the query columns in order, plus computed "
                               "columns; typed tables: one entry per value in a row, plus computed columns"},
    "pivot": {"type": "object", "properties": {"column": {"type": "string"}, "row_total": {"type": "boolean"}},
              "required": ["column"], "description": "turn the values of one group_by column into columns"},
    "total_row": {"type": "boolean"},
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "label": {"type": "string"}, "value": {"type": "string", "description": "e.g. [pl:total:amount]"},
        "format": FORMAT}, "required": ["label", "value"]}, "description": "kpis blocks"},
}, "required": ["type"]}
SHEETS = {"type": "array", "items": {"type": "object", "properties": {
    "name": {"type": "string", "description": "sheet name, at most 31 characters"},
    "blocks": {"type": "array", "items": BLOCK}}, "required": ["name", "blocks"]}}
SECTIONS = {"type": "array", "items": {"type": "object", "properties": {
    "heading": {"type": "string"},
    "blocks": {"type": "array", "items": {"type": "object", "properties": {
        "type": {"type": "string", "enum": ["paragraph", "bullets", "table"]},
        "text": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}},
        "table": {"type": "string", "description": "id of a table defined in sheets"}}, "required": ["type"]}}},
    "required": ["heading", "blocks"]}}

COMMON = ("Every figure stays traceable: query tables carry their exact dataset source with drill-down to the rows; "
          "numbers typed into `rows` are matched to this conversation's tool results (unmatched ones are shaded "
          "yellow and flagged, so prefer queries or copy figures exactly from tool results); computed columns, "
          "totals and KPIs become live Excel formulas. Column keys default to the label in lower case with "
          "underscores ('Actual amount' → actual_amount). Formulas use + - * / ( ) ABS ROUND MIN MAX SUM AVERAGE "
          "over keys of the same row, e.g. 'actual - budget', '(actual - budget) / budget' with format percent. "
          "Totals: numbers are summed; formula columns are recalculated on the total row (a ratio of totals). "
          "Reference any cell as [table:row:column] where row is 'total' or the first-column label, e.g. "
          "[pl:Revenue:amount]. Percentages are fractions (0.125). After creation an automatic check and an "
          "independent reviewer annotate the file; to fix findings, call again with file_id to save a new version.")


def _errors(exc: ValidationError) -> str:
    parts = []
    for e in exc.errors(include_url=False)[:6]:
        loc = ".".join(str(x) for x in e["loc"])
        parts.append(f"{loc}: {e['msg']}")
    return "The file definition is not valid: " + "; ".join(parts)


async def _create(ctx: ToolContext, a: dict[str, Any], kind: str) -> ToolOutput:
    payload = {k: a[k] for k in ("title", "subtitle", "sheets", "sections") if k in a}
    if kind == "report" and not payload.get("sections"):
        return ToolOutput("A report needs at least one section.")
    if kind == "workbook":
        payload.pop("sections", None)
    try:
        spec = FileSpec.model_validate(payload)
    except ValidationError as exc:
        return ToolOutput(_errors(exc))
    out = await ctx.engine.files.create(ctx.workspace, ctx.user_id, spec, thread_id=ctx.thread_id,
                                        file_id=a.get("file_id"), note=a.get("note"))
    f, model = out["file"], out["model"]
    s = model["summary"]
    unverified = [c["a"] for sh in model["sheets"] for b in sh["blocks"] if b["type"] == "table"
                  for row in b["rows"] for c in row if c.get("st") == "unverified"]
    lines = [f"Saved {'report' if kind == 'report' else 'workbook'} “{f['name']}” (file {f['id']}, version "
             f"{out['version']}; formats: {', '.join(out['formats'])})."]
    lines.append(f"Inputs: {s['cells_sourced']} traced to a source, {s['cells_unverified']} without a source"
                 + (f" ({', '.join(unverified[:10])})" if unverified else "") + f"; {s['cells_computed']} live formulas.")
    if kind == "report":
        lines.append(f"Report text: {s['text_sourced'] + s['text_computed']} figures linked, {s['text_unverified']} "
                     "without a source.")
    checks = [x for x in out["findings"] if x["origin"] == "check"]
    if checks:
        lines.append("Automatic checks: " + "; ".join(f"{x['target']}: {x['title']}" for x in checks[:8]))
    if f["review_status"] == "running":
        lines.append("An independent reviewer is checking the file now; findings will appear on it.")
    lines.append("Tell the user the file is ready and mention anything without a source.")
    return ToolOutput("\n".join(lines), [{"type": "link", "kind": "file", "id": f["id"], "label": f["name"]}],
                      {"file_id": f["id"], "version": out["version"]})


@registry.tool("create_workbook", "Create an Excel workbook of tables, KPIs and notes. " + COMMON,
               schema({"title": {"type": "string"}, "sheets": SHEETS,
                       "file_id": {"type": "string", "description": "save as a new version of this file"},
                       "note": {"type": "string", "description": "what changed in this version"}},
                      ["title", "sheets"]), category="files",
               summarize=lambda a: f"Create workbook '{a.get('title')}'")
async def _workbook(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    return await _create(ctx, a, "workbook")


@registry.tool("create_report", "Create a Word report with a companion Excel workbook. Define the tables in `sheets` "
               "and the narrative in `sections`. In the text, quote table figures with placeholders so they stay exact "
               "and linked: {{pl.total.variance}} or {{[pl:Revenue:actual] / [pl:total:actual] | percent}} (formats: "
               "money, number, integer, percent). Each figure gets an [S]/[C] marker that links to the list of sources "
               "and calculations. " + COMMON,
               schema({"title": {"type": "string"}, "subtitle": {"type": "string"}, "sheets": SHEETS,
                       "sections": SECTIONS, "file_id": {"type": "string"}, "note": {"type": "string"}},
                      ["title", "sheets", "sections"]), category="files",
               summarize=lambda a: f"Create report '{a.get('title')}'")
async def _report(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    return await _create(ctx, a, "report")


@registry.tool("get_file_review", "Read the open findings (automatic checks and the independent reviewer) of a file "
               "created with create_workbook or create_report, to fix them in a new version.",
               schema({"file_id": {"type": "string"}}, ["file_id"]), category="files")
async def _review(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    out = ctx.engine.files.get(ctx.ws_id, a["file_id"])
    f = out["file"]
    if f["review_status"] == "running":
        status = "The reviewer is still working; check again shortly."
    elif f["review_status"] == "error":
        status = f"The review failed: {f['review_error']}"
    else:
        status = f"Review status: {f['review_status']}."
    open_ = [x for x in out["findings"] if x["status"] == "open"]
    if not open_:
        return ToolOutput(f"{status} No open findings on version {out['version']}.")
    lines = [status, f"Open findings on version {out['version']}:"]
    lines += [f"- [{x['severity']}] {x['target']}: {x['title']}" + (f" — {x['body']}" if x["body"] else "")
              for x in open_]
    return ToolOutput("\n".join(lines))


# -- the user's own Excel and Word files ---------------------------------------------------------------------------
EDIT = {"type": "object", "properties": {
    "op": {"type": "string", "enum": ["set_cell", "append_row", "add_sheet", "rename_sheet", "fill", "replace_text",
                                      "set_paragraph", "insert_paragraph", "set_table_cell", "add_table_row"]},
    "sheet": {"type": "string"}, "cell": {"type": "string", "description": "e.g. C7"},
    "value": {"description": "number or text; copy figures exactly from tool results"},
    "formula": {"type": "string", "description": "Excel formula over this workbook, e.g. =SUM(C2:C9)"},
    "values": {"description": "append_row/add_table_row: list of values; fill: {placeholder: text}"},
    "after_row": {"type": "integer", "description": "append_row: insert below this row instead of at the end"},
    "name": {"type": "string"}, "rows": {"type": "array", "items": {"type": "array", "items": {}}},
    "find": {"type": "string"}, "replace": {"type": "string"},
    "index": {"type": "integer", "description": "paragraph number Pn from inspect_file"},
    "after": {"type": "integer", "description": "insert_paragraph: after paragraph Pn (omit for the end)"},
    "text": {"type": "string"}, "style": {"type": "string", "description": "e.g. Heading 2, List Bullet"},
    "table": {"type": "integer"}, "row": {"type": "integer"}, "column": {"type": "integer"},
}, "required": ["op"]}


def _edit_summary(out: dict[str, Any]) -> str:
    m = out["model"]
    s = m["summary"]
    lines = [f"Saved “{out['file']['name']}” version {out['version']} ({m['format']}): {s['edits']} change(s); "
             f"{s['sourced']} figure(s) traced to a source, {s['formulas']} formula(s), {s['unverified']} without a "
             "source (highlighted yellow in the file)."]
    for e in m["edits"][:30]:
        if e.get("status") == "sourced" and e.get("source"):
            lines.append(f"- {e['target']}: {e.get('after')} ← {e['source']}")
        elif e.get("status") == "unverified":
            lines.append(f"- {e['target']}: {', '.join(e.get('unverified') or [str(e.get('after'))])} has NO source")
    lines.append("Tell the user what changed, and name every figure without a source.")
    return "\n".join(lines)


def _upload_link(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "link", "kind": "file", "id": out["file"]["id"], "label": out["file"]["name"]}]


@registry.tool("open_file_for_editing", "Make an attached or uploaded Excel (.xlsx) or Word (.docx) document editable "
               "(the user's own template, a previous report, a reconciliation). The original stays as version 1. "
               "Returns a file_id for inspect_file and edit_file.",
               schema({"document_id": {"type": "string", "description": "id of the attached document"}},
                      ["document_id"]), category="files", summarize=lambda a: "Open a file for editing")
async def _open(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    doc = ctx.engine.knowledge.get(ctx.ws_id, a["document_id"])
    existing = ctx.engine.db.one("SELECT f.id FROM files f JOIN file_versions v ON v.file_id = f.id AND v.version = 1 "
                                 "WHERE f.workspace_id = ? AND json_extract(v.spec, '$.document_id') = ? "
                                 "ORDER BY f.created_at DESC LIMIT 1", (ctx.ws_id, doc["id"]))
    if existing:
        out = ctx.engine.files.get(ctx.ws_id, existing["id"])
    else:
        data = await ctx.engine.knowledge.read_file(ctx.ws_id, doc["id"])
        out = await ctx.engine.files.import_upload(ctx.workspace, ctx.user_id, doc["name"], data,
                                                   thread_id=ctx.thread_id, document_id=doc["id"])
    m = out["model"]
    risk = (f" Warning: editing will drop its {', '.join(m['at_risk'])}; tell the user before editing."
            if m.get("at_risk") else "")
    return ToolOutput(f"“{doc['name']}” is editable as file {out['file']['id']} (version {out['version']}).{risk} "
                      "Call inspect_file to read its cells or paragraphs before editing.", _upload_link(out),
                      {"file_id": out["file"]["id"]})


@registry.tool("inspect_file", "Read the user's own Excel or Word file: Excel lists non-empty cells (address: value, "
               "formulas with their saved result) for one sheet or range; Word lists numbered paragraphs Pn, tables "
               "Tn with rows Rn, and placeholders such as {{client_name}}.",
               schema({"file_id": {"type": "string"}, "sheet": {"type": "string"},
                       "range": {"type": "string", "description": "e.g. A1:H60"},
                       "start": {"type": "integer", "description": "Word: first paragraph to list"}}, ["file_id"]),
               category="files")
async def _inspect(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    text = await ctx.engine.files.inspect_upload(ctx.ws_id, a["file_id"], sheet=a.get("sheet"),
                                                 cell_range=a.get("range"), start=int(a.get("start") or 0))
    return ToolOutput(text)


@registry.tool("edit_file", "Edit the user's own Excel or Word file and save a new version that keeps its layout and "
               "styles. Excel: set_cell (value or formula), append_row (values; formulas start with =), add_sheet "
               "(name, rows), rename_sheet. Word: fill (values {placeholder: text}), replace_text (find, replace), "
               "set_paragraph (index, text), insert_paragraph (after, text, style), set_table_cell (table, row, "
               "column, text), add_table_row (table, values). Copy figures exactly from tool results: each number is "
               "matched to its source and unmatched ones are highlighted. Prefer formulas for totals and variances.",
               schema({"file_id": {"type": "string"}, "edits": {"type": "array", "items": EDIT},
                       "note": {"type": "string", "description": "what this version changes"}},
                      ["file_id", "edits"]), category="files",
               summarize=lambda a: f"Edit file ({len(a.get('edits') or [])} changes)")
async def _edit(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    out = await ctx.engine.files.edit_upload(ctx.workspace, ctx.user_id, a["file_id"], a.get("edits") or [],
                                             thread_id=ctx.thread_id, note=a.get("note"))
    return ToolOutput(_edit_summary(out), _upload_link(out), {"file_id": out["file"]["id"], "version": out["version"]})
