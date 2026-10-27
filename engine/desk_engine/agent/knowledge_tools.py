"""Agent tools for the document knowledge base (RAG). Answers cite passages as [1], [2], …"""

from __future__ import annotations

from typing import Any

from .tools import ToolContext, ToolOutput, registry, schema


def _has_docs(ctx: ToolContext) -> bool:
    if ctx.engine.db.one("SELECT 1 FROM documents WHERE workspace_id = ? AND status IN ('indexed', 'remote') LIMIT 1",
                         (ctx.ws_id,)):
        return True
    return ctx.engine.db.one("SELECT 1 FROM connections WHERE workspace_id = ? AND provider = 'pipeshub' AND "
                             "status = 'connected' LIMIT 1", (ctx.ws_id,)) is not None


def _collection_id(ctx: ToolContext, name: str | None) -> str | None:
    if not name:
        return None
    row = ctx.engine.db.one("SELECT id FROM doc_collections WHERE workspace_id = ? AND (id = ? OR lower(name) = lower(?))",
                            (ctx.ws_id, name, name))
    return row["id"] if row else "__none__"


@registry.tool("search_documents", "Search the workspace's documents (policies, contracts, reports, statements, "
               "e-mails saved to the knowledge base, files attached in chat, and PipesHub if connected). Returns numbered passages; cite them in "
               "the answer as [1], [2] with the document name and page.",
               schema({"query": {"type": "string", "description": "what to look for, in natural language"},
                       "collection": {"type": "string", "description": "optional collection name"},
                       "document_ids": {"type": "array", "items": {"type": "string"},
                                        "description": "only these documents, e.g. the files attached to the message"},
                       "top_k": {"type": "integer", "default": 6, "minimum": 1, "maximum": 12}}, ["query"]),
               category="knowledge", available=_has_docs)
async def _search(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    coll = _collection_id(ctx, a.get("collection"))
    if coll == "__none__":
        names = [r["name"] for r in ctx.engine.db.all("SELECT name FROM doc_collections WHERE workspace_id = ?", (ctx.ws_id,))]
        return ToolOutput(f"No collection called '{a['collection']}'. Collections: {', '.join(names) or 'none'}.")
    doc_ids = [str(d) for d in (a.get("document_ids") or [])][:20] or None
    hits = await ctx.engine.knowledge.search(ctx.workspace, a["query"], top_k=int(a.get("top_k") or 6), collection_id=coll,
                                             doc_ids=doc_ids)
    if not hits:
        return ToolOutput("No passages matched. Try other words, or check that the document was added under Documents.")
    lines, items = [], []
    for n, h in enumerate(hits, start=1):
        where = f"{h['name']}" + (f", page {h['page']}" if h["page"] else "") + (" (PipesHub)" if h["source"] == "pipeshub" else "")
        lines.append(f"[{n}] {where}\n{h['text'][:1800]}")
        items.append({"n": n, "document_id": h["document_id"], "name": h["name"], "page": h["page"],
                      "snippet": h["text"][:280], "source": h["source"], "link": h.get("link")})
    return ToolOutput("\n\n".join(lines), [{"type": "sources", "items": items}], hits)


@registry.tool("list_documents", "List documents in the knowledge base (name, collection, pages, status).",
               schema({"collection": {"type": "string"}}), category="knowledge", available=_has_docs)
async def _list(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    coll = _collection_id(ctx, a.get("collection"))
    docs = ctx.engine.knowledge.list(ctx.ws_id, None if coll == "__none__" else coll)
    if not docs:
        return ToolOutput("The knowledge base is empty.")
    return ToolOutput("\n".join(f"- {d['name']} (id {d['id']}, {d['collection_name'] or 'no collection'}, "
                                f"{d['pages']} pages, {d['status']})" for d in docs[:200]), [], docs[:200])


@registry.tool("read_document", "Read the text of one document (or a page range) when search passages are not enough, "
               "e.g. to review a whole contract clause or policy section.",
               schema({"document_id": {"type": "string"}, "page_from": {"type": "integer"},
                       "page_to": {"type": "integer"}}, ["document_id"]), category="knowledge", available=_has_docs)
async def _read(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    doc = ctx.engine.knowledge.get(ctx.ws_id, a["document_id"])
    if doc["status"] == "remote":
        return ToolOutput(f"“{doc['name']}” could not be read here (for example a scanned PDF); PipesHub indexed it with "
                          f"OCR. Use search_documents with document_ids [\"{doc['id']}\"] to find passages in it.")
    if doc["status"] == "processing":
        return ToolOutput(f"“{doc['name']}” is still being processed; try again in a moment.")
    if doc["status"] == "failed":
        return ToolOutput(f"“{doc['name']}” could not be read: {doc.get('error') or 'unknown error'}")
    text = ctx.engine.knowledge.text(ctx.ws_id, doc["id"], a.get("page_from"), a.get("page_to"))
    return ToolOutput(f"Document: {doc['name']}\n\n{text}" if text else "No text in that range.",
                      [{"type": "sources", "items": [{"n": 1, "document_id": doc["id"], "name": doc["name"],
                                                      "page": a.get("page_from"), "snippet": text[:280],
                                                      "source": "documents"}]}])


@registry.tool("search_workspace", "Search past conversations, saved memory, generated files and datasets of this workspace "
               "(use search_documents for document passages). Useful for 'what did we decide about…' or 'where is the "
               "file for…'.",
               schema({"query": {"type": "string"},
                       "sources": {"type": "array", "items": {"type": "string",
                                                              "enum": ["conversations", "memory", "files", "datasets"]}}},
                      ["query"]), category="knowledge")
async def _search_ws(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    from ..knowledge.search import search_all

    sources = [s for s in (a.get("sources") or ["conversations", "memory", "files", "datasets"]) if s != "documents"]
    res = await search_all(ctx.engine, ctx.workspace, ctx.user_id, a["query"], sources=sources, limit=6)
    lines = []
    for kind, items in res.items():
        for it in items:
            if kind == "conversations" and it["id"] == ctx.thread_id:
                continue
            lines.append(f"- [{kind}] {it['title']}: {str(it['snippet'])[:300]} (open: {it['link']})")
    return ToolOutput("\n".join(lines) or "Nothing matched.")
