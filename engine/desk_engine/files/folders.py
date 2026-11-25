"""Folders for Files, and documents kept as they are.

Files used to be one flat list of what the assistant built and the Excel and Word files it fills in. People keep
more than that beside their books: the signed PDF, the bank's CSV, the scan of a contract. So Files has folders,
and a folder takes any document.

Two rules hold everything here:

- **Nothing is destroyed by tidying.** Deleting a folder moves what is in it to the folder above and removes one
  row. A folder that is not empty is not deleted until the person has confirmed that.
- **A stored document is kept, not run.** Excel and Word files stay editable by the assistant as before. Any other
  document is stored as it arrived and handed back as a download, never shown inline. Only document types are
  taken: a program, a script or a web page is refused by its name, and by its first bytes when the name lies.
"""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING, Any

from ..db import new_id, now
from ..errors import Invalid, NotFound
from .limits import check_office_zip

if TYPE_CHECKING:
    from ..app import Engine

MAX_NAME = 120
MAX_DEPTH = 8
MAX_FOLDERS = 2000
MAX_STORED_BYTES = 25 * 1024 * 1024

OOXML = "application/vnd.openxmlformats-officedocument."
# What a folder takes besides the Excel and Word files the assistant edits: documents, by extension.
STORED: dict[str, str] = {
    "pdf": "application/pdf",
    "csv": "text/csv", "tsv": "text/tab-separated-values", "txt": "text/plain", "md": "text/markdown",
    "json": "application/json", "xml": "application/xml", "rtf": "application/rtf",
    "xlsx": OOXML + "spreadsheetml.sheet", "docx": OOXML + "wordprocessingml.document",
    "pptx": OOXML + "presentationml.presentation",
    "xls": "application/vnd.ms-excel", "doc": "application/msword", "ppt": "application/vnd.ms-powerpoint",
    "ods": "application/vnd.oasis.opendocument.spreadsheet", "odt": "application/vnd.oasis.opendocument.text",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "tif": "image/tiff", "tiff": "image/tiff",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp", "heic": "image/heic",
    "eml": "message/rfc822", "msg": "application/vnd.ms-outlook",
}
MACROS = {"xlsm", "xltm", "xlsb", "docm", "dotm", "pptm", "ppsm"}
ZIPPED = {"xlsx", "docx", "pptx", "ods", "odt", "odp"}
OLE = {"xls", "doc", "ppt", "msg"}
TEXT = {"csv", "tsv", "txt", "md", "json", "xml", "eml", "rtf"}
# The first bytes of the picture formats taken, so that a renamed file is not kept as a picture.
MAGIC: dict[str, tuple[bytes, ...]] = {
    "png": (b"\x89PNG\r\n\x1a\n",), "jpg": (b"\xff\xd8\xff",), "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"), "bmp": (b"BM",), "tif": (b"II*\x00", b"MM\x00*"), "tiff": (b"II*\x00", b"MM\x00*"),
}
# How a program starts, whatever it is called: Windows and DOS, Linux, macOS (four byte orders), a script, Java.
PROGRAMS = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe", b"#!")


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def stored_format(name: str, data: bytes) -> str:
    """The extension a document is kept under, or ``Invalid`` saying why it is not taken."""
    ext = extension(name)
    if not data:
        raise Invalid("This file is empty.", code="file_format")
    if len(data) > MAX_STORED_BYTES:
        raise Invalid("Files up to 25 MB can be added.", code="file_too_large")
    if ext in MACROS:
        raise Invalid("Files with macros are not kept here. Save a copy without macros (.xlsx, .docx, .pptx) and "
                      "add that.", code="file_format")
    if ext not in STORED:
        raise Invalid("Only documents are kept here: PDF, Word, Excel, PowerPoint, CSV, text, pictures of "
                      "documents and e-mail files. Programs, scripts, web pages and archives are not.",
                      code="file_format")
    head = data[:8]
    refused = Invalid(f"This file is not a .{ext} document, whatever its name says.", code="file_format")
    if head.startswith(PROGRAMS):
        raise refused
    if ext == "pdf" and b"%PDF-" not in data[:1024]:
        raise refused
    if ext in ZIPPED:
        if not head.startswith(b"PK"):
            raise refused
        check_office_zip(data)
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile as exc:
            raise Invalid("This file is damaged.", code="file_format") from exc
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise Invalid("This file contains macros. Save a copy without them and add that.", code="file_format")
    if ext in OLE and not head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise refused
    if ext in MAGIC and not head.startswith(MAGIC[ext]):
        raise refused
    if ext == "webp" and not (head.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise refused
    if ext == "heic" and data[4:8] != b"ftyp":
        raise refused
    if ext in TEXT and b"\x00" in data[:4096] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise refused        # text in UTF-16 has NULs and says so in its first two bytes; anything else with one is not text
    return ext


def clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise Invalid("Give the folder a name.", code="folder_name")
    if len(text) > MAX_NAME:
        raise Invalid(f"A folder name has at most {MAX_NAME} characters.", code="folder_name")
    if any(ch in text for ch in "/\\") or any(ord(ch) < 32 for ch in text) or text in (".", ".."):
        raise Invalid("A folder name cannot contain / or \\.", code="folder_name")
    return text


class Folders:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # -- reading -------------------------------------------------------------------------------------------------------
    def _rows(self, ws_id: str) -> dict[str, dict[str, Any]]:
        return {r["id"]: r for r in self.engine.db.all(
            "SELECT id, parent_id, name, created_at, updated_at FROM file_folders WHERE workspace_id = ?", (ws_id,))}

    @staticmethod
    def _chain(rows: dict[str, dict[str, Any]], folder_id: str | None) -> list[dict[str, Any]]:
        """The folder and those above it, outermost first. A parent that is gone ends the chain there."""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        while folder_id and folder_id in rows and folder_id not in seen:
            seen.add(folder_id)
            out.append(rows[folder_id])
            folder_id = rows[folder_id]["parent_id"]
        return out[::-1]

    def path(self, ws_id: str, folder_id: str | None, rows: dict[str, dict[str, Any]] | None = None) -> str:
        return " / ".join(f["name"] for f in self._chain(rows if rows is not None else self._rows(ws_id), folder_id))

    def get(self, ws_id: str, folder_id: str) -> dict[str, Any]:
        row = self.engine.db.one("SELECT * FROM file_folders WHERE id = ? AND workspace_id = ?", (folder_id, ws_id))
        if row is None:
            raise NotFound("Folder not found.")
        return row

    def require(self, ws_id: str, folder_id: Any) -> str | None:
        """``None`` for the top level; otherwise the id of a folder that exists in this workspace."""
        if folder_id in (None, "", "root"):
            return None
        return str(self.get(ws_id, str(folder_id))["id"])

    def list(self, ws_id: str) -> list[dict[str, Any]]:
        rows = self._rows(ws_id)
        files = {r["folder_id"]: r["n"] for r in self.engine.db.all(
            "SELECT folder_id, COUNT(*) AS n FROM files WHERE workspace_id = ? AND folder_id IS NOT NULL GROUP BY "
            "folder_id", (ws_id,))}
        inside: dict[str, int] = {}
        for r in rows.values():
            if r["parent_id"]:
                inside[r["parent_id"]] = inside.get(r["parent_id"], 0) + 1
        out = [{**r, "parent_id": r["parent_id"] if r["parent_id"] in rows else None,
                "path": self.path(ws_id, r["id"], rows), "depth": len(self._chain(rows, r["id"])),
                "files": files.get(r["id"], 0), "folders": inside.get(r["id"], 0)} for r in rows.values()]
        return sorted(out, key=lambda f: f["path"].lower())

    # -- changing ------------------------------------------------------------------------------------------------------
    def _free(self, ws_id: str, parent_id: str | None, name: str, *, but: str | None = None) -> None:
        taken = self.engine.db.all(
            "SELECT id, name FROM file_folders WHERE workspace_id = ? AND parent_id IS ?", (ws_id, parent_id))
        if any(r["name"].casefold() == name.casefold() and r["id"] != but for r in taken):
            raise Invalid("A folder with this name is already here.", code="folder_exists")

    def create(self, ws_id: str, user_id: str, name: Any, parent_id: Any = None) -> dict[str, Any]:
        text, parent = clean_name(name), self.require(ws_id, parent_id)
        rows = self._rows(ws_id)
        if len(rows) >= MAX_FOLDERS:
            raise Invalid(f"A workspace has at most {MAX_FOLDERS} folders.", code="too_many_folders")
        if len(self._chain(rows, parent)) >= MAX_DEPTH:
            raise Invalid(f"Folders go at most {MAX_DEPTH} deep.", code="folder_depth")
        self._free(ws_id, parent, text)
        fid, ts = new_id("fo_"), now()
        self.engine.db.execute(
            "INSERT INTO file_folders (id, workspace_id, parent_id, name, created_by, created_at, updated_at) VALUES "
            "(?,?,?,?,?,?,?)", (fid, ws_id, parent, text, user_id, ts, ts))
        self.engine.audit(ws_id, user_id, "folder.create", fid, {"name": text, "parent": parent})
        return next(f for f in self.list(ws_id) if f["id"] == fid)

    def update(self, ws_id: str, user_id: str, folder_id: str, *, name: Any = None, parent_id: Any = None,
               move: bool = False) -> dict[str, Any]:
        """Rename, move, or both. ``move`` says the parent was given, since the top level is ``None``."""
        row = self.get(ws_id, folder_id)
        text = clean_name(name) if name is not None else row["name"]
        parent = self.require(ws_id, parent_id) if move else row["parent_id"]
        rows = self._rows(ws_id)
        if move:
            above = [f["id"] for f in self._chain(rows, parent)]
            if folder_id in above:
                raise Invalid("A folder cannot be moved into itself or into a folder inside it.", code="folder_loop")

            def height(fid: str) -> int:
                return 1 + max((height(r["id"]) for r in rows.values() if r["parent_id"] == fid), default=0)
            if len(above) + height(folder_id) > MAX_DEPTH:
                raise Invalid(f"Folders go at most {MAX_DEPTH} deep.", code="folder_depth")
        self._free(ws_id, parent, text, but=folder_id)
        self.engine.db.execute("UPDATE file_folders SET name = ?, parent_id = ?, updated_at = ? WHERE id = ? AND "
                               "workspace_id = ?", (text, parent, now(), folder_id, ws_id))
        self.engine.audit(ws_id, user_id, "folder.update", folder_id,
                          {"name": text, "parent": parent, "was": {"name": row["name"], "parent": row["parent_id"]}})
        return next(f for f in self.list(ws_id) if f["id"] == folder_id)

    def delete(self, ws_id: str, user_id: str, folder_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        """Remove one folder. What is in it moves to the folder above; nothing in it is deleted."""
        row = self.get(ws_id, folder_id)
        db = self.engine.db
        files = db.one("SELECT COUNT(*) AS n FROM files WHERE workspace_id = ? AND folder_id = ?", (ws_id, folder_id))
        inside = db.all("SELECT id, name FROM file_folders WHERE workspace_id = ? AND parent_id = ?", (ws_id, folder_id))
        moved = {"files": int(files["n"]) if files else 0, "folders": len(inside)}
        if (moved["files"] or moved["folders"]) and not confirmed:
            raise Invalid(f"This folder holds {moved['files']} file(s) and {moved['folders']} folder(s). Confirm, and "
                          "they are moved to the folder above; nothing is deleted.", code="folder_not_empty",
                          details=moved)
        parent = row["parent_id"]
        with db.tx() as c:
            siblings = {r["name"].casefold() for r in c.execute(
                "SELECT name FROM file_folders WHERE workspace_id = ? AND parent_id IS ? AND id != ?",
                (ws_id, parent, folder_id)).fetchall()}
            for sub in inside:
                # two folders of one name cannot share a parent: the one coming up says where it came from
                name, n = sub["name"], 1
                while name.casefold() in siblings:
                    n += 1
                    name = f"{sub['name']} (from {row['name']})" if n == 2 else f"{sub['name']} (from {row['name']}, {n - 1})"
                siblings.add(name.casefold())
                c.execute("UPDATE file_folders SET parent_id = ?, name = ?, updated_at = ? WHERE id = ?",
                          (parent, name, now(), sub["id"]))
            c.execute("UPDATE files SET folder_id = ? WHERE workspace_id = ? AND folder_id = ?", (parent, ws_id, folder_id))
            c.execute("DELETE FROM file_folders WHERE id = ? AND workspace_id = ?", (folder_id, ws_id))
        self.engine.audit(ws_id, user_id, "folder.delete", folder_id, {"name": row["name"], "moved_up": moved})
        return {"deleted": folder_id, "moved_up": moved, "to": parent}

    def move_file(self, ws_id: str, user_id: str, file_id: str, folder_id: Any) -> dict[str, Any]:
        f = self.engine.db.one("SELECT id, folder_id FROM files WHERE id = ? AND workspace_id = ?", (file_id, ws_id))
        if f is None:
            raise NotFound("File not found.")
        target = self.require(ws_id, folder_id)
        self.engine.db.execute("UPDATE files SET folder_id = ? WHERE id = ? AND workspace_id = ?", (target, file_id, ws_id))
        self.engine.audit(ws_id, user_id, "file.move", file_id, {"to": target, "from": f["folder_id"]})
        return {"id": file_id, "folder_id": target, "folder_path": self.path(ws_id, target)}
