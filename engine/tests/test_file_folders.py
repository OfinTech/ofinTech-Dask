"""Folders for Files, and documents kept as they are.

What this guards. Files was one flat list of what the assistant built and the Excel and Word files it fills in.
It now has folders, and a folder takes any document. Three things must hold, and each is the kind of promise a
person relies on without checking:

- **Tidying never destroys.** A folder that is not empty is not deleted until that is confirmed, and then what
  was in it is in the folder above, byte for byte.
- **A stored document comes back exactly as it went in**, as a download and never as a page, and only documents
  are taken: a program is refused by its name, and by its first bytes when the name lies.
- **A folder belongs to one workspace.** Another workspace cannot see it, rename it, or put a file in it.

Every name and every byte below is invented.
"""

from __future__ import annotations

import asyncio
import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

from desk_engine.db import MIGRATIONS
from desk_engine.errors import Invalid
from desk_engine.files.folders import MAX_DEPTH, MAX_STORED_BYTES, STORED, stored_format
from desk_engine.files.spec import FileSpec

from .helpers import Harness
from .test_user_files import template_docx, template_xlsx

PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 24
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
CSV = "Date,Description,Amount\n2026-10-01,Opening balance,1000.00\n".encode()


def zipped(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


PPTX = zipped({"[Content_Types].xml": b"<Types/>", "ppt/presentation.xml": b"<p/>"})


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)

    def a(self, method: str, path: str, expect: int | None = 200, **kw: Any) -> Any:
        return self.h.api(method, f"/api/w/{self.ws}{path}", expect, **kw)

    def folder(self, name: str, parent: str | None = None, expect: int | None = 200) -> Any:
        return self.a("POST", "/files/folders", expect, json={"name": name, "parent_id": parent})

    def add(self, name: str, data: bytes, folder: str | None = None, expect: int = 200) -> Any:
        r = self.h.client.post(f"/api/w/{self.ws}/files/upload", files={"file": (name, data)},
                               data={"folder": folder} if folder else None)
        self.assertEqual(r.status_code, expect, r.text[:300])
        return r.json()

    def paths(self) -> dict[str, dict[str, Any]]:
        return {f["path"]: f for f in self.a("GET", "/files/folders")}


class Tree(Base):
    def test_a_folder_inside_a_folder_has_a_path_and_counts_what_it_holds(self) -> None:
        self.assertEqual(self.a("GET", "/files/folders"), [])
        year = self.folder("2026 close")
        bank = self.folder("Bank", year["id"])
        self.assertEqual((bank["path"], bank["depth"], bank["parent_id"]), ("2026 close / Bank", 2, year["id"]))
        self.add("October.pdf", PDF, bank["id"])
        got = self.paths()
        self.assertEqual(list(got), ["2026 close", "2026 close / Bank"])
        self.assertEqual((got["2026 close"]["files"], got["2026 close"]["folders"]), (0, 1))
        self.assertEqual((got["2026 close / Bank"]["files"], got["2026 close / Bank"]["folders"]), (1, 0))

    def test_two_folders_of_one_name_cannot_share_a_parent_and_can_have_different_ones(self) -> None:
        a = self.folder("Contracts")
        self.assertEqual(self.folder("contracts", expect=422)["error"], "folder_exists")
        self.assertEqual(self.folder("  Contracts ", expect=422)["error"], "folder_exists")
        inside = self.folder("Contracts", a["id"])
        self.assertEqual(inside["path"], "Contracts / Contracts")

    def test_names_that_are_not_names_are_refused(self) -> None:
        for bad in ("   ", "a/b", "a\\b", "..", "x" * 121):
            self.assertEqual(self.folder(bad, expect=422)["error"], "folder_name", bad)
        self.assertEqual(self.folder("  Two   words  ")["name"], "Two words")
        self.assertEqual(len(self.a("GET", "/files/folders")), 1)

    def test_folders_stop_at_eight_deep(self) -> None:
        parent = None
        for n in range(MAX_DEPTH):
            parent = self.folder(f"Level {n + 1}", parent)["id"]
        self.assertEqual(self.folder("One too many", parent, expect=422)["error"], "folder_depth")
        # and a tree cannot be moved to where it would not fit
        tall = self.folder("Tall")
        self.folder("Under tall", tall["id"])
        e = self.a("PATCH", f"/files/folders/{tall['id']}", 422, json={"parent_id": parent, "move": True})
        self.assertEqual(e["error"], "folder_depth")

    def test_a_parent_that_does_not_exist_is_not_found_and_nothing_is_made(self) -> None:
        self.folder("Orphan", "fo_nothing", expect=404)
        self.assertEqual(self.a("GET", "/files/folders"), [])


