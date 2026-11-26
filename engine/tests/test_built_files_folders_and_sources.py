"""A file the product builds can go into a folder, and can name sources its caller read itself.

What this guards.

- **A built file lands where it was asked to go.** ``Files.create`` and the two tools that build files take a
  folder; the file is listed in it. A folder of another workspace is refused and nothing is built. A new version
  stays where its file is, even when the file was moved after it was built. No folder is the top level, as before.
- **Figures computed outside a conversation still have sources.** A caller that read the figures itself passes
  them as ``Evidence.from_results``; a typed figure that is in those results is traced to them, named the way a
  conversation's tool result is named. The same file with no evidence and no conversation says "without source",
  as it always did.

Every name and every figure below is invented.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from desk_engine.agent.tools import ToolContext, registry
from desk_engine.errors import NotFound
from desk_engine.files.evidence import Evidence
from desk_engine.files.spec import FileSpec

from .helpers import Harness

READ_AT = 1793000000.0      # when the invented results were read

SPEC = {"kind": "workbook", "title": "October against September", "sheets": [{"name": "Movements", "blocks": [
    {"type": "table", "id": "mv", "title": "Movements",
     "rows": [["Revenue", 412750.25, 388120.5], ["Rent", 21000, 21000]],
     "columns": [{"label": "Line"}, {"label": "October", "format": "money"}, {"label": "September", "format": "money"},
                 {"label": "Movement", "formula": "october - september", "format": "money"}]}]}]}

RESULTS = [
    {"name": "financial_statements", "arguments": {"start_date": "2026-10-01", "end_date": "2026-10-31"},
     "result": {"lines": [{"line": "Revenue", "amount": 412750.25}, {"line": "Rent", "amount": 21000}]},
     "at": READ_AT},
    {"name": "financial_statements", "arguments": {"start_date": "2026-09-01", "end_date": "2026-09-30"},
     "result": "Revenue 388,120.50\nRent 21,000.00", "at": READ_AT},
]


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)
        self.workspace = dict(self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,)))
        self.user = self.h.engine.db.one("SELECT user_id FROM members WHERE workspace_id = ?", (self.ws,))["user_id"]

    def a(self, method: str, path: str, expect: int | None = 200, **kw: Any) -> Any:
        return self.h.api(method, f"/api/w/{self.ws}{path}", expect, **kw)

    def build(self, spec: dict[str, Any] | None = None, **kw: Any) -> Any:
        s = FileSpec.model_validate(spec or SPEC)
        return asyncio.run(self.h.engine.files.create(self.workspace, self.user, s, thread_id=None, review=False, **kw))

    def folder(self, name: str, parent: str | None = None) -> str:
        return self.a("POST", "/files/folders", json={"name": name, "parent_id": parent})["id"]

    def listed(self, folder: str) -> list[str]:
        return [f["id"] for f in self.a("GET", "/files", params={"folder": folder})]

    def cells(self, out: Any) -> dict[str, dict[str, Any]]:
        table = next(b for b in out["model"]["sheets"][0]["blocks"] if b["type"] == "table")
        return {f"{row[0]['v']}:{i}": c for row in table["rows"] for i, c in enumerate(row) if i in (1, 2)}


class IntoAFolder(Base):
    def test_a_file_built_into_a_folder_is_listed_in_that_folder_and_not_at_the_top(self) -> None:
        packs = self.folder("Board packs")
        october = self.folder("October", packs)
        fid = self.build(folder_id=october)["file"]["id"]
        self.assertEqual(self.listed(october), [fid])
        self.assertNotIn(fid, self.listed("root"))
        got = self.a("GET", f"/files/{fid}")["file"]
        self.assertEqual((got["folder_id"], got["folder_path"]), (october, "Board packs / October"))

    def test_no_folder_is_the_top_level_as_before(self) -> None:
        fid = self.build()["file"]["id"]
        self.assertEqual(self.listed("root"), [fid])
        self.assertEqual(self.build(folder_id="root")["file"]["folder_id"], None)

    def test_a_folder_of_another_workspace_is_refused_and_nothing_is_built(self) -> None:
        mine = self.folder("Mine")
        other = self.h.setup_workspace(with_model=False)
        theirs = self.h.api("POST", f"/api/w/{other}/files/folders", json={"name": "Theirs"})["id"]
        with self.assertRaises(NotFound):
            self.build(folder_id=theirs)
        with self.assertRaises(NotFound):
            self.build(folder_id="fd_nothing_like_this")
        self.assertEqual(self.h.engine.db.scalar("SELECT COUNT(*) FROM files WHERE workspace_id = ?", (self.ws,)), 0)
        self.assertEqual(self.listed(mine), [])

    def test_a_new_version_stays_where_its_file_is_even_after_it_was_moved(self) -> None:
        first, second = self.folder("First"), self.folder("Second")
        fid = self.build(folder_id=first)["file"]["id"]
        self.a("POST", f"/files/{fid}/move", json={"folder_id": second})
        out = self.build(file_id=fid, folder_id=first, note="Revised")
        self.assertEqual((out["version"], out["file"]["folder_id"]), (2, second))
        self.assertEqual(self.listed(second), [fid])
        self.assertEqual(self.listed(first), [])

    def test_the_audit_of_a_new_file_names_its_folder(self) -> None:
        october = self.folder("October")
        fid = self.build(folder_id=october)["file"]["id"]
        self.build(file_id=fid)
        rows = {r["action"]: json.loads(r["detail"]) for r in self.h.engine.db.all(
            "SELECT action, detail FROM audit_log WHERE target = ?", (fid,))}
        self.assertEqual(rows["file.create"]["folder"], october)
        self.assertNotIn("folder", rows["file.revise"])

    def test_the_two_tools_that_build_files_take_a_folder_and_leave_it_out_for_the_top(self) -> None:
        october = self.folder("October")
        ctx = ToolContext(self.h.engine, self.workspace, self.user)

        async def go() -> list[Any]:
            tools = await registry.for_context(ctx)
            for name in ("create_workbook", "create_report"):
                self.assertIn("folder_id", tools[name].parameters["properties"])
            sheets = SPEC["sheets"]
            section = [{"heading": "Results", "blocks": [{"type": "paragraph", "text": "Two lines."}]}]
            return [await tools["create_workbook"].handler(ctx, {"title": "W", "sheets": sheets, "folder_id": october}),
                    await tools["create_report"].handler(ctx, {"title": "R", "sheets": sheets, "sections": section,
                                                               "folder_id": october}),
                    await tools["create_workbook"].handler(ctx, {"title": "Top", "sheets": sheets})]
        made = [o.data["file_id"] for o in asyncio.run(go())]
        self.assertEqual(sorted(self.listed(october)), sorted(made[:2]))
        self.assertEqual(self.listed("root"), [made[2]])


class SourcesFromTheCaller(Base):
    def test_figures_in_the_supplied_results_are_traced_to_them(self) -> None:
        out = self.build(evidence=Evidence.from_results(RESULTS))
        cells = self.cells(out)
        for key in ("Revenue:1", "Revenue:2", "Rent:1", "Rent:2"):
            self.assertEqual(cells[key]["st"], "sourced", key)
        s = out["model"]["summary"]
        self.assertEqual((s["cells_sourced"], s["cells_unverified"]), (4, 0))
        titles = {x["title"] for x in out["model"]["sources"]}
        self.assertIn("Financial statements (2026-10-01 · 2026-10-31)", titles)
        self.assertIn("Financial statements (2026-09-01 · 2026-09-30)", titles)
        self.assertTrue(all(x["at"] == READ_AT for x in out["model"]["sources"]))

    def test_a_figure_that_is_in_none_of_them_is_still_without_source(self) -> None:
        spec = json.loads(json.dumps(SPEC))
        spec["sheets"][0]["blocks"][0]["rows"][1][1] = 21500       # the October rent read was 21,000
        cells = self.cells(self.build(spec, evidence=Evidence.from_results(RESULTS)))
        self.assertEqual(cells["Rent:1"]["st"], "unverified")
        self.assertEqual(cells["Revenue:1"]["st"], "sourced")

    def test_with_no_evidence_and_no_conversation_every_figure_is_without_source_as_before(self) -> None:
        out = self.build()
        self.assertTrue(all(c["st"] == "unverified" for c in self.cells(out).values()))
        self.assertEqual(out["model"]["summary"]["cells_sourced"], 0)

    def test_a_failed_result_is_not_a_source_and_a_result_without_its_time_is_a_mistake(self) -> None:
        failed = [dict(RESULTS[0], status="error")]
        self.assertEqual(Evidence.from_results(failed).items, [])
        with self.assertRaises(ValueError):
            Evidence.from_results([{"name": "financial_statements", "result": "Revenue 1"}])

    def test_a_refresh_can_be_given_fresh_results(self) -> None:
        fid = self.build(evidence=Evidence.from_results(RESULTS))["file"]["id"]
        fresh = asyncio.run(self.h.engine.files.refresh(self.workspace, self.user, fid,
                                                        evidence=Evidence.from_results(RESULTS)))
        self.assertEqual(fresh["version"], 2)
        self.assertEqual(fresh["model"]["summary"]["cells_sourced"], 4)
        plain = asyncio.run(self.h.engine.files.refresh(self.workspace, self.user, fid))
        self.assertEqual(plain["model"]["summary"]["cells_sourced"], 0)      # the documented limit, kept honest


if __name__ == "__main__":
    unittest.main()
