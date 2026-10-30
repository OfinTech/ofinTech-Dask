"""Document knowledge base: collections, upload (also chat attachments), search, download, settings, PipesHub."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.responses import Response

from ..db import dumps, new_id, now
from ..errors import Conflict, Invalid, NotFound
from ..knowledge.embed import Embedder
from ..knowledge.pipeshub import SCOPES, PipesHub
from ..security import check_outbound_url
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
    sync_pipeshub: bool = False


@router.post("/api/w/{ws}/collections", role="editor", body=CollectionIn)
async def add_collection(ctx: Ctx) -> dict[str, Any]:
    b: CollectionIn = ctx.body
    if ctx.engine.db.one("SELECT 1 FROM doc_collections WHERE workspace_id = ? AND lower(name) = lower(?)",
                         (ctx.ws_id, b.name.strip())):
        raise Conflict("A collection with this name exists.")
    cid = new_id("col_")
    ctx.engine.db.insert("doc_collections", {"id": cid, "workspace_id": ctx.ws_id, "name": b.name.strip(),
                                             "description": b.description, "sync_pipeshub": int(b.sync_pipeshub),
                                             "created_by": ctx.user_id, "created_at": now()})
    ctx.audit("collection.create", cid)
    return ctx.engine.db.one("SELECT * FROM doc_collections WHERE id = ?", (cid,))


@router.patch("/api/w/{ws}/collections/{cid}", role="editor", body=CollectionIn)
async def edit_collection(ctx: Ctx) -> dict[str, Any]:
    cid = ctx.request.path_params["cid"]
    b: CollectionIn = ctx.body
    if not ctx.engine.db.update("doc_collections", cid, {"name": b.name.strip(), "description": b.description,
                                                         "sync_pipeshub": int(b.sync_pipeshub)}, workspace_id=ctx.ws_id):
        raise NotFound("Collection not found.")
    if b.sync_pipeshub:  # send existing documents
        for d in ctx.engine.db.all("SELECT id FROM documents WHERE collection_id = ? AND status = 'indexed' AND "
                                   "(pipeshub_status IS NULL OR pipeshub_status != 'sent')", (cid,)):
            ctx.engine.knowledge._spawn(ctx.engine.knowledge.push_to_pipeshub(ctx.ws_id, d["id"]))
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
    ctx.engine.knowledge._spawn(ctx.engine.knowledge.push_to_pipeshub(ctx.ws_id, did))
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


# --- settings: embeddings and PipesHub ------------------------------------------------------------------------


@router.get("/api/w/{ws}/knowledge/settings", role="viewer")
async def settings(ctx: Ctx) -> dict[str, Any]:
    ph = ctx.engine.db.one("SELECT id, label, status, config, last_error FROM connections WHERE workspace_id = ? AND "
                           "provider = 'pipeshub'", (ctx.ws_id,))
    stats = ctx.engine.db.one("SELECT COUNT(*) AS documents, COALESCE(SUM(chunks), 0) AS chunks, "
                              "COALESCE(SUM(embedded), 0) AS embedded FROM documents WHERE workspace_id = ? AND "
                              "status = 'indexed'", (ctx.ws_id,))
    if ph:
        from ..db import loads

        cfg = loads(ph.pop("config"), {})
        ph.update({k: cfg.get(k) for k in ("base_url", "kb_id", "kb_name", "send")})
        ph["include_other_sources"] = bool(cfg.get("include_other_sources"))
        ph["sent"] = ctx.engine.db.scalar("SELECT COUNT(*) FROM documents WHERE workspace_id = ? AND "
                                          "pipeshub_status = 'sent'", (ctx.ws_id,))
    return {"embedding_provider_id": ctx.workspace.get("embedding_provider_id"),
            "embedding_model": ctx.workspace.get("embedding_model"), "pipeshub": ph, "stats": stats,
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


class PipesHubIn(BaseModel):
    base_url: str = Field(min_length=8, max_length=500, description="e.g. https://pipeshub.example.com")
    token: str | None = Field(default=None, min_length=8, max_length=4000, description="personal access token phpat_…")
    kb_id: str | None = Field(default=None, max_length=80, description="existing knowledge base; empty = create one")
    send: Literal["all", "collections"] = "all"
    include_other_sources: bool = False


@router.post("/api/w/{ws}/connections/pipeshub", role="admin", body=PipesHubIn)
async def connect_pipeshub(ctx: Ctx) -> dict[str, Any]:
    from ..db import loads

    b: PipesHubIn = ctx.body
    url = b.base_url.strip().rstrip("/").removesuffix("/api/v1")
    check_outbound_url(url, allow_private=ctx.engine.settings.allow_private_network_urls,
                       allow_http=ctx.engine.settings.allow_private_network_urls)
    existing = ctx.engine.db.one("SELECT * FROM connections WHERE workspace_id = ? AND provider = 'pipeshub'", (ctx.ws_id,))
    token = (b.token or "").strip()
    if not token:
        if not existing:
            raise Invalid("Enter a PipesHub personal access token.")
        token = ctx.engine.secrets.get(existing["secret_id"], workspace_id=ctx.ws_id)["token"]
    old = loads(existing["config"], {}) if existing else {}
    client = PipesHub(ctx.engine, base_url=url, token=token, ws_id=ctx.ws_id)
    await client.check()  # address and token
    kb_id = (b.kb_id or "").strip() or (old.get("kb_id") if old.get("base_url") == url else None)
    if kb_id:
        kb_name = await client.kb_name(kb_id)
    else:
        kb_name = f"OfinTech Desk · {ctx.workspace['name']}"[:255]  # type: ignore[index]
        kb_id = await client.create_kb(kb_name)
    cid = existing["id"] if existing else new_id("cn_")
    sid = ctx.engine.secrets.put("connection", {"token": token}, workspace_id=ctx.ws_id, secret_id=f"sec_{cid}")
    config = {"base_url": url, "kb_id": kb_id, "kb_name": kb_name, "send": b.send,
              "include_other_sources": b.include_other_sources}
    row = {"id": cid, "workspace_id": ctx.ws_id, "provider": "pipeshub", "label": f"PipesHub: {url.split('//')[-1][:60]}",
           "status": "connected", "config": dumps(config), "secret_id": sid, "created_by": ctx.user_id,
           "created_at": now(), "last_error": None}
    if existing:
        ctx.engine.db.update("connections", cid, {k: row[k] for k in ("label", "status", "config", "secret_id",
                                                                      "last_error")}, workspace_id=ctx.ws_id)
    else:
        ctx.engine.db.insert("connections", row)
    if existing and old.get("kb_id") != kb_id:  # another knowledge base: send everything again
        ctx.engine.db.execute("UPDATE documents SET pipeshub_status = NULL, pipeshub_record_id = NULL "
                              "WHERE workspace_id = ?", (ctx.ws_id,))
    queued = 0
    for d in ctx.engine.db.all("SELECT id FROM documents WHERE workspace_id = ? AND status IN ('indexed','failed') AND "
                               "(pipeshub_status IS NULL OR pipeshub_status != 'sent')", (ctx.ws_id,)):
        ctx.engine.knowledge._spawn(_send_existing(ctx.engine, ctx.ws_id, d["id"]))
        queued += 1
    ctx.audit("connection.connected", cid, {"provider": "pipeshub", "kb_id": kb_id, "send": b.send,
                                            "include_other_sources": b.include_other_sources})
    return {"id": cid, "kb_id": kb_id, "kb_name": kb_name, "documents_queued": queued, "scopes": SCOPES}


async def _send_existing(engine: Any, ws_id: str, doc_id: str) -> None:
    doc = engine.knowledge.get(ws_id, doc_id)
    if doc["status"] == "failed":  # e.g. a scanned PDF: PipesHub may read it with OCR
        await engine.knowledge.process(ws_id, doc_id)
    else:
        await engine.knowledge.push_to_pipeshub(ws_id, doc_id)


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
