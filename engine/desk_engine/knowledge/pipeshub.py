"""PipesHub (github.com/pipeshub-ai/pipeshub-ai, Apache-2.0) as the external document engine.

What it adds to the built-in knowledge base: its own parsing with OCR (scanned PDFs and images), knowledge graph
retrieval, and its 50+ connectors.

* Each workspace gets its own PipesHub knowledge base (``POST /api/v1/knowledgeBase``), or uses one the admin names.
* Documents are uploaded as the original files (``POST /api/v1/knowledgeBase/{kbId}/upload``, a Server-Sent
  Events stream), so PipesHub parses them itself. The record id is kept, and the record is deleted with the
  document (``DELETE /api/v1/knowledgeBase/record/{recordId}``).
* ``search_documents`` also calls ``POST /api/v1/retrieve`` filtered to that knowledge base (``filters.kb``), so a
  workspace only sees its own files. Records that belong to another workspace are dropped as a second check.
* Optionally the search covers everything the token's user can read in PipesHub (its connectors: SharePoint,
  Confluence, Google Drive, …). Every workspace member can then query that content, so it is admin-only and off by
  default.

Authentication is a PipesHub personal access token (``phpat_…``) with the scopes kb:read, kb:write, kb:upload,
kb:delete and semantic:write.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import httpx2 as httpx

from ..db import loads
from ..errors import UpstreamError

if TYPE_CHECKING:
    from ..app import Engine

SCOPES = ["kb:read", "kb:write", "kb:upload", "kb:delete", "semantic:write"]
# PipesHub refuses printf-style specifiers ("50% discount") and markup-like text in queries
_FORMAT_SPEC = re.compile(r"%(?:\d{1,10}[!$]|[#0\-+ ]{0,10}\d{0,10}\.?\d{0,10}[diouxXeEfFgGaAcspn%]|\d{1,10}[a-zA-Z])")


def safe_query(text: str) -> str:
    q = _FORMAT_SPEC.sub(lambda m: m.group(0).replace("%", " percent "), text)
    q = re.sub(r"<[^>]{0,200}>", " ", q)
    q = re.sub(r"\bon(\w+)\s*=", r"on\1 =", q)  # "one=1" would trip the inline-handler filter
    return " ".join(q.split())[:4000]


def upload_name(name: str, doc_id: str) -> str:
    """PipesHub skips a file whose name already exists in the folder, so the document id is part of the name."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^\w\-. ()&]+", " ", stem).strip()[:120] or "document"
    return f"{stem} ({doc_id[-8:]})" + (f".{ext.lower()}" if ext else "")


