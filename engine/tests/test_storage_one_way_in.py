"""One way in: every path that keeps a workspace's bytes goes through ``Engine.keep``, and so through its gates.

What this guards. The engine stored customers' files from several places, each writing to file storage itself.
An edition that limits storage per plan had to put its check in each of them, and on 25 Nov 2026 a new place
(documents kept in a folder as they were uploaded) appeared beside the ones it checked: for a few hours a full
allowance did not stop that upload. A check that has to be remembered in every new place is forgotten in one.

So there is one method that writes, it asks the gates, and this file fails if anything else in the engine writes
to file storage. With no gate registered, which is how this repository runs, nothing changes for anybody.

Every file, name and figure below is invented.
"""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from pathlib import Path
from typing import Any

from desk_engine.errors import Invalid
from desk_engine.files.spec import FileSpec
from desk_engine.storage import GENERATED, UPLOAD

from .helpers import Harness
from .test_file_folders import CSV, PDF
from .test_user_files import template_xlsx

ENGINE = Path(__file__).resolve().parents[1] / "desk_engine"
LEDGER = ("Date,Entry,Type,Account code,Account,Account class,Debit,Credit,Description,Party,Cost centre tags\n"
          "2026-10-01,JV-1,Journal,1000,Bank,Bank,100,0,Opening,,\n"
          "2026-10-01,JV-1,Journal,3000,Capital,Equity,0,100,Opening,,\n").encode()


class OnlyOnePlaceWrites(unittest.TestCase):
    def test_nothing_in_the_engine_writes_to_file_storage_except_keep(self) -> None:
        writes = []
        for path in sorted(ENGINE.rglob("*.py")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\bstorage\.put\(", line):
                    writes.append(f"{path.relative_to(ENGINE)}:{n}")
        self.assertEqual(len(writes), 1, "file storage is written from more than one place: " + ", ".join(writes))
        self.assertTrue(writes[0].startswith("app.py:"), writes)
        source = (ENGINE / "app.py").read_text(encoding="utf-8")
        keep = source[source.index("    async def keep("):]
        keep = keep[:keep.index("\n    # -- shared services")]
        self.assertIn("self.admit(workspace_id, len(data), kind)", keep)
        self.assertLess(keep.index("self.admit("), keep.index("self.storage.put("), "the gates are asked before the write")

    def test_the_storage_classes_themselves_are_the_only_other_writers(self) -> None:
        # the backends, the encrypting wrapper around them, and the operator's command that copies one store to another
        # (``python -m desk_engine storage-move``) call ``put`` on a store they hold; none of them takes a customer's file
        other = sorted({str(p.relative_to(ENGINE)) for p in ENGINE.rglob("*.py")
                        if re.search(r"(?<!storage)\.put\((key|item\.key|self\._|path)", p.read_text(encoding="utf-8"))})
        self.assertLessEqual(set(other), {"storage.py", "__main__.py"}, other)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)
        self.asked: list[tuple[str, int, str]] = []

    def record(self) -> None:
        self.h.engine.storage_gates.append(lambda ws, size, kind: self.asked.append((ws, size, kind)))

    def refuse(self, *kinds: str) -> None:
        def gate(ws: str, size: int, kind: str) -> None:
            self.asked.append((ws, size, kind))
            if kind in kinds:
                raise Invalid("This workspace's storage is full.", code="storage_full", details={"adding": size})
        self.h.engine.storage_gates.append(gate)

    def upload(self, path: str, name: str, data: bytes, expect: int = 200) -> Any:
        field = "files" if path == "/documents" else "file"      # the documents page takes several at once
        r = self.h.client.post(f"/api/w/{self.ws}{path}", files={field: (name, data)})
        self.assertEqual(r.status_code, expect, r.text[:300])
        return r.json()

    def stored(self) -> list[str]:
        async def go() -> list[str]:
            out: list[str] = []
            for prefix in ("files", "documents", "uploads"):
                out += [o.key for o in await self.h.engine.storage.list(f"{prefix}/{self.ws}")]
            return out
        return asyncio.run(go())

    def rows(self) -> tuple[int, int, int]:
        db = self.h.engine.db
        return (db.scalar("SELECT COUNT(*) FROM files WHERE workspace_id = ?", (self.ws,)),
                db.scalar("SELECT COUNT(*) FROM file_versions WHERE workspace_id = ?", (self.ws,)),
                db.scalar("SELECT COUNT(*) FROM documents WHERE workspace_id = ?", (self.ws,)))

    def build(self, **kw: Any) -> Any:
        spec = FileSpec.model_validate({"kind": "workbook", "title": "Built here",
                                        "sheets": [{"name": "S", "blocks": [{"type": "note", "text": "A note."}]}]})
        workspace = dict(self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,)))
        user = self.h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (self.ws,))["user_id"]
        return asyncio.run(self.h.engine.files.create(workspace, user, spec, thread_id=None, review=False, **kw))