class RenameAndMove(Base):
    def test_rename_keeps_the_place_and_move_keeps_the_name(self) -> None:
        a, b = self.folder("Audit"), self.folder("Tax")
        c = self.folder("Working papers", a["id"])
        self.assertEqual(self.a("PATCH", f"/files/folders/{c['id']}", json={"name": "Papers"})["path"], "Audit / Papers")
        self.assertEqual(self.a("PATCH", f"/files/folders/{c['id']}", json={"parent_id": b["id"], "move": True})["path"],
                         "Tax / Papers")
        # the top level is null, which is why a move has to say that it is one
        self.assertEqual(self.a("PATCH", f"/files/folders/{c['id']}", json={"parent_id": None})["path"], "Tax / Papers")
        self.assertEqual(self.a("PATCH", f"/files/folders/{c['id']}", json={"parent_id": None, "move": True})["path"], "Papers")

    def test_a_folder_cannot_go_into_itself_or_into_one_inside_it(self) -> None:
        a = self.folder("A")
        b = self.folder("B", a["id"])
        c = self.folder("C", b["id"])
        for target in (a["id"], b["id"], c["id"]):
            e = self.a("PATCH", f"/files/folders/{a['id']}", 422, json={"parent_id": target, "move": True})
            self.assertEqual(e["error"], "folder_loop")
        self.assertEqual(list(self.paths()), ["A", "A / B", "A / B / C"])

    def test_a_move_or_a_rename_onto_a_name_already_there_is_refused(self) -> None:
        a, b = self.folder("A"), self.folder("B")
        self.folder("Notes", a["id"])
        n = self.folder("Notes", b["id"])
        e = self.a("PATCH", f"/files/folders/{n['id']}", 422, json={"parent_id": a["id"], "move": True})
        self.assertEqual(e["error"], "folder_exists")
        self.assertEqual(self.a("PATCH", f"/files/folders/{b['id']}", 422, json={"name": "a"})["error"], "folder_exists")
        self.assertEqual(self.a("PATCH", f"/files/folders/{b['id']}", json={"name": "b"})["name"], "b")   # its own name, recased


class DeleteMovesUp(Base):
    def test_an_empty_folder_is_simply_removed(self) -> None:
        f = self.folder("Empty")
        self.assertEqual(self.a("DELETE", f"/files/folders/{f['id']}")["moved_up"], {"files": 0, "folders": 0})
        self.assertEqual(self.a("GET", "/files/folders"), [])

    def test_a_folder_that_holds_something_waits_for_a_yes_and_then_nothing_in_it_is_lost(self) -> None:
        year = self.folder("2026")
        bank = self.folder("Bank", year["id"])
        deep = self.folder("Statements", bank["id"])
        pdf = self.add("October.pdf", PDF, bank["id"])["file"]["id"]
        pack = self.add("Pack.xlsx", template_xlsx(), bank["id"])["file"]["id"]
        low = self.add("Scan.png", PNG, deep["id"])["file"]["id"]

        e = self.a("DELETE", f"/files/folders/{bank['id']}", 422)
        self.assertEqual((e["error"], e["details"]), ("folder_not_empty", {"files": 2, "folders": 1}))
        self.assertIn("nothing is deleted", e["message"])
        self.assertEqual(list(self.paths()), ["2026", "2026 / Bank", "2026 / Bank / Statements"])   # untouched

        out = self.a("DELETE", f"/files/folders/{bank['id']}?confirm=1")
        self.assertEqual((out["moved_up"], out["to"]), ({"files": 2, "folders": 1}, year["id"]))
        self.assertEqual(list(self.paths()), ["2026", "2026 / Statements"])
        where = {f["id"]: f["folder_path"] for f in self.a("GET", "/files")}
        self.assertEqual(where, {pdf: "2026", pack: "2026", low: "2026 / Statements"})
        # the documents themselves: the same bytes, and the workbook is still one the assistant can read
        self.assertEqual(self.h.client.get(f"/api/w/{self.ws}/files/{pdf}/download?format=pdf").content, PDF)
        self.assertEqual(self.h.client.get(f"/api/w/{self.ws}/files/{low}/download?format=png").content, PNG)
        self.assertIn("Monthly pack", self.a("GET", f"/files/{pack}/inspect")["text"])

    def test_from_the_top_level_the_contents_land_at_the_top_level(self) -> None:
        top = self.folder("Inbox")
        fid = self.add("note.txt", b"Call the bank on Monday.\n", top["id"])["file"]["id"]
        self.a("DELETE", f"/files/folders/{top['id']}?confirm=1")
        f = self.a("GET", f"/files/{fid}")["file"]
        self.assertEqual((f["folder_id"], f["folder_path"]), (None, ""))
        self.assertEqual(len(self.a("GET", "/files?folder=root")), 1)

    def test_a_folder_coming_up_beside_one_of_its_name_says_where_it_came_from(self) -> None:
        self.folder("Notes")
        old = self.folder("Old")
        self.folder("Notes", old["id"])
        self.a("DELETE", f"/files/folders/{old['id']}?confirm=1")
        self.assertEqual(list(self.paths()), ["Notes", "Notes (from Old)"])


