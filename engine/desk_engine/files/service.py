"""Files: create, version, render, store, review and download evidence-linked workbooks and reports."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..db import dumps, loads, new_id, now
from ..errors import AppError, Conflict, Invalid, NotFound
from .evidence import Evidence
from .resolve import fmt_value, resolve
from .review import llm_review, run_checks
from .spec import FileSpec

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.files")

MIME = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _safe_name(title: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in " -_()&" else " " for ch in title)
    return " ".join(keep.split())[:80] or "OfinTech file"


class Files:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.tasks: set[asyncio.Task[None]] = set()

    # -- links back into the app ------------------------------------------------------------------------------
    def app_url(self, ws_id: str) -> Any:
        s = self.engine.settings

        def url(path: str) -> str | None:
            if not path:
                return None
            sep = "&" if "?" in path else "?"
            target = f"{path}{sep}ws={quote(ws_id)}"
            if s.is_desktop:
                return f"ofintech-desk://open/{target}"
            return f"{s.public_url.rstrip('/')}/#/{target}"

        return url

    # -- create / revise ------------------------------------------------------------------------------------------
    async def create(self, workspace: dict[str, Any], user_id: str, spec: FileSpec, *, thread_id: str | None,
                     file_id: str | None = None, note: str | None = None, review: bool | None = None) -> dict[str, Any]:
        ws_id = workspace["id"]
        existing = None
        if file_id:
            existing = self.engine.db.one("SELECT * FROM files WHERE id = ? AND workspace_id = ?", (file_id, ws_id))
            if existing is None:
                raise NotFound("File not found.")
            thread_id = thread_id or existing["thread_id"]
        evidence = Evidence.for_thread(self.engine, thread_id)
        model = resolve(self.engine, workspace, spec, evidence)
        checks = run_checks(model)
        ts = now()
        version = (existing["current_version"] + 1) if existing else 1
        fid = existing["id"] if existing else new_id("f_")
        with self.engine.db.tx() as c:
            if existing:
                c.execute("UPDATE files SET name = ?, kind = ?, current_version = ?, review_status = 'none', "
                          "review_error = NULL, updated_at = ? WHERE id = ?", (spec.title, spec.kind, version, ts, fid))
            else:
                c.execute("INSERT INTO files (id, workspace_id, thread_id, name, kind, current_version, created_by, "
                          "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (fid, ws_id, thread_id, spec.title, spec.kind, version, user_id, ts, ts))
            c.execute("INSERT INTO file_versions (id, file_id, workspace_id, version, spec, model, note, created_by, "
                      "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (new_id("fv_"), fid, ws_id, version, spec.model_dump_json(), dumps(model),
                       note or ("Created" if version == 1 else "Revised"), user_id, ts))
            for f in checks:
                c.execute("INSERT INTO file_findings (id, file_id, workspace_id, version, origin, target, severity, "
                          "title, body, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (new_id("ff_"), fid, ws_id, version, "check", f["target"], f["severity"], f["title"],
                           f.get("body") or "", ts))
        await self.render(ws_id, fid, version)
        self.engine.audit(ws_id, user_id, "file.create" if version == 1 else "file.revise", fid,
                          {"version": version, "kind": spec.kind})
        if (review if review is not None else bool(workspace.get("auto_review", 1))):
            self.start_review(workspace, fid, user_id)
        return self.get(ws_id, fid)

    async def refresh(self, workspace: dict[str, Any], user_id: str, file_id: str) -> dict[str, Any]:
        v = self._version(workspace["id"], file_id, None)
        if str(v["file"]["kind"]).startswith("user_"):
            raise Invalid("Uploaded files have no data to refresh; ask the assistant to update the figures.")
        spec = FileSpec.model_validate_json(v["spec"])
        return await self.create(workspace, user_id, spec, thread_id=None, file_id=file_id,
                                 note="Refreshed with the latest data")

    # -- the user's own Excel and Word files --------------------------------------------------------------------------
    async def import_upload(self, workspace: dict[str, Any], user_id: str, name: str, data: bytes, *,
                            thread_id: str | None = None, document_id: str | None = None) -> dict[str, Any]:
        """Keep an uploaded .xlsx/.docx as version 1 of an editable file (the original, untouched)."""
        from ..platform.storage import check_quota
        from .editable import at_risk, detect_format, outline_docx, outline_xlsx

        check_quota(self.engine, workspace["id"], len(data))
        fmt = detect_format(name, data)
        outline = await asyncio.to_thread(outline_xlsx if fmt == "xlsx" else outline_docx, data)
        ws_id, ts, fid = workspace["id"], now(), new_id("f_")
        title = name.rsplit(".", 1)[0][:120] or "Uploaded file"
        model = {"kind": "upload", "format": fmt, "outline": outline, "edits": [], "original_name": name[:200],
                 "at_risk": at_risk(data, fmt), "summary": {"edits": 0, "sourced": 0, "unverified": 0, "formulas": 0}}
        spec = {"upload": True, "format": fmt, "name": name[:200], "document_id": document_id}
        with self.engine.db.tx() as c:
            c.execute("INSERT INTO files (id, workspace_id, thread_id, name, kind, current_version, created_by, "
                      "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (fid, ws_id, thread_id, title, f"user_{fmt}", 1, user_id, ts, ts))
            c.execute("INSERT INTO file_versions (id, file_id, workspace_id, version, spec, model, note, created_by, "
                      "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (new_id("fv_"), fid, ws_id, 1, dumps(spec), dumps(model), "Original, as uploaded", user_id, ts))
        await self._store(ws_id, fid, 1, fmt, data)
        self.engine.audit(ws_id, user_id, "file.upload", fid, {"format": fmt, "size": len(data)})
        return self.get(ws_id, fid)

    async def _store(self, ws_id: str, file_id: str, version: int, fmt: str, data: bytes) -> None:
        key = f"files/{ws_id}/{file_id}/v{version}.{fmt}"
        await self.engine.storage.put(key, data, MIME[fmt])
        outputs = {fmt: {"key": key, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        self.engine.db.execute("UPDATE file_versions SET outputs = ? WHERE file_id = ? AND version = ?",
                               (dumps(outputs), file_id, version))

    def _upload(self, ws_id: str, file_id: str, version: int | None = None) -> dict[str, Any]:
        v = self._version(ws_id, file_id, version)
        if not str(v["file"]["kind"]).startswith("user_"):
            raise Invalid("This file was created by the assistant; revise it with create_workbook or create_report "
                          "and its file_id.", code="not_an_upload")
        return v

    async def inspect_upload(self, ws_id: str, file_id: str, *, sheet: str | None = None, cell_range: str | None = None,
                             start: int = 0, version: int | None = None) -> str:
        from .editable import inspect_docx, inspect_xlsx

        v = self._upload(ws_id, file_id, version)
        fmt = loads(v["model"], {})["format"]
        data, _, _ = await self.download(ws_id, file_id, fmt, v["version"])
        if fmt == "xlsx":
            return await asyncio.to_thread(inspect_xlsx, data, sheet, cell_range)
        return await asyncio.to_thread(inspect_docx, data, start)

    async def edit_upload(self, workspace: dict[str, Any], user_id: str, file_id: str, ops: list[dict[str, Any]], *,
                          thread_id: str | None, note: str | None = None) -> dict[str, Any]:
        """Apply edits to the latest version and save the result as a new version with its edit log."""
        from .editable import MAX_OPS, apply_docx, apply_xlsx, outline_docx, outline_xlsx, summarize

        if not ops:
            raise Invalid("Give at least one edit.")
        if len(ops) > MAX_OPS:
            raise Invalid(f"At most {MAX_OPS} edits at a time.")
        ws_id = workspace["id"]
        v = self._upload(ws_id, file_id, None)
        model = loads(v["model"], {})
        fmt = model["format"]
        data, _, _ = await self.download(ws_id, file_id, fmt, v["version"])
        evidence = Evidence.for_thread(self.engine, thread_id or v["file"]["thread_id"])
        fn = apply_xlsx if fmt == "xlsx" else apply_docx
        new, log = await asyncio.to_thread(fn, data, ops, evidence)
        outline = await asyncio.to_thread(outline_xlsx if fmt == "xlsx" else outline_docx, new)
        version, ts = v["version"] + 1, now()
        new_model = model | {"outline": outline, "edits": log, "summary": summarize(log), "at_risk": []}
        with self.engine.db.tx() as c:
            c.execute("UPDATE files SET current_version = ?, updated_at = ?, thread_id = coalesce(thread_id, ?) "
                      "WHERE id = ?", (version, ts, thread_id, file_id))
            c.execute("INSERT INTO file_versions (id, file_id, workspace_id, version, spec, model, note, created_by, "
                      "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (new_id("fv_"), file_id, ws_id, version, v["spec"], dumps(new_model),
                       (note or "Edited by the assistant")[:300], user_id, ts))
            for e in log:
                if e.get("status") == "unverified":
                    figs = ", ".join(e.get("unverified") or []) or str(e.get("after"))
                    c.execute("INSERT INTO file_findings (id, file_id, workspace_id, version, origin, target, severity, "
                              "title, body, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (new_id("ff_"), file_id, ws_id, version, "check", e["target"], "medium",
                               "Figure without a source", f"{figs} was not found in the conversation's data or "
                               "documents. Check it before sharing.", ts))
        await self._store(ws_id, file_id, version, fmt, new)
        self.engine.audit(ws_id, user_id, "file.edit", file_id, {"version": version, "edits": len(log)})
        return self.get(ws_id, file_id)

    # -- rendering and storage -------------------------------------------------------------------------------------
    def _version(self, ws_id: str, file_id: str, version: int | None) -> dict[str, Any]:
        f = self.engine.db.one("SELECT * FROM files WHERE id = ? AND workspace_id = ?", (file_id, ws_id))
        if f is None:
            raise NotFound("File not found.")
        n = version or f["current_version"]
        v = self.engine.db.one("SELECT * FROM file_versions WHERE file_id = ? AND version = ?", (file_id, n))
        if v is None:
            raise NotFound("That version does not exist.")
        return v | {"file": f}

    def findings(self, ws_id: str, file_id: str, version: int) -> list[dict[str, Any]]:
        return self.engine.db.all("SELECT f.*, u.name AS decided_by_name FROM file_findings f LEFT JOIN users u ON "
                                  "u.id = f.decided_by WHERE f.file_id = ? AND f.workspace_id = ? AND f.version = ? "
                                  "ORDER BY CASE f.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                                  "f.created_at", (file_id, ws_id, version))

    async def render(self, ws_id: str, file_id: str, version: int) -> dict[str, Any]:
        from .docx import render_docx
        from .xlsx import render_xlsx

        v = self._version(ws_id, file_id, version)
        model = loads(v["model"], {})
        if model.get("kind") == "upload":  # the user's own file is stored as edited, never re-rendered
            return loads(v["outputs"], {})
        findings = self.findings(ws_id, file_id, version)
        author = self.engine.db.scalar("SELECT name FROM users WHERE id = ?", (v["created_by"],))
        wsname = self.engine.db.scalar("SELECT name FROM workspaces WHERE id = ?", (ws_id,))
        meta = {"author": author, "workspace": wsname, "version": version}
        url = self.app_url(ws_id)
        outputs: dict[str, Any] = {}
        blobs = {"xlsx": await asyncio.to_thread(render_xlsx, model, findings=findings, meta=meta, app_url=url)}
        if model.get("kind") == "report":
            blobs["docx"] = await asyncio.to_thread(render_docx, model, findings=findings, meta=meta, app_url=url)
        for fmt, data in blobs.items():
            key = f"files/{ws_id}/{file_id}/v{version}.{fmt}"
            await self.engine.storage.put(key, data, MIME[fmt])
            outputs[fmt] = {"key": key, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.engine.db.execute("UPDATE file_versions SET outputs = ? WHERE file_id = ? AND version = ?",
                               (dumps(outputs), file_id, version))
        return outputs

    async def download(self, ws_id: str, file_id: str, fmt: str, version: int | None) -> tuple[bytes, str, str]:
        v = self._version(ws_id, file_id, version)
        outputs = loads(v["outputs"], {})
        if fmt not in outputs:
            raise NotFound(f"This file has no {fmt} version.")
        data = await self.engine.storage.get(outputs[fmt]["key"])
        name = f"{_safe_name(v['file']['name'])} v{v['version']}.{fmt}"
        return data, name, MIME[fmt]

    # -- reading -------------------------------------------------------------------------------------------------------
    def list(self, ws_id: str) -> list[dict[str, Any]]:
        rows = self.engine.db.all(
            "SELECT f.id, f.name, f.kind, f.current_version, f.review_status, f.thread_id, f.created_at, f.updated_at, "
            "u.name AS created_by_name, (SELECT COUNT(*) FROM file_findings x WHERE x.file_id = f.id AND x.version = "
            "f.current_version AND x.status = 'open') AS open_findings FROM files f LEFT JOIN users u ON "
            "u.id = f.created_by WHERE f.workspace_id = ? ORDER BY f.updated_at DESC LIMIT 500", (ws_id,))
        for r in rows:
            m = self.engine.db.one("SELECT model FROM file_versions WHERE file_id = ? AND version = ?",
                                   (r["id"], r["current_version"]))
            r["summary"] = loads(m["model"], {}).get("summary", {}) if m else {}
        return rows

    def get(self, ws_id: str, file_id: str, version: int | None = None) -> dict[str, Any]:
        v = self._version(ws_id, file_id, version)
        f = v.pop("file")
        versions = self.engine.db.all(
            "SELECT v.version, v.note, v.created_at, v.outputs, u.name AS created_by_name FROM file_versions v LEFT JOIN "
            "users u ON u.id = v.created_by WHERE v.file_id = ? ORDER BY v.version DESC", (file_id,))
        for x in versions:
            x["formats"] = sorted(loads(x.pop("outputs"), {}))
        model = loads(v["model"], {})
        out = {"file": f, "version": v["version"], "note": v["note"], "model": model, "versions": versions,
               "findings": self.findings(ws_id, file_id, v["version"]),
               "formats": sorted(loads(v["outputs"], {}))}
        if v["version"] > 1:
            prev = self.engine.db.one("SELECT model FROM file_versions WHERE file_id = ? AND version = ?",
                                      (file_id, v["version"] - 1))
            if prev:
                out["changes"] = changes(loads(prev["model"], {}), model)
        return out

    def cell(self, workspace: dict[str, Any], file_id: str, address: str, version: int | None) -> dict[str, Any]:
        from ..data.query import Filter, detail_rows

        v = self._version(workspace["id"], file_id, version)
        model = loads(v["model"], {})
        cell = find_cell(model, address)
        if cell is None:
            raise NotFound("No such cell in this file.")
        out: dict[str, Any] = {"cell": cell, "display": fmt_value(cell.get("v"), cell.get("f", "text"))}
        if cell.get("s"):
            out["source"] = next((s for s in model["sources"] if s["id"] == cell["s"]), None)
        if cell.get("d"):
            d = cell["d"]
            try:
                out["rows"] = detail_rows(self.engine, workspace, d["dataset"],
                                          [Filter.model_validate(x) for x in d["filters"]])
                out["rows"]["exact"] = d.get("exact", True)
            except AppError as exc:
                out["rows_error"] = exc.message
        out["findings"] = [f for f in self.findings(workspace["id"], file_id, v["version"]) if f["target"] == address]
        return out

    # -- findings -------------------------------------------------------------------------------------------------------
    async def decide(self, ws_id: str, file_id: str, finding_id: str, status: str, user_id: str) -> dict[str, Any]:
        if status not in {"open", "resolved", "dismissed"}:
            raise Invalid("Status must be open, resolved or dismissed.")
        row = self.engine.db.one("SELECT * FROM file_findings WHERE id = ? AND file_id = ? AND workspace_id = ?",
                                 (finding_id, file_id, ws_id))
        if row is None:
            raise NotFound("Finding not found.")
        self.engine.db.execute("UPDATE file_findings SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                               (status, None if status == "open" else user_id, None if status == "open" else now(),
                                finding_id))
        self.engine.audit(ws_id, user_id, f"file.finding.{status}", finding_id, {"file_id": file_id})
        await self.render(ws_id, file_id, row["version"])
        return self.engine.db.one("SELECT * FROM file_findings WHERE id = ?", (finding_id,)) or {}

    # -- review ----------------------------------------------------------------------------------------------------------
    def start_review(self, workspace: dict[str, Any], file_id: str, user_id: str) -> None:
        f = self.engine.db.one("SELECT * FROM files WHERE id = ? AND workspace_id = ?", (file_id, workspace["id"]))
        if f is None:
            raise NotFound("File not found.")
        if str(f["kind"]).startswith("user_"):
            raise Invalid("The independent review covers files the assistant builds; edits to your own files are "
                          "checked against sources as they are made.")
        if f["review_status"] == "running":
            raise Conflict("A review of this file is already running.")
        self.engine.db.execute("UPDATE files SET review_status = 'running', review_error = NULL WHERE id = ?", (file_id,))
        task = asyncio.get_running_loop().create_task(self._review(workspace, file_id, f["current_version"], user_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def wait_idle(self) -> None:
        # Same shape as Knowledge.wait_idle: wait on pending tasks only, never spin on finished ones.
        while True:
            pending = [t for t in self.tasks if not t.done()]
            if not pending:
                self.tasks.difference_update([t for t in self.tasks if t.done()])
                return
            await asyncio.wait(pending)

    async def _review(self, workspace: dict[str, Any], file_id: str, version: int, user_id: str) -> None:
        ws_id = workspace["id"]
        try:
            v = self._version(ws_id, file_id, version)
            model = loads(v["model"], {})
            checks = [f for f in self.findings(ws_id, file_id, version) if f["origin"] == "check"]
            found, summary, model_name = await llm_review(self.engine, workspace, model, checks, user_id)
            ts = now()
            with self.engine.db.tx() as c:
                c.execute("DELETE FROM file_findings WHERE file_id = ? AND version = ? AND origin = 'reviewer'",
                          (file_id, version))
                for f in found:
                    c.execute("INSERT INTO file_findings (id, file_id, workspace_id, version, origin, target, severity, "
                              "title, body, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (new_id("ff_"), file_id, ws_id, version, "reviewer", f["target"], f["severity"],
                               f["title"], f.get("body") or "", ts))
                c.execute("UPDATE file_versions SET note = coalesce(note, '') || ? WHERE file_id = ? AND version = ?",
                          (f" · reviewed by {model_name}", file_id, version))
            await self.render(ws_id, file_id, version)
            self.engine.db.execute("UPDATE files SET review_status = 'done', review_error = NULL WHERE id = ?",
                                   (file_id,))
            self.engine.audit(ws_id, None, "file.review", file_id, {"version": version, "findings": len(found),
                                                                    "model": model_name, "summary": summary[:300]})
            name = v["file"]["name"]
            self.engine.notify(ws_id, v["file"]["created_by"], "review",
                               f"Review of “{name}”: {len(found)} finding{'s' if len(found) != 1 else ''}.",
                               f"/files/{file_id}")
        except AppError as exc:
            self.engine.db.execute("UPDATE files SET review_status = 'error', review_error = ? WHERE id = ?",
                                   (exc.message, file_id))
        except Exception as exc:  # pragma: no cover - logged
            log.exception("review of %s failed", file_id)
            self.engine.db.execute("UPDATE files SET review_status = 'error', review_error = ? WHERE id = ?",
                                   (f"The review failed ({type(exc).__name__}).", file_id))

    # -- delete ----------------------------------------------------------------------------------------------------------
    async def delete(self, ws_id: str, file_id: str, user_id: str) -> None:
        f = self.engine.db.one("SELECT * FROM files WHERE id = ? AND workspace_id = ?", (file_id, ws_id))
        if f is None:
            raise NotFound("File not found.")
        from ..storage import delete_prefix

        self.engine.db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await delete_prefix(self.engine.storage, f"files/{ws_id}/{file_id}")
        self.engine.audit(ws_id, user_id, "file.delete", file_id, {"name": f["name"]})


def iter_cells(model: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in model.get("sheets", []):
        for b in s["blocks"]:
            if b["type"] == "table":
                for cells in b["rows"] + ([b["total"]] if b.get("total") else []):
                    out.extend(cells)
            elif b["type"] == "kpis":
                out.extend(it["cell"] for it in b["items"])
    return out


def find_cell(model: dict[str, Any], address: str) -> dict[str, Any] | None:
    return next((c for c in iter_cells(model) if c.get("a") == address), None)


def changes(before: dict[str, Any], after: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    """Figures that changed between two versions, matched by address."""
    old = {c["a"]: c for c in iter_cells(before) if c.get("st") != "label"}
    out = []
    for c in iter_cells(after):
        if c.get("st") == "label":
            continue
        p = old.get(c["a"])
        if p is None:
            continue
        a, b = p.get("v"), c.get("v")
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(a - b) > 0.005:
                out.append({"a": c["a"], "before": a, "after": b, "f": c.get("f")})
        elif a != b:
            out.append({"a": c["a"], "before": a, "after": b, "f": c.get("f")})
        if len(out) >= limit:
            break
    return out