class EveryPathAsks(Base):
    def test_each_way_a_file_comes_in_asks_the_gate_with_the_workspace_the_size_and_the_kind(self) -> None:
        self.record()
        xlsx = template_xlsx()
        for path, name, data in (("/files/upload", "Pack.xlsx", xlsx), ("/files/upload", "Statement.pdf", PDF),
                                 ("/documents", "Policy.pdf", PDF + b"\n% a second copy with a line more\n"),
                                 ("/imports", "ledger.csv", LEDGER)):
            self.asked.clear()
            self.upload(path, name, data)
            self.assertIn((self.ws, len(data), UPLOAD), self.asked, (path, name, self.asked))
            self.assertTrue(all(ws == self.ws and kind == UPLOAD for ws, _, kind in self.asked), (path, self.asked))
            # asked before anything is read or written, and again as the bytes are kept
            self.assertGreaterEqual(sum(1 for _, size, _ in self.asked if size == len(data)), 2, (path, self.asked))

    def test_what_the_product_builds_is_asked_about_as_generated(self) -> None:
        self.record()
        out = self.build()
        kinds = {kind for _, _, kind in self.asked}
        self.assertEqual(kinds, {GENERATED})
        self.assertEqual(self.asked[0], (self.ws, 0, GENERATED))             # before a version exists
        outputs = json.loads(self.h.engine.db.scalar("SELECT outputs FROM file_versions WHERE file_id = ?", (out["file"]["id"],)))
        self.assertEqual(self.asked[-1], (self.ws, outputs["xlsx"]["size"], GENERATED))   # then with what was rendered

    def test_an_edit_to_an_uploaded_file_is_a_generated_copy(self) -> None:
        fid = self.upload("/files/upload", "Pack.xlsx", template_xlsx())["file"]["id"]
        self.record()
        self.h.api("POST", f"/api/w/{self.ws}/files/{fid}/edit", json={"edits": [
            {"op": "set_cell", "cell": "B4", "value": 5, "source": "typed by the user"}]})
        self.assertEqual({kind for _, _, kind in self.asked}, {GENERATED})
        self.assertEqual(len({size for _, size, _ in self.asked}), 1)        # the same bytes, asked about twice
        self.assertEqual(len(self.asked), 2)

    def test_with_no_gate_nothing_is_asked_and_everything_is_kept(self) -> None:
        self.assertEqual(self.h.engine.storage_gates, [])
        self.upload("/files/upload", "Statement.pdf", PDF)
        self.upload("/documents", "Policy.pdf", PDF)
        self.assertEqual(len(self.stored()), 2)