class FilesInFolders(Base):
    def test_what_existed_before_is_at_the_top_level_and_listed_as_before(self) -> None:
        old = self.add("Pack.xlsx", template_xlsx())["file"]
        self.assertEqual((old["kind"], old["folder_id"], old["folder_path"]), ("user_xlsx", None, ""))
        listed = self.a("GET", "/files")
        self.assertEqual([(f["id"], f["folder_id"]) for f in listed], [(old["id"], None)])
        self.assertEqual(len(self.a("GET", "/files?folder=root")), 1)

    def test_an_excel_or_word_file_added_to_a_folder_is_still_one_the_assistant_edits(self) -> None:
        f = self.folder("Templates")
        x = self.add("Pack.xlsx", template_xlsx(), f["id"])
        d = self.add("Letter.docx", template_docx(), f["id"])
        self.assertEqual((x["file"]["kind"], x["file"]["folder_path"], x["model"]["kind"]), ("user_xlsx", "Templates", "upload"))
        self.assertEqual((d["file"]["kind"], d["model"]["outline"]["placeholders"]), ("user_docx", ["{{client_name}}", "{{fee}}"]))
        out = self.a("POST", f"/files/{x['file']['id']}/edit", json={"edits": [{"op": "set_cell", "cell": "B4", "value": 5,
                                                                                 "source": "typed by the user"}]})
        self.assertEqual((out["version"], out["file"]["folder_path"]), (2, "Templates"))     # a new version stays put

    def test_one_folder_all_folders_and_the_top_level(self) -> None:
        a, b = self.folder("A"), self.folder("B")
        self.add("a.pdf", PDF, a["id"])
        self.add("b.pdf", PDF, b["id"])
        self.add("top.pdf", PDF)
        names = lambda q: sorted(f["name"] for f in self.a("GET", f"/files{q}"))     # noqa: E731
        self.assertEqual(names(""), ["a", "b", "top"])
        self.assertEqual((names(f"?folder={a['id']}"), names(f"?folder={b['id']}"), names("?folder=root")),
                         (["a"], ["b"], ["top"]))
        self.a("GET", "/files?folder=fo_nothing", 404)

    def test_a_file_moves_between_folders_and_back_to_the_top(self) -> None:
        a, b = self.folder("A"), self.folder("B")
        fid = self.add("a.pdf", PDF, a["id"])["file"]["id"]
        self.assertEqual(self.a("POST", f"/files/{fid}/move", json={"folder_id": b["id"]})["folder_path"], "B")
        self.assertEqual(self.a("POST", f"/files/{fid}/move", json={"folder_id": None})["folder_id"], None)
        self.a("POST", f"/files/{fid}/move", 404, json={"folder_id": "fo_nothing"})
        self.a("POST", "/files/f_nothing/move", 404, json={"folder_id": a["id"]})
        self.assertEqual(self.a("GET", f"/files/{fid}")["file"]["folder_path"], "")

    def test_adding_to_a_folder_that_does_not_exist_keeps_nothing(self) -> None:
        self.add("lost.pdf", PDF, "fo_nothing", expect=404)
        self.assertEqual(self.a("GET", "/files"), [])
        self.assertEqual(self.h.engine.db.scalar("SELECT COUNT(*) FROM file_versions"), 0)

    def test_every_change_is_on_the_audit_log(self) -> None:
        a = self.folder("A")
        fid = self.add("a.pdf", PDF)["file"]["id"]
        self.a("PATCH", f"/files/folders/{a['id']}", json={"name": "B"})
        self.a("POST", f"/files/{fid}/move", json={"folder_id": a["id"]})
        self.a("DELETE", f"/files/folders/{a['id']}?confirm=1")
        actions = [r["action"] for r in self.h.engine.db.all(
            "SELECT action FROM audit_log WHERE workspace_id = ? AND (action LIKE 'folder.%' OR action LIKE 'file.%') "
            "ORDER BY rowid", (self.ws,))]
        self.assertEqual(actions, ["folder.create", "file.upload", "folder.update", "file.move", "folder.delete"])