def parse_sse(body: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", body):
        event, data = "message", []
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            raw = "\n".join(data)
            try:
                payload: Any = json.loads(raw)
                if isinstance(payload, str):  # data is a JSON-encoded string
                    payload = json.loads(payload)
            except ValueError:
                payload = raw
            events.append((event, payload))
    return events


class PipesHub:
    def __init__(self, engine: Engine, connection: dict[str, Any] | None = None, *, base_url: str | None = None,
                 token: str | None = None) -> None:
        self.engine = engine
        self.connection = connection
        self.config = loads(connection["config"], {}) if connection else {}
        secret = (engine.secrets.get(connection["secret_id"], workspace_id=connection["workspace_id"])
                  if connection else {})
        self.base = (base_url or self.config.get("base_url") or "").rstrip("/")
        self.headers = {"Authorization": f"Bearer {token or secret.get('token', '')}", "User-Agent": "OfinTech-Desk"}

    @property
    def kb_id(self) -> str | None:
        return self.config.get("kb_id")

    async def _request(self, method: str, path: str, *, json_body: Any = None, files: Any = None, data: Any = None,
                       params: Any = None, timeout: float = 60) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.engine.http_transport) as client:
                resp = await client.request(method, f"{self.base}/api/v1{path}", json=json_body, files=files, data=data,
                                            params=params, headers=self.headers)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"PipesHub is not reachable at {self.base} ({type(exc).__name__}).",
                                code="pipeshub_unreachable") from exc
        if resp.status_code >= 400 and not (resp.status_code == 404 and method == "DELETE"):
            detail = ""
            try:
                body = resp.json()
                err = body.get("error") if isinstance(body, dict) else None
                detail = str((err or {}).get("message") if isinstance(err, dict) else body.get("message") or err or "")
            except (ValueError, AttributeError):
                detail = resp.text[:200]
            if resp.status_code == 401:
                raise UpstreamError("PipesHub rejected the access token (expired or revoked).", code="pipeshub_auth")
            if resp.status_code == 403:
                raise UpstreamError(f"PipesHub refused the request: {detail or 'missing permission'}. The token needs "
                                    f"the scopes {', '.join(SCOPES)}.", code="pipeshub_forbidden")
            raise UpstreamError(f"PipesHub returned HTTP {resp.status_code}: {detail[:200]}".strip(),
                                code="pipeshub_error")
        return resp

    # -- set-up ----------------------------------------------------------------------------------------------
    async def check(self) -> None:
        await self._request("GET", "/knowledgeBase", params={"limit": 1})

    async def create_kb(self, name: str) -> str:
        resp = await self._request("POST", "/knowledgeBase", json_body={"kbName": name[:255]})
        kb = resp.json()
        if not kb.get("id"):
            raise UpstreamError("PipesHub did not return the new knowledge base's id.")
        return str(kb["id"])

    async def kb_name(self, kb_id: str) -> str:
        resp = await self._request("GET", f"/knowledgeBase/{kb_id}")
        body = resp.json()
        return str(body.get("name") or body.get("kbName") or kb_id)

    # -- documents -------------------------------------------------------------------------------------------
    async def upload(self, filename: str, data: bytes, mime: str, *, last_modified_ms: int) -> str:
        if not self.kb_id:
            raise UpstreamError("No PipesHub knowledge base is set for this workspace.")
        meta = json.dumps([{"file_path": filename, "last_modified": last_modified_ms}])
        resp = await self._request("POST", f"/knowledgeBase/{self.kb_id}/upload",
                                   files=[("files", (filename, data, mime or "application/octet-stream"))],
                                   data={"files_metadata": meta, "isVersioned": "true"}, timeout=300)
        events = parse_sse(resp.text) if "event-stream" in resp.headers.get("content-type", "") else \
            [("file:succeeded", r) for r in (resp.json().get("records") or [])]
        for event, payload in events:
            if event == "file:succeeded" and isinstance(payload, dict) and payload.get("recordId"):
                return str(payload["recordId"])
            if event == "file:failed" and isinstance(payload, dict):
                reason = payload.get("reason") or payload.get("stage") or "failed"
                errors = payload.get("errors") or []
                msg = {"EXCEEDS_SIZE_LIMIT": "the file is larger than PipesHub's limit",
                       "UNSUPPORTED_TYPE": "PipesHub does not support this file type",
                       "DUPLICATE_NAME": "a file with this name is already in the knowledge base"}.get(reason, reason)
                raise UpstreamError(f"PipesHub did not accept the file: {msg}" + (f" ({errors[0]})" if errors else "") + ".",
                                    code="pipeshub_upload")
            if event == "error":
                raise UpstreamError(f"PipesHub upload failed: {payload.get('message') if isinstance(payload, dict) else payload}",
                                    code="pipeshub_upload")
        raise UpstreamError("PipesHub did not confirm the upload.", code="pipeshub_upload")

    async def delete(self, record_id: str) -> None:
        await self._request("DELETE", f"/knowledgeBase/record/{record_id}")

    async def retrieve(self, query: str, limit: int) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"query": safe_query(query), "limit": max(1, min(limit, 50))}
        if not self.config.get("include_other_sources"):
            if not self.kb_id:
                return []
            body["filters"] = {"kb": [self.kb_id]}
        resp = await self._request("POST", "/retrieve", json_body=body)
        out = []
        for c in resp.json().get("chunks") or []:
            rec = c.get("record") or {}
            cit = c.get("citation") or {}
            out.append({"record_id": rec.get("recordId"), "name": rec.get("name") or "PipesHub record",
                        "connector": rec.get("connector"), "text": c.get("text") or "",
                        "page": cit.get("pageNum"), "url": cit.get("url") or cit.get("recordUrl"),
                        "score": float(c.get("score") or 0)})
        return out


def pipeshub_for(engine: Engine, ws_id: str) -> PipesHub | None:
    row = engine.db.one("SELECT * FROM connections WHERE workspace_id = ? AND provider = 'pipeshub' AND "
                        "status = 'connected' LIMIT 1", (ws_id,))
    return PipesHub(engine, row) if row else None