class ARefusalLeavesNothing(Base):
    def test_every_upload_is_refused_with_the_gates_own_words_and_nothing_is_kept(self) -> None:
        self.refuse(UPLOAD)
        for path, name, data in (("/files/upload", "Pack.xlsx", template_xlsx()), ("/files/upload", "Statement.pdf", PDF),
                                 ("/files/upload", "Bank.csv", CSV), ("/imports", "ledger.csv", LEDGER)):
            e = self.upload(path, name, data, expect=422)
            self.assertEqual((e["error"], e["message"], e["details"]),
                             ("storage_full", "This workspace's storage is full.", {"adding": len(data)}), path)
        # the documents page takes several files at once and says, file by file, which were not added and why
        out = self.upload("/documents", "Policy.pdf", PDF)
        self.assertEqual(out, {"added": [], "failed": [{"name": "Policy.pdf", "error": "This workspace's storage is full."}]})
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.rows(), (0, 0, 0))

    def test_a_gate_that_refuses_uploads_only_lets_the_product_build(self) -> None:
        self.refuse(UPLOAD)
        out = self.build()
        self.assertEqual(out["file"]["kind"], "workbook")
        self.assertEqual(len(self.stored()), 1)

    def test_a_gate_that_takes_no_more_refuses_a_build_before_a_version_exists(self) -> None:
        self.refuse(GENERATED)
        with self.assertRaises(Invalid) as ctx:
            self.build()
        self.assertEqual(ctx.exception.code, "storage_full")
        self.assertEqual((self.stored(), self.rows()), ([], (0, 0, 0)))

    def test_a_refused_edit_leaves_the_file_at_the_version_it_had(self) -> None:
        fid = self.upload("/files/upload", "Pack.xlsx", template_xlsx())["file"]["id"]
        self.refuse(GENERATED)
        e = self.h.api("POST", f"/api/w/{self.ws}/files/{fid}/edit", 422, json={"edits": [
            {"op": "set_cell", "cell": "B4", "value": 5, "source": "typed by the user"}]})
        self.assertEqual(e["error"], "storage_full")
        self.assertEqual(self.h.api("GET", f"/api/w/{self.ws}/files/{fid}")["version"], 1)
        self.assertEqual(len(self.stored()), 1)

    def test_a_gate_is_asked_about_its_own_workspace_only(self) -> None:
        other = self.h.api("POST", "/api/workspaces", json={
            "name": "Other Ltd", "base_currency": "GBP", "country": "GB", "fiscal_year_start_month": 4,
            "accounting_framework": "IFRS", "timezone": "Europe/London"})["id"]

        def full_for_the_first(ws: str, size: int, kind: str) -> None:
            if ws == self.ws:
                raise Invalid("This workspace's storage is full.", code="storage_full")
        self.h.engine.storage_gates.append(full_for_the_first)
        self.upload("/files/upload", "Statement.pdf", PDF, expect=422)
        r = self.h.client.post(f"/api/w/{other}/files/upload", files={"file": ("Statement.pdf", PDF)})
        self.assertEqual(r.status_code, 200, r.text[:200])


class Keep(Base):
    def test_a_key_outside_the_workspaces_own_place_is_a_mistake_in_the_code_and_is_not_written(self) -> None:
        engine = self.h.engine
        for key in ("files/ws_someone_else/f_1/v1.pdf", "files", f"{self.ws}/files/x", f"files/{self.ws}"):
            with self.assertRaises(ValueError, msg=key):
                asyncio.run(engine.keep(self.ws, key, PDF))
        self.assertEqual(self.stored(), [])
        asyncio.run(engine.keep(self.ws, f"files/{self.ws}/f_1/v1.pdf", PDF, "application/pdf"))
        self.assertEqual(self.stored(), [f"files/{self.ws}/f_1/v1.pdf"])

    def test_every_gate_is_asked_and_the_first_refusal_stops_the_write(self) -> None:
        seen: list[str] = []
        self.h.engine.storage_gates.append(lambda ws, size, kind: seen.append("first"))
        self.refuse(UPLOAD)
        self.h.engine.storage_gates.append(lambda ws, size, kind: seen.append("third"))
        with self.assertRaises(Invalid):
            asyncio.run(self.h.engine.keep(self.ws, f"files/{self.ws}/f_1/v1.pdf", PDF))
        self.assertEqual((seen, self.stored()), (["first"], []))


if __name__ == "__main__":
    unittest.main()