class StoredDocuments(Base):
    def test_each_kind_of_document_comes_back_byte_for_byte_as_a_download(self) -> None:
        for name, data in (("Statement.pdf", PDF), ("Bank export.csv", CSV), ("Note.txt", "ملاحظة\n".encode()),
                           ("Receipt.png", PNG), ("Receipt.jpg", JPG), ("Board deck.pptx", PPTX),
                           ("Old ledger.xls", OLE), ("Letter.doc", OLE), ("Invoice.xml", b"<Invoice/>")):
            out = self.add(name, data)
            ext = name.rsplit(".", 1)[1]
            self.assertEqual((out["file"]["kind"], out["model"]["kind"], out["model"]["original_name"], out["model"]["size"],
                              out["formats"]), ("stored", "stored", name, len(data), [ext]), name)
            r = self.h.client.get(f"/api/w/{self.ws}/files/{out['file']['id']}/download?format={ext}")
            self.assertEqual((r.status_code, r.content), (200, data), name)
            self.assertEqual(r.headers["content-type"].split(";")[0], STORED[ext], name)
            self.assertEqual(r.headers["x-content-type-options"], "nosniff")
            self.assertTrue(r.headers["content-disposition"].startswith("attachment;"), name)
            self.assertIn(f".{ext}", r.headers["content-disposition"])
        listed = {f["name"]: (f["format"], f["size"]) for f in self.a("GET", "/files")}
        self.assertEqual(listed["Statement"], ("pdf", len(PDF)))

    def test_only_documents_are_taken(self) -> None:
        for name in ("setup.exe", "run.sh", "page.html", "drawing.svg", "code.js", "archive.zip", "noextension", "macro.xlsm",
                     "macro.docm", "tool.py", "installer.msi"):
            e = self.add(name, b"anything at all", expect=422)
            self.assertEqual(e["error"], "file_format", name)
        self.assertEqual(self.a("GET", "/files"), [])

    def test_a_name_that_lies_about_what_the_file_is_does_not_get_it_in(self) -> None:
        cases = (("invoice.pdf", b"MZ\x90\x00\x03\x00"), ("invoice.pdf", b"<html><script>alert(1)</script></html>"),
                 ("export.csv", b"#!/bin/sh\nrm -rf /\n"), ("export.csv", b"\x7fELF\x02\x01\x01"),
                 ("scan.png", JPG), ("scan.jpg", PDF), ("deck.pptx", PDF), ("old.xls", PPTX),
                 ("note.txt", b"abc\x00\x00\x00def"), ("deck.pptx", zipped({"ppt/vbaProject.bin": b"x", "ppt/presentation.xml": b"<p/>"})))
        for name, data in cases:
            self.assertEqual(self.add(name, data, expect=422)["error"], "file_format", (name, data[:12]))
        self.assertEqual(self.add("empty.pdf", b"", expect=422)["error"], "file_format")
        self.assertEqual(self.a("GET", "/files"), [])

    def test_the_size_limit(self) -> None:
        with self.assertRaises(Invalid) as ctx:
            stored_format("big.pdf", PDF + b"0" * MAX_STORED_BYTES)
        self.assertEqual(ctx.exception.code, "file_too_large")
        self.assertEqual(stored_format("fits.pdf", PDF + b"0" * (MAX_STORED_BYTES - len(PDF))), "pdf")

    def test_a_stored_document_is_not_read_edited_refreshed_reviewed_or_saved_over(self) -> None:
        fid = self.add("Statement.pdf", PDF)["file"]["id"]
        for method, path, body in (("POST", f"/files/{fid}/edit", {"edits": [{"op": "set_cell", "cell": "A1", "value": 1}]}),
                                   ("GET", f"/files/{fid}/inspect", None), ("POST", f"/files/{fid}/refresh", None),
                                   ("POST", f"/files/{fid}/review", None)):
            r = self.h.client.request(method, f"/api/w/{self.ws}{path}", json=body)
            self.assertEqual(r.status_code, 422, (path, r.text[:200]))
        # the assistant's "save as a new version of this file" is refused too: the service, as its tools call it
        spec = FileSpec.model_validate({"kind": "workbook", "title": "Over it",
                                        "sheets": [{"name": "S", "blocks": [{"type": "note", "text": "x"}]}]})
        workspace = dict(self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,)))
        user = self.h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (self.ws,))["user_id"]
        with self.assertRaises(Invalid) as ctx:
            asyncio.run(self.h.engine.files.create(workspace, user, spec, thread_id=None, file_id=fid))
        self.assertEqual(ctx.exception.code, "not_editable")
        self.assertEqual(self.h.client.get(f"/api/w/{self.ws}/files/{fid}/download?format=pdf").content, PDF)
        self.assertEqual(self.a("GET", f"/files/{fid}")["version"], 1)

    def test_deleting_one_removes_it_from_storage(self) -> None:
        fid = self.add("Statement.pdf", PDF)["file"]["id"]
        self.h.api("DELETE", f"/api/w/{self.ws}/files/{fid}", expect=None)
        self.assertEqual(self.h.client.get(f"/api/w/{self.ws}/files/{fid}/download?format=pdf").status_code, 404)
        self.assertEqual(self.a("GET", "/files"), [])

    def test_a_format_nothing_is_kept_in_is_refused_before_anything_is_read(self) -> None:
        fid = self.add("Statement.pdf", PDF)["file"]["id"]
        self.assertEqual(self.h.client.get(f"/api/w/{self.ws}/files/{fid}/download?format=exe").status_code, 422)
        self.assertEqual(self.h.client.get(f"/api/w/{self.ws}/files/{fid}/download?format=xlsx").status_code, 404)


