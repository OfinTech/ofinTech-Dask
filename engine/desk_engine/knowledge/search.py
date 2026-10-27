"""Workspace search: one query over documents, past conversations, saved memory, generated files and datasets,
with an optional short answer that cites what it used (the same idea as PipesHub's enterprise search, built in).

Every source applies its own permission rules: documents and memory are filtered by workspace (and personal memory by
user), conversations exclude other people's private threads, and PipesHub results are filtered as in base.py."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..db import loads
from .base import fts_query

if TYPE_CHECKING:
    from ..app import Engine

SOURCES = ("documents", "conversations", "memory", "files", "datasets")
ANSWER_PROMPT = """Answer the question using only the numbered sources below. Cite every statement with [n]. If the
sources do not answer it, say so in one sentence. Be brief: at most 120 words. Do not invent figures."""


STOP = {"the", "and", "for", "per", "with", "from", "our", "what", "how", "are", "was", "this", "that", "del", "las",
        "los", "para", "con", "une", "des", "les", "pour", "avec", "dans", "على", "في", "من", "عن", "إلى", "مع"}


def _like_terms(query: str) -> list[str]:
    words = [t for t in re.findall(r"\w{3,}", query.lower()) if t not in STOP]
    return words[:6]


def _enough(text: str, terms: list[str]) -> bool:
    """At least half of the query's words appear (names and labels are short, so one stray word is not a match)."""
    low = text.lower()
    return sum(1 for t in terms if t in low) >= max(1, (len(terms) + 1) // 2)


async def search_all(engine: Engine, workspace: dict[str, Any], user_id: str, query: str, *,
                     sources: list[str] | None = None, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    ws_id = workspace["id"]
    wanted = [s for s in (sources or SOURCES) if s in SOURCES]
    out: dict[str, list[dict[str, Any]]] = {}
    fq = fts_query(query)
    if "documents" in wanted:
        hits = await engine.knowledge.search(workspace, query, top_k=limit)
        out["documents"] = [{"id": h.get("document_id"), "title": h["name"], "page": h.get("page"),
                             "heading": h.get("heading"), "snippet": h["text"][:600], "source": h["source"],
                             "link": (f"documents/{h['document_id']}" + (f"?page={h['page']}" if h.get("page") else ""))
                             if h.get("document_id") else h.get("link"), "external": not h.get("document_id")}
                            for h in hits]
    if "conversations" in wanted and fq:
        rows = engine.db.all(
            "SELECT m.id, m.thread_id, m.role, m.created_at, t.title, snippet(messages_fts, 0, '«', '»', ' … ', 24) AS snip "
            "FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid JOIN threads t ON t.id = m.thread_id "
            "WHERE messages_fts MATCH ? AND t.workspace_id = ? AND (t.visibility = 'workspace' OR t.created_by = ?) "
            "ORDER BY bm25(messages_fts) LIMIT ?", (fq, ws_id, user_id, limit * 3))
        seen: set[str] = set()
        conv = []
        for r in rows:
            if r["thread_id"] in seen:
                continue
            seen.add(r["thread_id"])
            conv.append({"id": r["thread_id"], "title": r["title"], "snippet": r["snip"],
                         "who": "assistant" if r["role"] == "assistant" else "user", "at": r["created_at"],
                         "link": f"chat/{r['thread_id']}"})
        out["conversations"] = conv[:limit]
    if "memory" in wanted:
        mems = engine.memory.list(ws_id, user_id, "active", query)[:limit] if fq else []
        out["memory"] = [{"id": m["id"], "title": f"Memory ({m['scope']}, {m['category']})", "snippet": m["content"],
                          "link": f"memory?id={m['id']}"} for m in mems]
    terms = _like_terms(query)
    if "files" in wanted and terms:
        clauses = " OR ".join("lower(f.name) LIKE ? OR lower(v.model) LIKE ?" for _ in terms)
        params: list[Any] = [ws_id]
        for t in terms:
            params += [f"%{t}%", f"%{t}%"]
        rows = engine.db.all(f"SELECT f.id, f.name, f.kind, f.updated_at, v.model FROM files f JOIN file_versions v ON "
                             f"v.file_id = f.id AND v.version = f.current_version WHERE f.workspace_id = ? AND ({clauses}) "
                             f"ORDER BY f.updated_at DESC LIMIT ?", [*params, limit])
        out["files"] = []
        for r in rows:
            model = loads(r["model"], {})
            labels = [str(c[0].get("v")) for s in model.get("sheets", []) for b in s["blocks"] if b["type"] == "table"
                      for c in b["rows"][:40] if c and isinstance(c[0].get("v"), str)]
            if not _enough(r["name"] + " " + " ".join(labels), terms):
                continue
            hit = [x for x in labels if any(t in x.lower() for t in terms)][:5]
            out["files"].append({"id": r["id"], "title": r["name"], "snippet": ("Rows: " + ", ".join(hit)) if hit else
                                 f"{r['kind'].title()} · {len(labels)} rows", "link": f"files/{r['id']}"})
    if "datasets" in wanted and terms:
        clauses = " OR ".join("lower(name) LIKE ? OR lower(columns) LIKE ?" for _ in terms)
        params = [ws_id]
        for t in terms:
            params += [f"%{t}%", f"%{t}%"]
        rows = engine.db.all(f"SELECT id, name, source, row_count, columns FROM datasets WHERE workspace_id = ? AND "
                             f"({clauses}) ORDER BY refreshed_at DESC LIMIT ?", [*params, limit])
        out["datasets"] = [{"id": r["id"], "title": r["name"],
                            "snippet": f"{r['row_count']} rows from {r['source']}; columns: "
                                       + ", ".join(c["name"] for c in loads(r["columns"], [])[:12]),
                            "link": f"data/{r['id']}"} for r in rows
                           if _enough(r["name"] + " " + " ".join(c["name"] for c in loads(r["columns"], [])), terms)]
    return out


async def answer(engine: Engine, workspace: dict[str, Any], user_id: str, query: str,
                 results: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """A short cited answer over the top results (a few hundred tokens of context, one model call)."""
    from ..llm.base import ChatMessage
    from ..llm.registry import build_model, resolve_choice

    numbered: list[dict[str, Any]] = []
    for kind in ("documents", "memory", "conversations", "files"):
        for r in results.get(kind, [])[:4 if kind != "documents" else 6]:
            numbered.append({**r, "kind": kind, "n": len(numbered) + 1})
    if not numbered:
        return {"text": "Nothing in this workspace matches the question.", "sources": []}
    ctx = "\n\n".join(f"[{r['n']}] {r['title']}" + (f", page {r['page']}" if r.get("page") else "") + f"\n{r['snippet'][:700]}"
                      for r in numbered)
    pid, model_name = resolve_choice(engine, workspace, workspace.get("utility_provider_id"), workspace.get("utility_model"))
    llm = build_model(engine, workspace["id"], pid, model_name)
    result = None
    async for ev in llm.stream([ChatMessage("system", ANSWER_PROMPT),
                                ChatMessage("user", f"Question: {query}\n\nSources:\n{ctx}")], []):
        if ev.type == "done":
            result = ev.result
    assert result is not None
    engine.record_usage(workspace["id"], pid, model_name, result.usage, "search")
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", result.text) if 0 < int(n) <= len(numbered)})
    return {"text": result.text.strip(), "model": model_name,
            "sources": [{k: r.get(k) for k in ("n", "kind", "title", "page", "link", "snippet")} for r in numbered
                        if r["n"] in cited]}
