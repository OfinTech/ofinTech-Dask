"""Document knowledge base: collections, upload (also chat attachments), search, download, settings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.responses import Response

from ..db import dumps, new_id, now
from ..errors import Conflict, Invalid, NotFound
from ..knowledge.embed import Embedder
from ..web import Ctx, Router

router = Router()


# --- collections -----------------------------------------------------------------------------------


@router.get("/api/w/{ws}/collections", role="viewer")
async def collections(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.engine.db.all("SELECT c.*, (SELECT COUNT(*) FROM documents d WHERE d.collection_id = c.id) AS documents "
                             "FROM doc_collections c WHERE c.workspace_id = ? ORDER BY c.name", (ctx.ws_id,))


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


@router.post("/api/w/{ws}/collections", role="editor", body=CollectionIn)
async def add_collection(ctx: Ctx) -> dict[str, Any]:
    b: CollectionIn = ctx.body
    if ctx.engine.db.one("SELECT 1 FROM doc_collections WHERE workspace_id = ? AND lower(name) = lower(?)",
                         (ctx.ws_id, b.name.strip())):
        raise Conflict("A collection with this name exists.")
    cid = new_id("col_")
    ctx.engine.db.insert("doc_collections", {"id": cid, "workspace_id": ctx.ws_id, "name": b.name.strip(),
                                             "description": b.description,
                                             "created_by": ctx.user_id, "created_at": now()})
    ctx.audit("collection.create", cid)
    return ctx.engine.db.one("SELECT * FROM doc_collections WHERE id = ?", (cid,))


@router.patch("/api/w/{ws}/collections/{cid}", role="editor", body=CollectionIn)
async def edit_collection(ctx: Ctx) -> dict[str, Any]:
    cid = ctx.request.path_params["cid"]
    b: CollectionIn = ctx.body
    if not ctx.engine.db.update("doc_collections", cid, {"name": b.name.strip(), "description": b.description},
                                workspace_id=ctx.ws_id):
        raise NotFound("Collection not found.")
    return ctx.engine.db.one("SELECT * FROM doc_collections WHERE id = ?", (cid,))


@router.delete("/api/w/{ws}/collections/{cid}", role="editor")
async def delete_collection(ctx: Ctx) -> None:
    cid = ctx.request.path_params["cid"]
    if not ctx.engine.db.execute("DELETE FROM doc_collections WHERE id = ? AND workspace_id = ?", (cid, ctx.ws_id)):
        raise NotFound("Collection not found.")
    ctx.audit("collection.delete", cid)


# --- documents ------------------------------------------------------------------------------------------


@router.get("/api/w/{ws}/documents", role="viewer")
async def documents(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.engine.knowledge.list(ctx.ws_id, ctx.request.query_params.get("collection") or None)


@router.post("/api/w/{ws}/documents", role="viewer")  # viewers may attach files in chat; other uploads need editor
async def upload(ctx: Ctx) -> dict[str, Any]:
    if int(ctx.request.headers.get("content-length") or 0) > 210 * 1024 * 1024:
        raise Invalid("Upload at most 200 MB at a time.")
    added, failed = [], []
    async with ctx.request.form(max_files=20, max_fields=5) as form:
        chat = form.get("purpose") == "chat"  # attachments from the assistant's composer
        if not chat:
            ctx.require("editor")
        collection_id = form.get("collection_id") or None
        if isinstance(collection_id, str) and collection_id:
            ctx.engine.knowledge.collection(ctx.ws_id, collection_id)
        elif chat:
            collection_id = ctx.engine.knowledge.chat_collection(ctx.ws_id, ctx.user_id)
        else:
            collection_id = None
        files = [f for f in form.getlist("files") if not isinstance(f, str)]
        if not files:
            raise Invalid("Choose at least one file.")
        for f in files:
            data = await f.read()
            try:
                added.append(await ctx.engine.knowledge.add(ctx.ws_id, ctx.user_id, f.filename or "document", data,
                                                            collection_id=collection_id,
                                                            source="chat" if chat else "upload"))
            except Conflict as exc:
                dup = (exc.details or {}).get("document_id") if isinstance(exc.details, dict) else None
                if chat and dup:  # the same file attached again: reuse the document already here
                    added.append(ctx.engine.knowledge.get(ctx.ws_id, dup))
                else:
                    failed.append({"name": f.filename, "error": exc.message})
            except Invalid as exc:
                failed.append({"name": f.filename, "error": exc.message})
    return {"added": added, "failed": failed}


@router.get("/api/w/{ws}/documents/{did}", role="viewer")
async def document(ctx: Ctx) -> dict[str, Any]:
    did = ctx.request.path_params["did"]
    doc = ctx.engine.knowledge.get(ctx.ws_id, did)
    page = ctx.request.query_params.get("page")
    pf = int(page) if page and page.isdigit() else None
    return {"document": doc, "text": ctx.engine.knowledge.text(ctx.ws_id, did, pf, pf, 60000)}


class MoveIn(BaseModel):
    collection_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)


@router.patch("/api/w/{ws}/documents/{did}", role="editor", body=MoveIn)
async def edit_document(ctx: Ctx) -> dict[str, Any]:
    did = ctx.request.path_params["did"]
    ctx.engine.knowledge.get(ctx.ws_id, did)
    values: dict[str, Any] = {"updated_at": now()}
    if "collection_id" in ctx.body.model_fields_set:
        ctx.engine.knowledge.collection(ctx.ws_id, ctx.body.collection_id)
        values["collection_id"] = ctx.body.collection_id
    if ctx.body.name:
        values["name"] = ctx.body.name.strip()
    ctx.engine.db.update("documents", did, values, workspace_id=ctx.ws_id)
    return ctx.engine.knowledge.get(ctx.ws_id, did)


@router.delete("/api/w/{ws}/documents/{did}", role="editor")
async def delete_document(ctx: Ctx) -> None:
    await ctx.engine.knowledge.delete(ctx.ws_id, ctx.request.path_params["did"], ctx.user_id)


@router.post("/api/w/{ws}/documents/{did}/reindex", role="editor")
async def reindex(ctx: Ctx) -> dict[str, Any]:
    did = ctx.request.path_params["did"]
    ctx.engine.knowledge.get(ctx.ws_id, did)
    ctx.engine.db.execute("UPDATE documents SET status = 'processing', error = NULL WHERE id = ?", (did,))
    ctx.engine.knowledge._spawn(ctx.engine.knowledge.process(ctx.ws_id, did))
    return ctx.engine.knowledge.get(ctx.ws_id, did)


@router.get("/api/w/{ws}/documents/{did}/file", role="viewer")
async def download(ctx: Ctx) -> Response:
    doc = ctx.engine.knowledge.get(ctx.ws_id, ctx.request.path_params["did"])
    data = await ctx.engine.knowledge.read_file(ctx.ws_id, doc["id"])
    safe = "".join(ch if ch.isalnum() or ch in " -_.()" else "_" for ch in doc["name"])[:150]
    # always a download, never rendered inline by the app
    return Response(data, media_type="application/octet-stream",
                    headers={"content-disposition": f'attachment; filename="{safe}"'})


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    collection_id: str | None = None
    top_k: int = Field(default=8, ge=1, le=20)


@router.post("/api/w/{ws}/documents/search", role="viewer", body=SearchIn)
async def search(ctx: Ctx) -> list[dict[str, Any]]:
    b: SearchIn = ctx.body
    return await ctx.engine.knowledge.search(ctx.workspace, b.query, top_k=b.top_k, collection_id=b.collection_id)


# --- settings: embeddings ------------------------------------------------------------------------


@router.get("/api/w/{ws}/knowledge/settings", role="viewer")
async def settings(ctx: Ctx) -> dict[str, Any]:
    stats = ctx.engine.db.one("SELECT COUNT(*) AS documents, COALESCE(SUM(chunks), 0) AS chunks, "
                              "COALESCE(SUM(embedded), 0) AS embedded FROM documents WHERE workspace_id = ? AND "
                              "status = 'indexed'", (ctx.ws_id,))
    return {"embedding_provider_id": ctx.workspace.get("embedding_provider_id"),
            "embedding_model": ctx.workspace.get("embedding_model"), "stats": stats,
            "mode": "hybrid" if ctx.workspace.get("embedding_model") else "keyword"}


class EmbeddingIn(BaseModel):
    provider_id: str | None = None
    model: str | None = Field(default=None, max_length=200)


@router.put("/api/w/{ws}/knowledge/embeddings", role="admin", body=EmbeddingIn)
async def set_embeddings(ctx: Ctx) -> dict[str, Any]:
    b: EmbeddingIn = ctx.body
    if b.provider_id and b.model:
        vec = await Embedder(ctx.engine, ctx.ws_id, b.provider_id, b.model).embed(["test"])  # validates key and model
        dims = int(vec.shape[1])
    else:
        b.provider_id, b.model, dims = None, None, 0
    ctx.engine.db.update("workspaces", ctx.ws_id, {"embedding_provider_id": b.provider_id, "embedding_model": b.model})
    ctx.audit("knowledge.embeddings", ctx.ws_id, {"model": b.model})
    queued = 0
    if b.model:
        ws = ctx.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ctx.ws_id,))
        ctx.workspace.update(ws)
        queued = ctx.engine.knowledge.reembed_all(ctx.ws_id)
    return {"model": b.model, "dimensions": dims, "documents_queued": queued}


# --- collections that follow a Google Drive folder --------------------------------------------------------


class DriveFolderIn(BaseModel):
    folder: str = Field(min_length=5, max_length=500, description="folder link or id")
    connection_id: str | None = None


@router.post("/api/w/{ws}/collections/{cid}/drive", role="editor", body=DriveFolderIn)
async def follow_drive(ctx: Ctx) -> dict[str, Any]:
    import re

    from ..connectors.google import GoogleClient
    from ..connectors.oauth import get_connection

    cid = ctx.request.path_params["cid"]
    ctx.engine.knowledge.collection(ctx.ws_id, cid)
    m = re.search(r"folders/([A-Za-z0-9_-]+)", ctx.body.folder) or re.match(r"^([A-Za-z0-9_-]{10,})$", ctx.body.folder.strip())
    if not m:
        raise Invalid("Paste the Drive folder link (…/drive/folders/<id>) or its id.")
    conn = get_connection(ctx.engine, ctx.ws_id, ctx.body.connection_id, "google")
    name = await GoogleClient(ctx.engine, conn).folder_name(m.group(1))
    src = {"type": "google_drive", "connection_id": conn["id"], "folder_id": m.group(1), "folder_name": name}
    ctx.engine.db.update("doc_collections", cid, {"sync_source": dumps(src)}, workspace_id=ctx.ws_id)
    ctx.audit("collection.follow_drive", cid, {"folder": name})
    counts = await ctx.engine.knowledge.sync_drive(ctx.ws_id, cid)
    return {"folder_name": name, **counts}


@router.post("/api/w/{ws}/collections/{cid}/drive/sync", role="editor")
async def sync_drive(ctx: Ctx) -> dict[str, Any]:
    return await ctx.engine.knowledge.sync_drive(ctx.ws_id, ctx.request.path_params["cid"])


@router.delete("/api/w/{ws}/collections/{cid}/drive", role="editor")
async def unfollow_drive(ctx: Ctx) -> None:
    cid = ctx.request.path_params["cid"]
    ctx.engine.knowledge.collection(ctx.ws_id, cid)
    ctx.engine.db.update("doc_collections", cid, {"sync_source": None}, workspace_id=ctx.ws_id)
    ctx.audit("collection.unfollow_drive", cid)


# --- workspace search --------------------------------------------------------------------------------------


class WorkspaceSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    sources: list[Literal["documents", "conversations", "memory", "files", "datasets"]] | None = None
    answer: bool = False


@router.post("/api/w/{ws}/search", role="viewer", body=WorkspaceSearchIn)
async def workspace_search(ctx: Ctx) -> dict[str, Any]:
    from ..knowledge.search import answer, search_all

    b: WorkspaceSearchIn = ctx.body
    results = await search_all(ctx.engine, ctx.workspace, ctx.user_id, b.query, sources=b.sources)  # type: ignore[arg-type]
    out: dict[str, Any] = {"query": b.query, "results": results}
    if b.answer:
        out["answer"] = await answer(ctx.engine, ctx.workspace, ctx.user_id, b.query, results)  # type: ignore[arg-type]
    return out
