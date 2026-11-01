"""Agent tools for accounting systems and Google."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..connectors.accounting import ACCOUNTING_PROVIDERS, client_for, default_period, sync_connection, with_aging
from ..connectors.google import GoogleClient, build_raw_email
from ..connectors.oauth import get_connection
from ..data import importer, store
from .tools import ToolContext, ToolOutput, registry, schema, table_block, table_text


def _has(provider: set[str]) -> Any:
    def check(ctx: ToolContext) -> bool:
        marks = ",".join("?" for _ in provider)
        return ctx.engine.db.one(f"SELECT 1 FROM connections WHERE workspace_id = ? AND status != 'pending' AND "
                                 f"provider IN ({marks}) LIMIT 1", (ctx.ws_id, *provider)) is not None
    return check


CONN = {"type": "string", "description": "connection id; needed only when several are connected"}


@registry.tool("accounting_report", "Profit and loss or balance sheet straight from the connected accounting system "
               "(QuickBooks Online, Zoho Books, Xero, Odoo or a system linked through the Open Accounting Connector). "
               "Dates are YYYY-MM-DD.",
               schema({"report": {"type": "string", "enum": ["profit_and_loss", "balance_sheet"]},
                       "start_date": {"type": "string"}, "end_date": {"type": "string"},
                       "as_of": {"type": "string", "description": "balance sheet date"},
                       "by_month": {"type": "boolean", "description": "P&L columns per month"},
                       "connection_id": CONN}, ["report"]),
               category="accounting", available=_has(ACCOUNTING_PROVIDERS))
async def _report(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    conn = get_connection(ctx.engine, ctx.ws_id, a.get("connection_id"), ACCOUNTING_PROVIDERS)
    client = client_for(ctx.engine, conn)
    if a["report"] == "balance_sheet":
        from datetime import date

        as_of = date.fromisoformat(a["as_of"]) if a.get("as_of") else date.today()
        lines = await client.balance_sheet(as_of)
        cols = [{"key": "section", "label": "Section", "format": "text"}, {"key": "group", "label": "Group", "format": "text"},
                {"key": "account", "label": "Account", "format": "text"}, {"key": "amount", "label": "Amount", "format": "money"}]
        rows = [[ln["section"], ln["group"], ln["account"], ln["amount"]] for ln in lines]
        totals = defaultdict(float)
        for ln in lines:
            totals[ln["section"]] += ln["amount"]
        head = f"Balance sheet as of {as_of} from {conn['label']}: " + ", ".join(f"{k} {v:,.2f}" for k, v in totals.items())
        return ToolOutput(head + "\n" + table_text(cols, rows, 80), [table_block(cols, rows, f"Balance sheet {as_of}")],
                          lines)
    start, end = default_period(a.get("start_date"), a.get("end_date"))
    lines = await client.profit_and_loss(start, end)
    if a.get("by_month"):
        periods = sorted({ln["period"] for ln in lines})
        grid: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
        for ln in lines:
            grid[(ln["section"], ln["account"])][ln["period"]] = grid[(ln["section"], ln["account"])].get(ln["period"], 0) + ln["amount"]
        cols = [{"key": "section", "label": "Section", "format": "text"}, {"key": "account", "label": "Account", "format": "text"}]
        cols += [{"key": p, "label": p[:7], "format": "money"} for p in periods]
        rows = [[s, acc, *[vals.get(p) for p in periods]] for (s, acc), vals in grid.items()]
    else:
        agg: dict[tuple[str, str], float] = defaultdict(float)
        for ln in lines:
            agg[(ln["section"], ln["account"])] += ln["amount"]
        cols = [{"key": "section", "label": "Section", "format": "text"}, {"key": "account", "label": "Account", "format": "text"},
                {"key": "amount", "label": "Amount", "format": "money"}]
        rows = [[s, acc, round(v, 2)] for (s, acc), v in agg.items()]
    order = {"Income": 0, "Cost of sales": 1, "Expenses": 2, "Other income": 3, "Other expenses": 4, "Net profit": 5}
    rows.sort(key=lambda r: (order.get(r[0], 9), str(r[1])))
    title = f"Profit and loss {start} to {end} ({conn['label']})"
    return ToolOutput(title + "\n" + table_text(cols, rows, 80), [table_block(cols, rows, title)], lines)


@registry.tool("list_invoices", "Customer invoices (receivable) or supplier bills (payable) from the accounting "
               "system, with days overdue and aging bucket.",
               schema({"kind": {"type": "string", "enum": ["receivable", "payable"]},
                       "status": {"type": "string", "enum": ["open", "overdue", "all"], "default": "open"},
                       "party_contains": {"type": "string"}, "limit": {"type": "integer", "default": 100},
                       "connection_id": CONN}, ["kind"]),
               category="accounting", available=_has(ACCOUNTING_PROVIDERS))
async def _invoices(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    conn = get_connection(ctx.engine, ctx.ws_id, a.get("connection_id"), ACCOUNTING_PROVIDERS)
    client = client_for(ctx.engine, conn)
    status = a.get("status") or "open"
    docs = await (client.invoices if a["kind"] == "receivable" else client.bills)(open_only=status != "all")
    docs = with_aging(docs)
    if status == "overdue":
        docs = [d for d in docs if d["days_overdue"] > 0 and (d["balance"] or 0) > 0]
    if a.get("party_contains"):
        needle = a["party_contains"].lower()
        docs = [d for d in docs if needle in str(d.get("party") or "").lower()]
    docs.sort(key=lambda d: (-(d["days_overdue"] or 0), -(d["balance"] or 0)))
    limit = max(1, min(int(a.get("limit") or 100), 500))
    party = "Customer" if a["kind"] == "receivable" else "Vendor"
    cols = [{"key": "number", "label": "Number", "format": "text"}, {"key": "party", "label": party, "format": "text"},
            {"key": "due", "label": "Due", "format": "date"}, {"key": "currency", "label": "Cur.", "format": "text"},
            {"key": "balance", "label": "Balance", "format": "money"}, {"key": "days", "label": "Days overdue", "format": "number"}]
    rows = [[d["number"], d["party"], d["due_date"], d["currency"], d["balance"], d["days_overdue"]] for d in docs[:limit]]
    total = sum(d["balance"] or 0 for d in docs)
    head = f"{len(docs)} documents, balance {total:,.2f} (mixed currencies are summed as-is; check the Cur. column)."
    return ToolOutput(head + "\n" + table_text(cols, rows), [table_block(cols, rows, f"{party} documents")], docs[:limit])


@registry.tool("sync_accounting", "Refresh the datasets synced from an accounting connection (P&L by month, balance "
               "sheet, invoices, bills).", schema({"connection_id": CONN}), category="accounting",
               available=_has(ACCOUNTING_PROVIDERS), timeout=1800, min_role="editor")
async def _sync(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    conn = get_connection(ctx.engine, ctx.ws_id, a.get("connection_id"), ACCOUNTING_PROVIDERS)
    result = await sync_connection(ctx.engine, conn, user_id=ctx.user_id)
    names = [store.get_dataset(ctx.engine, ctx.ws_id, d)["name"] for d in result["datasets"].values()]
    text = "Synced: " + ", ".join(names) if names else "Nothing was synced."
    if result["errors"]:
        text += "\nProblems: " + "; ".join(f"{k}: {v}" for k, v in result["errors"].items())
    return ToolOutput(text, [], result)


# --- Google --------------------------------------------------------------------------------------

_google = _has({"google"})


def _gclient(ctx: ToolContext, connection_id: str | None) -> GoogleClient:
    return GoogleClient(ctx.engine, get_connection(ctx.engine, ctx.ws_id, connection_id, "google"))


@registry.tool("gmail_search", "Search Gmail with Gmail search syntax (e.g. 'from:acme has:attachment newer_than:30d').",
               schema({"query": {"type": "string"}, "max_results": {"type": "integer", "default": 10},
                       "connection_id": CONN}, ["query"]), category="google", available=_google)
async def _gsearch(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    items = await _gclient(ctx, a.get("connection_id")).search(a["query"], int(a.get("max_results") or 10))
    if not items:
        return ToolOutput("No messages found.")
    lines = [f"- id {m['id']} | {m['date']} | {m['from']} | {m['subject']} | {m['snippet'][:120]}"
             + (f" | attachments: {', '.join(x['filename'] for x in m['attachments'])}" if m["attachments"] else "")
             for m in items]
    cols = [{"key": "date", "label": "Date", "format": "text"}, {"key": "from", "label": "From", "format": "text"},
            {"key": "subject", "label": "Subject", "format": "text"}]
    return ToolOutput("\n".join(lines), [table_block(cols, [[m["date"], m["from"], m["subject"]] for m in items],
                                                     "Gmail")], items)


@registry.tool("gmail_read", "Read one e-mail (headers, text body, attachment list).",
               schema({"message_id": {"type": "string"}, "connection_id": CONN}, ["message_id"]),
               category="google", available=_google)
async def _gread(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    m = await _gclient(ctx, a.get("connection_id")).read(a["message_id"])
    att = "\n".join(f"- {x['filename']} ({x['mime_type']}, attachment_id {x['attachment_id']})" for x in m["attachments"])
    text = (f"From: {m['from']}\nTo: {m['to']}\nDate: {m['date']}\nSubject: {m['subject']}\n\n{m['body']}"
            + (f"\n\nAttachments:\n{att}" if att else ""))
    return ToolOutput(text, [], m)


def _mail_summary(a: dict[str, Any]) -> str:
    return f"E-mail to {a.get('to')}: “{a.get('subject')}”\n\n{a.get('body', '')[:1500]}"


EMAIL_SCHEMA = schema({"to": {"type": "string"}, "cc": {"type": "string"}, "subject": {"type": "string"},
                       "body": {"type": "string", "description": "plain text"},
                       "reply_to_message_id": {"type": "string"}, "connection_id": CONN}, ["to", "subject", "body"])


async def _prepare(ctx: ToolContext, a: dict[str, Any]) -> tuple[GoogleClient, str, str | None]:
    client = _gclient(ctx, a.get("connection_id"))
    thread_id = in_reply_to = None
    if a.get("reply_to_message_id"):
        original = await client.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{a['reply_to_message_id']}",
                                    {"format": "metadata", "metadataHeaders": ["Message-ID"]})
        thread_id = original.get("threadId")
        for h in original.get("payload", {}).get("headers", []):
            if h["name"].lower() == "message-id":
                in_reply_to = h["value"]
    raw = build_raw_email(sender=None, to=a["to"], cc=a.get("cc"), subject=a["subject"], body=a["body"],
                          in_reply_to=in_reply_to)
    return client, raw, thread_id


@registry.tool("gmail_create_draft", "Create a Gmail draft for the user to review and send.", EMAIL_SCHEMA,
               effect="write", category="google", available=_google, summarize=lambda a: "Create Gmail draft. " + _mail_summary(a))
async def _gdraft(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    client, raw, thread_id = await _prepare(ctx, a)
    draft = await client.create_draft(raw, thread_id)
    return ToolOutput(f"Draft created in Gmail (draft id {draft.get('id')}).", [], draft)


@registry.tool("gmail_send", "Send an e-mail from the connected Gmail account.", EMAIL_SCHEMA, effect="write",
               category="google", available=_google, summarize=lambda a: "Send e-mail. " + _mail_summary(a))
async def _gsend(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    client, raw, thread_id = await _prepare(ctx, a)
    sent = await client.send(raw, thread_id)
    return ToolOutput(f"E-mail sent (message id {sent.get('id')}).", [], sent)


async def _import_bytes(ctx: ToolContext, content: bytes, filename: str, name: str | None, sheet: str | None,
                        source: str, ref: dict[str, Any]) -> ToolOutput:
    tables = importer.read_workbook(content, filename)
    table = next((t for t in tables if sheet and t.name.lower() == sheet.lower()), tables[0])
    shaped = importer.shape(table)
    ds = store.create_dataset(ctx.engine, ctx.ws_id, name=name or f"{filename.rsplit('.', 1)[0]} · {shaped.name}",
                              source=source, columns=[c.to_dict() for c in shaped.columns], rows=shaped.rows,
                              source_ref=ref, created_by=ctx.user_id)
    others = [t.name for t in tables if t is not table]
    text = (f"Imported '{ds['name']}' with {ds['row_count']} rows; columns: "
            + ", ".join(f"{c['name']} [{c['type']}]" for c in ds["columns"]))
    if others:
        text += f". Other sheets not imported: {', '.join(others)}"
    return ToolOutput(text, [{"type": "link", "kind": "dataset", "id": ds["id"], "label": ds["name"]}],
                      {"dataset_id": ds["id"]})


@registry.tool("gmail_import_attachment", "Import an Excel or CSV attachment from an e-mail as a dataset.",
               schema({"message_id": {"type": "string"}, "attachment_id": {"type": "string"},
                       "filename": {"type": "string"}, "dataset_name": {"type": "string"}, "sheet": {"type": "string"},
                       "connection_id": CONN}, ["message_id", "attachment_id", "filename"]),
               category="google", available=_google, effect="write")
async def _gimport(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    content = await _gclient(ctx, a.get("connection_id")).attachment(a["message_id"], a["attachment_id"])
    return await _import_bytes(ctx, content, a["filename"], a.get("dataset_name"), a.get("sheet"), "gmail",
                               {"message_id": a["message_id"], "filename": a["filename"]})


@registry.tool("drive_find_spreadsheets", "Find Google Sheets, Excel and CSV files in Google Drive.",
               schema({"name_contains": {"type": "string"}, "connection_id": CONN}), category="google",
               available=_google)
async def _dfind(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    files = await _gclient(ctx, a.get("connection_id")).find_spreadsheets(a.get("name_contains") or "")
    if not files:
        return ToolOutput("No spreadsheets found.")
    return ToolOutput("\n".join(f"- {f['name']} (id {f['id']}, {f['mimeType']}, modified {f.get('modifiedTime')})"
                                for f in files), [], files)


@registry.tool("drive_import_spreadsheet", "Import a Google Sheet, Excel or CSV file from Google Drive as a dataset.",
               schema({"file_id": {"type": "string"}, "dataset_name": {"type": "string"}, "sheet": {"type": "string"},
                       "connection_id": CONN}, ["file_id"]), category="google", available=_google, effect="write")
async def _dimport(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    content, filename = await _gclient(ctx, a.get("connection_id")).download_spreadsheet(a["file_id"])
    return await _import_bytes(ctx, content, filename, a.get("dataset_name"), a.get("sheet"), "google_drive",
                               {"file_id": a["file_id"]})


# --- documents from Gmail and Drive into the knowledge base ---------------------------------------


@registry.tool("gmail_save_to_documents", "Add an e-mail attachment (PDF, Word, Excel, text) or the e-mail itself to "
               "the document knowledge base so it can be searched and cited.",
               schema({"message_id": {"type": "string"},
                       "attachment_id": {"type": "string", "description": "omit to save the e-mail body"},
                       "filename": {"type": "string"}, "collection": {"type": "string"}, "connection_id": CONN},
                      ["message_id"]), category="google", available=_google, effect="write")
async def _gsave(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    client = _gclient(ctx, a.get("connection_id"))
    if a.get("attachment_id"):
        if not a.get("filename"):
            return ToolOutput("Give the attachment's filename (from gmail_read).")
        data = await client.attachment(a["message_id"], a["attachment_id"])
        filename = a["filename"]
    else:
        m = await client.read(a["message_id"])
        filename = f"{(m['subject'] or 'e-mail')[:80]}.txt"
        data = (f"From: {m['from']}\nTo: {m['to']}\nDate: {m['date']}\nSubject: {m['subject']}\n\n{m['body']}").encode()
    coll = None
    if a.get("collection"):
        row = ctx.engine.db.one("SELECT id FROM doc_collections WHERE workspace_id = ? AND lower(name) = lower(?)",
                                (ctx.ws_id, a["collection"]))
        coll = row["id"] if row else None
    doc = await ctx.engine.knowledge.add(ctx.ws_id, ctx.user_id, filename, data, collection_id=coll, source="gmail",
                                   source_ref={"message_id": a["message_id"]})
    return ToolOutput(f"Added '{doc['name']}' to the knowledge base; it is being indexed.",
                      [{"type": "link", "kind": "document", "id": doc["id"], "label": doc["name"]}], {"document_id": doc["id"]})


@registry.tool("drive_add_document", "Add a file from Google Drive (PDF, Word, Google Doc, text) to the document "
               "knowledge base.", schema({"file_id": {"type": "string"}, "collection": {"type": "string"},
                                          "connection_id": CONN}, ["file_id"]), category="google", available=_google, effect="write")
async def _dadd(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    content, filename = await _gclient(ctx, a.get("connection_id")).download_document(a["file_id"])
    coll = None
    if a.get("collection"):
        row = ctx.engine.db.one("SELECT id FROM doc_collections WHERE workspace_id = ? AND lower(name) = lower(?)",
                                (ctx.ws_id, a["collection"]))
        coll = row["id"] if row else None
    doc = await ctx.engine.knowledge.add(ctx.ws_id, ctx.user_id, filename, content, collection_id=coll, source="google_drive",
                                   source_ref={"file_id": a["file_id"]})
    return ToolOutput(f"Added '{doc['name']}' to the knowledge base; it is being indexed.",
                      [{"type": "link", "kind": "document", "id": doc["id"], "label": doc["name"]}], {"document_id": doc["id"]})
