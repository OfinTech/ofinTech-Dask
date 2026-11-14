"""Knowledge base: documents → text → chunks → full-text index (SQLite FTS5, BM25) and optional
embeddings → hybrid retrieval (reciprocal rank fusion).

Every query is filtered by workspace; files are stored under the engine's data folder."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import numpy as np

from ..db import dumps, loads, new_id, now
from ..errors import AppError, Conflict, Invalid, NotFound
from .embed import workspace_embedder
from .extract import Section, extract, mime_for

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.knowledge")

CHUNK_CHARS = 1600
OVERLAP_CHARS = 200
MAX_FILE_MB = 50
RRF_K = 60
SLOW_SEARCH_MS = 500   # a search slower than this is logged as a warning, not as routine information
STOP = set("""a an and are as at be by for from has have how i in is it of on or our that the this to was were what when
where which who why will with does do did can could should would about into than then there their these those your you
we me my us""".split())


def rss_mb() -> float | None:
    """Resident memory of this process, or None where the platform does not expose it cheaply. Read from
    /proc so the figure is the memory held right now, not the peak that getrusage reports."""
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            pages = int(fh.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return round(pages * 4096 / 1048576, 1)


def chunk_sections(sections: list[Section]) -> list[tuple[int | None, str | None, str]]:
    """Paragraph-aware chunks of about CHUNK_CHARS with a small overlap; pages and headings kept."""
    out: list[tuple[int | None, str | None, str]] = []
    for sec in sections:
        paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[-•*]\s)", sec.text) if p.strip()]
        buf = ""
        for p in paras:
            while len(p) > CHUNK_CHARS:  # very long paragraph: hard split on sentence or space
                cut = max(p.rfind(". ", 0, CHUNK_CHARS), p.rfind(" ", 0, CHUNK_CHARS))
                cut = cut + 1 if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
                piece, p = p[:cut].strip(), p[cut:].strip()
                if buf:
                    out.append((sec.page, sec.heading, buf))
                    buf = buf[-OVERLAP_CHARS:]
                out.append((sec.page, sec.heading, (buf + " " + piece).strip() if buf else piece))
                buf = piece[-OVERLAP_CHARS:]
            if len(buf) + len(p) + 2 > CHUNK_CHARS and buf:
                out.append((sec.page, sec.heading, buf))
                tail = buf[-OVERLAP_CHARS:]
                tail = tail[tail.find(" ") + 1:] if " " in tail else tail
                buf = f"{tail}\n\n{p}"
            else:
                buf = f"{buf}\n\n{p}" if buf else p
        if buf.strip():
            out.append((sec.page, sec.heading, buf.strip()))
    return out


def fts_query(text: str) -> str | None:
    tokens = [t for t in re.findall(r"\w+", text.lower()) if t not in STOP and (len(t) > 1 or t.isdigit())]
    seen: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.append(t)
    if not seen:
        return None
    return " OR ".join(f'"{t}"' for t in seen[:16])


class Knowledge:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.tasks: set[asyncio.Task[None]] = set()
        # Least-recently-used, bounded by settings.knowledge_cache_mb. One entry is one workspace's
        # embedding matrix, which is float32 and grows with the number of chunks: an unbounded map of them
        # is how a server with many workspaces runs out of memory without any one workspace looking large.
        self._matrix_cache: OrderedDict[tuple[str, str], tuple[tuple[int, int], np.ndarray, list[int]]] = OrderedDict()

    # -- files ---------------------------------------------------------------------------------------
    @staticmethod
    def storage_key(ws_id: str, doc_id: str) -> str:
        if not doc_id.replace("_", "").isalnum() or not ws_id.replace("_", "").isalnum():
            raise Invalid("Bad document id.")
        return f"documents/{ws_id}/{doc_id}"

    async def read_file(self, ws_id: str, doc_id: str) -> bytes:
        return await self.engine.storage.get(self.storage_key(ws_id, doc_id))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def wait_idle(self) -> None:
        """Returns once no background processing is running. Waits only on tasks that are still pending and
        drops finished ones itself, so a finished task whose done-callback has not run yet can never make this
        loop spin (CI caught the earlier `gather` form doing exactly that for twenty minutes)."""
        while True:
            pending = [t for t in self.tasks if not t.done()]
            if not pending:
                self.tasks.difference_update([t for t in self.tasks if t.done()])
                return
            await asyncio.wait(pending)

    # -- collections ------------------------------------------------------------------------------------
    def collection(self, ws_id: str, collection_id: str | None) -> dict[str, Any] | None:
        if not collection_id:
            return None
        row = self.engine.db.one("SELECT * FROM doc_collections WHERE id = ? AND workspace_id = ?", (collection_id, ws_id))
        if row is None:
            raise NotFound("Collection not found.")
        return row

    # -- collections that follow a Google Drive folder -------------------------------------------------------
    async def sync_drive(self, ws_id: str, collection_id: str) -> dict[str, Any]:
        """Adds new files, replaces changed ones and removes deleted ones. Returns counts."""
        from ..connectors.google import GoogleClient
        from .extract import MIME, extension

        coll = self.collection(ws_id, collection_id)
        src = loads(coll.get("sync_source"), {}) if coll else {}
        if src.get("type") != "google_drive":
            raise Invalid("This collection does not follow a Drive folder.")
        conn = self.engine.db.one("SELECT * FROM connections WHERE id = ? AND workspace_id = ? AND provider = 'google'",
                                  (src["connection_id"], ws_id))
        if conn is None:
            raise Invalid("The Google connection for this folder was removed.")
        client = GoogleClient(self.engine, conn)
        files = await client.list_folder(src["folder_id"])
        existing = {loads(d["source_ref"], {}).get("file_id"): d for d in self.engine.db.all(
            "SELECT id, source_ref, updated_at FROM documents WHERE workspace_id = ? AND collection_id = ? AND "
            "source = 'google_drive'", (ws_id, collection_id))}
        counts = {"added": 0, "updated": 0, "removed": 0, "skipped": 0}
        seen = set()
        for f in files:
            name, mime = f["name"], f.get("mimeType", "")
            if mime != "application/vnd.google-apps.document" and extension(name) not in MIME:
                counts["skipped"] += 1
                continue
            seen.add(f["id"])
            old = existing.get(f["id"])
            old_mod = loads(old["source_ref"], {}).get("modified") if old else None
            if old and old_mod == f.get("modifiedTime"):
                continue
            try:
                content, filename = await client.download_document(f["id"])
                if old:
                    await self.delete(ws_id, old["id"], None)
                await self.add(ws_id, None, filename, content, collection_id=collection_id, source="google_drive",
                               source_ref={"file_id": f["id"], "modified": f.get("modifiedTime"),
                                           "folder_id": src["folder_id"]})
                counts["updated" if old else "added"] += 1
            except AppError as exc:
                log.info("drive sync skipped %s: %s", name, exc.message)
                counts["skipped"] += 1
        for file_id, d in existing.items():
            if file_id not in seen:
                await self.delete(ws_id, d["id"], None)
                counts["removed"] += 1
        src |= {"last_sync": now(), "last_result": counts}
        self.engine.db.update("doc_collections", collection_id, {"sync_source": dumps(src)}, workspace_id=ws_id)
        return counts

    async def sync_all_drive(self, max_age: float = 3600) -> None:
        for c in self.engine.db.all("SELECT id, workspace_id, sync_source FROM doc_collections WHERE sync_source IS NOT NULL"):
            src = loads(c["sync_source"], {})
            if src.get("type") == "google_drive" and now() - float(src.get("last_sync") or 0) > max_age:
                try:
                    await self.sync_drive(c["workspace_id"], c["id"])
                except AppError as exc:
                    log.warning("drive sync of %s failed: %s", c["id"], exc.message)

    def chat_collection(self, ws_id: str, user_id: str | None) -> str:
        row = self.engine.db.one("SELECT id FROM doc_collections WHERE workspace_id = ? AND name = 'Chat attachments'",
                                 (ws_id,))
        if row:
            return row["id"]
        cid = new_id("col_")
        self.engine.db.insert("doc_collections", {"id": cid, "workspace_id": ws_id, "name": "Chat attachments",
                                                  "description": "Files attached in conversations with the assistant.",
                                                  "created_by": user_id, "created_at": now()})
        return cid

    async def wait_ready(self, ws_id: str, doc_ids: list[str], timeout: float = 90) -> list[dict[str, Any]]:
        """Waits until attached documents are processed (or the timeout), then returns them."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while True:
            docs = [self.get(ws_id, d) for d in doc_ids]
            pending = [d for d in docs if d["status"] == "processing"]
            if not pending or loop.time() > end:
                return docs
            await asyncio.sleep(0.2)

    # -- documents --------------------------------------------------------------------------------------
    async def add(self, ws_id: str, user_id: str | None, filename: str, data: bytes, *, collection_id: str | None = None,
            source: str = "upload", source_ref: dict[str, Any] | None = None) -> dict[str, Any]:
        mime = mime_for(filename)
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise Invalid(f"Documents up to {MAX_FILE_MB} MB can be added.", code="document_too_large")
        if not data:
            raise Invalid("The file is empty.")
        self.collection(ws_id, collection_id)
        digest = hashlib.sha256(data).hexdigest()
        dup = self.engine.db.one("SELECT id, name FROM documents WHERE workspace_id = ? AND sha256 = ?", (ws_id, digest))
        if dup:
            raise Conflict(f"This file is already in the knowledge base as “{dup['name']}”.", code="duplicate_document",
                           details={"document_id": dup["id"]})
        doc_id = new_id("doc_")
        await self.engine.storage.put(self.storage_key(ws_id, doc_id), data, mime)
        ts = now()
        self.engine.db.insert("documents", {
            "id": doc_id, "workspace_id": ws_id, "collection_id": collection_id, "name": filename[:200], "mime": mime,
            "size": len(data), "sha256": digest, "source": source, "source_ref": dumps(source_ref or {}),
            "status": "processing", "created_by": user_id, "created_at": ts, "updated_at": ts})
        self.engine.audit(ws_id, user_id, "document.add", doc_id, {"name": filename, "source": source})
        self._spawn(self.process(ws_id, doc_id))
        return self.get(ws_id, doc_id)

    def get(self, ws_id: str, doc_id: str) -> dict[str, Any]:
        row = self.engine.db.one("SELECT d.*, c.name AS collection_name FROM documents d LEFT JOIN doc_collections c ON "
                                 "c.id = d.collection_id WHERE d.id = ? AND d.workspace_id = ?", (doc_id, ws_id))
        if row is None:
            raise NotFound("Document not found.")
        row["source_ref"] = loads(row["source_ref"], {})
        row.pop("sha256", None)
        return row

    def list(self, ws_id: str, collection_id: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT d.id, d.name, d.mime, d.size, d.status, d.error, d.pages, d.chunks, d.embedded, "
               "d.source, d.collection_id, c.name AS collection_name, d.created_at, d.updated_at, u.name AS created_by_name "
               "FROM documents d LEFT JOIN doc_collections c ON c.id = d.collection_id LEFT JOIN users u ON u.id = "
               "d.created_by WHERE d.workspace_id = ?")
        params: list[Any] = [ws_id]
        if collection_id:
            sql += " AND d.collection_id = ?"
            params.append(collection_id)
        return self.engine.db.all(sql + " ORDER BY d.created_at DESC LIMIT 2000", params)

    async def process(self, ws_id: str, doc_id: str) -> None:
        db = self.engine.db
        doc = db.one("SELECT * FROM documents WHERE id = ? AND workspace_id = ?", (doc_id, ws_id))
        if doc is None:
            return
        try:
            data = await self.read_file(ws_id, doc_id)
            sections = await asyncio.to_thread(extract, data, doc["name"])
            if not sections:
                raise Invalid("No text was found in this document.", code="empty_document")
            chunks = chunk_sections(sections)
            pages = max((s.page or 0) for s in sections)  # 0 when the format has no pages (Word, text)
            with db.tx() as c:
                c.execute("DELETE FROM doc_chunks WHERE document_id = ?", (doc_id,))
                c.executemany("INSERT INTO doc_chunks (document_id, workspace_id, ord, page, heading, text) "
                              "VALUES (?,?,?,?,?,?)", [(doc_id, ws_id, i, p, h, t) for i, (p, h, t) in enumerate(chunks)])
                c.execute("UPDATE documents SET status = 'indexed', error = NULL, pages = ?, chunks = ?, embedded = 0, "
                          "updated_at = ? WHERE id = ?", (pages, len(chunks), now(), doc_id))
        except AppError as exc:
            db.execute("UPDATE documents SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                       (exc.message, now(), doc_id))
            return
        except Exception as exc:  # pragma: no cover - logged
            log.exception("processing %s failed", doc_id)
            self.engine.background_failed("document_processing")
            db.execute("UPDATE documents SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                       (f"Could not process the file ({type(exc).__name__}).", now(), doc_id))
            return
        await self.embed_document(ws_id, doc_id)

    async def embed_document(self, ws_id: str, doc_id: str) -> None:
        workspace = self.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws_id,))
        try:
            embedder = workspace_embedder(self.engine, workspace) if workspace else None
        except AppError as exc:
            self.engine.db.execute("UPDATE documents SET error = ? WHERE id = ?", (exc.message, doc_id))
            return
        if embedder is None:
            return
        rows = self.engine.db.all("SELECT id, heading, text FROM doc_chunks WHERE document_id = ? AND "
                                  "(embedding IS NULL OR embedding_model IS NOT ?) ORDER BY ord", (doc_id, embedder.model))
        if not rows:
            # Nothing to embed. That is not the same as nothing being embedded: every chunk may already carry a
            # vector from this very model. Whoever cleared the document's flag before calling this has to see it
            # put back, or the workspace reports no document as embedded while semantic search is working fine.
            if self.engine.db.scalar("SELECT COUNT(*) FROM doc_chunks WHERE document_id = ?", (doc_id,)):
                self.engine.db.execute("UPDATE documents SET embedded = 1, error = NULL, updated_at = ? WHERE id = ?",
                                       (now(), doc_id))
            return
        try:
            vectors = await embedder.embed([(f"{r['heading']}\n" if r["heading"] else "") + r["text"] for r in rows])
        except AppError as exc:
            self.engine.db.execute("UPDATE documents SET error = ? WHERE id = ?",
                                   (f"Keyword search works; embeddings failed: {exc.message}", doc_id))
            return
        with self.engine.db.tx() as c:
            c.executemany("UPDATE doc_chunks SET embedding = ?, embedding_model = ? WHERE id = ?",
                          [(v.astype(np.float32).tobytes(), embedder.model, r["id"]) for r, v in zip(rows, vectors, strict=True)])
            c.execute("UPDATE documents SET embedded = 1, error = NULL, updated_at = ? WHERE id = ?", (now(), doc_id))

    async def delete(self, ws_id: str, doc_id: str, user_id: str | None) -> None:
        doc = self.get(ws_id, doc_id)
        self.engine.db.execute("DELETE FROM documents WHERE id = ? AND workspace_id = ?", (doc_id, ws_id))
        await self.engine.storage.delete(self.storage_key(ws_id, doc_id))
        self.engine.audit(ws_id, user_id, "document.delete", doc_id, {"name": doc["name"]})

    def reembed_all(self, ws_id: str) -> int:
        """Queues the documents whose chunks are not already embedded with the workspace's current model.

        Saving the embedding settings calls this every time, including when provider and model have not changed.
        Queueing everything then meant clearing the embedded flag for the whole workspace and walking each
        document only to find nothing to do -- so re-saving an unchanged setting reported nothing as embedded
        and, on a large workspace, spent a pass over every document to reach that wrong answer."""
        workspace = self.engine.db.one("SELECT embedding_model FROM workspaces WHERE id = ?", (ws_id,))
        model = (workspace or {}).get("embedding_model")
        if not model:
            return 0
        docs = self.engine.db.all(
            "SELECT id FROM documents WHERE workspace_id = ? AND status = 'indexed' AND id IN ("
            "  SELECT DISTINCT document_id FROM doc_chunks WHERE workspace_id = ?"
            "   AND (embedding IS NULL OR embedding_model IS NOT ?))", (ws_id, ws_id, model))
        if not docs:
            return 0
        self.engine.db.execute(
            f"UPDATE documents SET embedded = 0 WHERE workspace_id = ? AND id IN ({', '.join('?' for _ in docs)})",
            [ws_id, *(d["id"] for d in docs)])

        async def run() -> None:
            for d in docs:
                await self.embed_document(ws_id, d["id"])

        self._spawn(run())
        return len(docs)

    async def resume(self) -> None:
        for d in self.engine.db.all("SELECT id, workspace_id FROM documents WHERE status = 'processing'"):
            self._spawn(self.process(d["workspace_id"], d["id"]))

    def text(self, ws_id: str, doc_id: str, page_from: int | None = None, page_to: int | None = None,
             max_chars: int = 20000) -> str:
        self.get(ws_id, doc_id)
        sql = "SELECT page, text FROM doc_chunks WHERE document_id = ? AND workspace_id = ?"
        params: list[Any] = [doc_id, ws_id]
        if page_from is not None:
            sql += " AND page >= ?"
            params.append(page_from)
        if page_to is not None:
            sql += " AND page <= ?"
            params.append(page_to)
        parts, total = [], 0
        for r in self.engine.db.all(sql + " ORDER BY ord", params):
            block = (f"[page {r['page']}]\n" if r["page"] else "") + r["text"]
            parts.append(block)
            total += len(block)
            if total > max_chars:
                break
        return "\n\n".join(parts)[:max_chars]

    # -- retrieval ---------------------------------------------------------------------------------------
    def _keyword(self, ws_id: str, query: str, collection_id: str | None, doc_ids: list[str] | None,
                 limit: int) -> list[int]:
        match = fts_query(query)
        if not match:
            return []
        sql = ("SELECT c.id FROM doc_chunks_fts f JOIN doc_chunks c ON c.id = f.rowid JOIN documents d ON d.id = "
               "c.document_id WHERE doc_chunks_fts MATCH ? AND c.workspace_id = ?")
        params: list[Any] = [match, ws_id]
        if collection_id:
            sql += " AND d.collection_id = ?"
            params.append(collection_id)
        if doc_ids:
            sql += f" AND d.id IN ({', '.join('?' for _ in doc_ids)})"
            params.extend(doc_ids)
        sql += " ORDER BY bm25(doc_chunks_fts, 1.0, 0.5) LIMIT ?"
        params.append(limit)
        return [r["id"] for r in self.engine.db.all(sql, params)]

    def cache_bytes(self) -> int:
        return sum(entry[1].nbytes for entry in self._matrix_cache.values())

    def forget(self, ws_id: str) -> None:
        """Drops a workspace's cached matrices. Called when the workspace goes, so a deleted tenant does not
        keep holding memory until the process restarts."""
        for key in [k for k in self._matrix_cache if k[0] == ws_id]:
            del self._matrix_cache[key]

    def _evict(self, keep: tuple[str, str]) -> None:
        """Trims the cache to the configured budget, oldest use first, never dropping the entry just used.

        A single workspace larger than the whole budget is still kept: it is the matrix this query needs, and
        rebuilding it from SQLite on every search costs far more than the memory. That case is logged, because
        it is the signal that the workspace has outgrown an exhaustive scan."""
        budget = max(0, self.engine.settings.knowledge_cache_mb) * 1048576
        if not budget:
            return
        held = self.cache_bytes()
        for key in list(self._matrix_cache):
            if held <= budget:
                return
            if key == keep:
                continue
            held -= self._matrix_cache[key][1].nbytes
            del self._matrix_cache[key]
        if held > budget:
            log.warning("embedding matrix for workspace %s is %.0f MB, over the %.0f MB cache budget; raise "
                        "DESK_KNOWLEDGE_CACHE_MB or move this workspace off an exhaustive scan",
                        keep[0], held / 1048576, budget / 1048576)

    def _matrix(self, ws_id: str, model: str) -> tuple[np.ndarray, list[int], bool]:
        """The workspace's embedding matrix and the chunk ids behind its rows, plus whether it came from cache.

        The signature is the number of embedded chunks and the highest chunk id. Chunk ids come from an
        AUTOINCREMENT column and so only ever grow, which makes the pair sufficient: an insert raises the
        maximum, a delete lowers the count, and no sequence of either can return both to what they were."""
        stamp = self.engine.db.one("SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS m FROM doc_chunks WHERE workspace_id = ? "
                                   "AND embedding_model = ?", (ws_id, model))
        key = (ws_id, model)
        sig = (stamp["n"], stamp["m"])
        cached = self._matrix_cache.get(key)
        if cached and cached[0] == sig:
            self._matrix_cache.move_to_end(key)
            return cached[1], cached[2], True
        rows = self.engine.db.all("SELECT id, embedding FROM doc_chunks WHERE workspace_id = ? AND embedding_model = ?",
                                  (ws_id, model))
        if not rows:
            self._matrix_cache.pop(key, None)
            return np.zeros((0, 1), dtype=np.float32), [], False
        ids = [r["id"] for r in rows]
        mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        self._matrix_cache[key] = (sig, mat, ids)
        self._matrix_cache.move_to_end(key)
        self._evict(key)
        return mat, ids, False

    async def _semantic(self, workspace: dict[str, Any], query: str, limit: int,
                        stats: dict[str, Any] | None = None) -> list[int]:
        """The chunk ids closest to the query. ``stats``, when given, is filled in with what the scan cost:
        how many vectors it compared, whether the matrix was already in memory, and where it stopped if it
        could not run. Nothing about the query or the documents is recorded."""
        note = stats if stats is not None else {}
        note["semantic"] = "off"
        try:
            embedder = workspace_embedder(self.engine, workspace)
        except AppError:
            note["semantic"] = "misconfigured"
            return []
        if embedder is None:
            return []
        mat, ids, hit = self._matrix(workspace["id"], embedder.model)
        note["cache"] = "hit" if hit else "build"
        note["scanned"] = len(ids)
        if not ids:
            note["semantic"] = "no vectors"
            return []
        try:
            q = (await embedder.embed([query]))[0]
        except AppError as exc:
            note["semantic"] = "query embedding failed"
            log.warning("query embedding failed: %s", exc.message)
            return []
        if mat.shape[1] != q.shape[0]:
            note["semantic"] = "dimension mismatch"
            return []
        note["semantic"] = "ok"
        scores = mat @ q
        top = np.argsort(-scores)[:limit]
        return [ids[i] for i in top if scores[i] > 0.15]

    async def search(self, workspace: dict[str, Any], query: str, *, top_k: int = 6, collection_id: str | None = None,
                     doc_ids: list[str] | None = None) -> list[dict[str, Any]]:
        ws_id = workspace["id"]
        stats: dict[str, Any] = {"semantic": "off", "cache": "-", "scanned": 0}
        started = time.perf_counter()
        keyword = self._keyword(ws_id, query, collection_id, doc_ids, 50)
        keyword_ms = (time.perf_counter() - started) * 1000
        semantic_started = time.perf_counter()
        semantic = await self._semantic(workspace, query, 50, stats)
        semantic_ms = (time.perf_counter() - semantic_started) * 1000
        if collection_id or doc_ids:
            allowed = {r["id"] for r in self.engine.db.all(
                "SELECT c.id FROM doc_chunks c JOIN documents d ON d.id = c.document_id WHERE c.workspace_id = ?"
                + (" AND d.collection_id = ?" if collection_id else "")
                + (f" AND d.id IN ({', '.join('?' for _ in doc_ids)})" if doc_ids else ""),
                [ws_id, *([collection_id] if collection_id else []), *(doc_ids or [])])}
            semantic = [i for i in semantic if i in allowed]
        fused: dict[int, float] = {}
        for ranking in (keyword, semantic):
            for rank, cid in enumerate(ranking):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(fused, key=lambda i: -fused[i])[: top_k * 2]
        hits: list[dict[str, Any]] = []
        if best:
            marks = ", ".join("?" for _ in best)
            rows = {r["id"]: r for r in self.engine.db.all(
                f"SELECT c.id, c.document_id, c.page, c.heading, c.text, d.name, d.collection_id FROM doc_chunks c JOIN "
                f"documents d ON d.id = c.document_id WHERE c.workspace_id = ? AND c.id IN ({marks})", [ws_id, *best])}
            for cid in best:
                r = rows.get(cid)
                if r:
                    hits.append({"source": "documents", "chunk_id": cid, "document_id": r["document_id"], "name": r["name"],
                                 "page": r["page"], "heading": r["heading"], "text": r["text"],
                                 "score": round(fused[cid], 5), "match": ("keyword+semantic" if cid in keyword and cid in semantic
                                                                          else "keyword" if cid in keyword else "semantic")})
        hits.sort(key=lambda h: -h["score"])
        hits = hits[:top_k]
        self._log_search(ws_id, stats, keyword_ms, semantic_ms, (time.perf_counter() - started) * 1000,
                         len(keyword), len(semantic), len(hits))
        return hits

    def _log_search(self, ws_id: str, stats: dict[str, Any], keyword_ms: float, semantic_ms: float,
                    total_ms: float, keyword_n: int, semantic_n: int, hits: int) -> None:
        """One line per search, so the cost of retrieval can be read off a running server rather than guessed.

        Only counts and timings are recorded. The query, the chunk text and the document names are not: a
        search line is written on every lookup, including the ones an agent makes on a customer's ledger, and a
        log is the easiest place to leak the contents of the thing being searched."""
        rss = rss_mb()
        line = ("search ws=%s chunks=%s cache=%s semantic=%s keyword_ms=%.1f semantic_ms=%.1f total_ms=%.1f "
                "cache_mb=%.1f keyword_hits=%s semantic_hits=%s returned=%s")
        args = (ws_id, stats.get("scanned", 0), stats.get("cache", "-"), stats.get("semantic", "off"),
                keyword_ms, semantic_ms, total_ms, self.cache_bytes() / 1048576, keyword_n, semantic_n, hits)
        if rss is not None:
            line, args = line + " rss_mb=%.1f", (*args, rss)
        if total_ms >= SLOW_SEARCH_MS:
            log.warning(line, *args)
        else:
            log.info(line, *args)