class AnotherWorkspace(Base):
    def test_it_cannot_see_rename_delete_or_fill_a_folder_that_is_not_its_own(self) -> None:
        mine = self.folder("Board papers")
        kept = self.add("Minutes.pdf", PDF, mine["id"])["file"]["id"]
        other = self.h.api("POST", "/api/workspaces", json={
            "name": "Other Ltd", "base_currency": "GBP", "country": "GB", "fiscal_year_start_month": 4,
            "accounting_framework": "IFRS", "timezone": "Europe/London"})["id"]
        o = lambda m, p, e=200, **k: self.h.api(m, f"/api/w/{other}{p}", e, **k)     # noqa: E731
        self.assertEqual(o("GET", "/files/folders"), [])
        self.assertEqual(o("GET", "/files"), [])
        o("PATCH", f"/files/folders/{mine['id']}", 404, json={"name": "Taken"})
        o("DELETE", f"/files/folders/{mine['id']}?confirm=1", 404)
        o("POST", "/files/folders", 404, json={"name": "Inside", "parent_id": mine["id"]})
        o("GET", f"/files?folder={mine['id']}", 404)
        r = self.h.client.post(f"/api/w/{other}/files/upload", files={"file": ("x.pdf", PDF)}, data={"folder": mine["id"]})
        self.assertEqual(r.status_code, 404)
        theirs = self.h.client.post(f"/api/w/{other}/files/upload", files={"file": ("x.pdf", PDF)}).json()["file"]["id"]
        o("POST", f"/files/{theirs}/move", 404, json={"folder_id": mine["id"]})
        o("POST", f"/files/{kept}/move", 404, json={"folder_id": None})
        # and nothing of the first workspace moved
        self.assertEqual(list(self.paths()), ["Board papers"])
        self.assertEqual(self.a("GET", f"/files/{kept}")["file"]["folder_path"], "Board papers")


class Migration(unittest.TestCase):
    def test_a_database_from_before_folders_gains_them_and_its_files_are_at_the_top_level(self) -> None:
        # The folders migration is found by what it does, not by its number: an edition that adds migrations of its
        # own places this one after them (migrations are applied by position, and a position never changes once a
        # database has run it), so a number written here would be true in one edition only.
        at = [i for i, script in enumerate(MIGRATIONS) if "CREATE TABLE file_folders" in script]
        self.assertEqual(len(at), 1, "exactly one migration creates the folders table")
        with tempfile.TemporaryDirectory() as tmp:
            db = sqlite3.connect(Path(tmp) / "desk.db")
            for script in MIGRATIONS[:at[0]]:
                db.executescript(script)
            self.assertNotIn("folder_id", [c[1] for c in db.execute("PRAGMA table_info(files)")])
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("INSERT INTO files (id, workspace_id, name, kind, created_at, updated_at) VALUES "
                       "('f_old', 'ws_old', 'Before folders', 'workbook', 1, 1)")
            db.commit()
            db.executescript(MIGRATIONS[at[0]])
            self.assertEqual(db.execute("SELECT name, folder_id FROM files").fetchall(), [("Before folders", None)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM file_folders").fetchone()[0], 0)
            db.close()
        # nothing after it depends on the place it has here: it only adds a table, a column and two indexes
        self.assertNotIn("DROP", MIGRATIONS[at[0]].upper())

if __name__ == "__main__":
    unittest.main()
