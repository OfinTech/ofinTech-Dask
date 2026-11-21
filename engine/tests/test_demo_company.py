"""The demo company, loaded into a workspace that has nothing (desk_engine/demo.py).

The point of this file is one assertion: the invented ledger that ships with the product is accepted as a general
ledger by the same predicate a customer's own import has to satisfy. Everything the overview, the analysis, the
close and the cost centres show hangs off `costcentres.is_ledger`, which compares the dataset's first eleven
column names to `connectors.accounting.GL_COLUMNS` exactly. A heading renamed in the demo file, or a change to
that column list, breaks the one click this feature is -- silently, in a way no other test would notice, because
the file is data and not code.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desk_engine import demo
from desk_engine.costcentres.service import is_ledger
from desk_engine.data import store

from .helpers import Harness


class DemoCompanyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace(with_model=False)

    def tearDown(self) -> None:
        self.h.close()

    def test_the_files_are_found_in_a_checkout(self) -> None:
        self.assertTrue(demo.available(), "the demo files should be found from the engine package")
        folder = demo.directory()
        assert folder is not None
        for filename, _ in demo.FILES:
            self.assertTrue((folder / filename).is_file(), f"{filename} is missing")

    def test_an_empty_workspace_is_offered_the_demo(self) -> None:
        state = self.h.api("GET", f"/api/w/{self.ws}/demo")
        self.assertTrue(state["available"])
        self.assertEqual(state["loaded"], [])

    def test_one_click_gives_the_workspace_a_general_ledger(self) -> None:
        # Before: the overview has nothing to be built from.
        before = self.h.client.get(f"/api/w/{self.ws}/overview")
        self.assertNotEqual(before.status_code, 200, "a workspace with no ledger should not have an overview")

        loaded = self.h.api("POST", f"/api/w/{self.ws}/demo")["loaded"]
        self.assertEqual(len(loaded), len(demo.FILES))

        datasets = store.list_datasets(self.h.engine, self.ws)
        ledgers = [d for d in datasets if is_ledger(d)]
        self.assertEqual(len(ledgers), 1, f"exactly one dataset should be a general ledger, got {len(ledgers)}")
        self.assertGreater(ledgers[0]["row_count"], 1000, "the demo ledger should carry its 14 months of entries")

        after = self.h.client.get(f"/api/w/{self.ws}/overview")
        self.assertEqual(after.status_code, 200, f"the overview should work now: {after.text[:300]}")

    def test_every_demo_dataset_is_marked_and_named_as_demo(self) -> None:
        self.h.api("POST", f"/api/w/{self.ws}/demo")
        for ds in store.list_datasets(self.h.engine, self.ws):
            self.assertTrue(ds["source_ref"].get("demo"), f"{ds['name']} is not marked as demo data")
            self.assertIn(demo.COMPANY, ds["name"], "the name should say it is a demo on the Data screen")

    def test_loading_twice_does_not_load_twice(self) -> None:
        first = self.h.api("POST", f"/api/w/{self.ws}/demo")["loaded"]
        second = self.h.api("POST", f"/api/w/{self.ws}/demo")["loaded"]
        self.assertEqual([d["id"] for d in first], [d["id"] for d in second])
        self.assertEqual(len(store.list_datasets(self.h.engine, self.ws)), len(demo.FILES))

    def test_removing_it_leaves_the_workspace_as_it_was(self) -> None:
        own = store.create_dataset(self.h.engine, self.ws, name="The company's own figures", source="csv",
                                   columns=[{"key": "c0", "name": "Amount", "type": "number"}], rows=[[1]],
                                   source_ref={"filename": "own.csv"}, created_by="u")
        self.h.api("POST", f"/api/w/{self.ws}/demo")
        self.assertEqual(self.h.api("DELETE", f"/api/w/{self.ws}/demo")["removed"], len(demo.FILES))

        left = store.list_datasets(self.h.engine, self.ws)
        self.assertEqual([d["id"] for d in left], [own["id"]], "removal should take the demo and nothing else")
        self.assertEqual(self.h.api("GET", f"/api/w/{self.ws}/demo")["loaded"], [])

    def test_a_build_without_the_files_offers_nothing_and_refuses_a_load(self) -> None:
        # An engine installed without the repository beside it (a wheel, an image built without the COPY line).
        with mock.patch.object(demo, "directory", return_value=None):
            state = self.h.api("GET", f"/api/w/{self.ws}/demo")
            self.assertFalse(state["available"], "the page offers the button only when this is true")
            refused = self.h.api("POST", f"/api/w/{self.ws}/demo", expect=422)
        self.assertEqual(refused["error"], "no_demo")
        self.assertEqual(store.list_datasets(self.h.engine, self.ws), [])

    def test_the_operator_can_keep_the_files_somewhere_else(self) -> None:
        here = demo.directory()
        assert here is not None
        with tempfile.TemporaryDirectory() as tmp:
            empty, full = Path(tmp) / "empty", Path(tmp) / "full"
            empty.mkdir()
            shutil.copytree(here, full)
            with mock.patch.dict(os.environ, {"DESK_DEMO_DIR": str(full)}):
                self.assertEqual(demo.directory(), full)
            with mock.patch.dict(os.environ, {"DESK_DEMO_DIR": str(empty)}):
                self.assertEqual(demo.directory(), here, "a folder without the ledger is passed over, not trusted")

    def test_a_file_that_cannot_be_read_leaves_nothing_behind(self) -> None:
        # Two of three files loaded is worse than none: the load is all or nothing.
        here = demo.directory()
        assert here is not None
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "demo"
            shutil.copytree(here, broken)
            (broken / demo.FILES[-1][0]).write_bytes(b"")          # the last file, so two are already in
            with mock.patch.dict(os.environ, {"DESK_DEMO_DIR": str(broken)}):
                self.h.api("POST", f"/api/w/{self.ws}/demo", expect=422)
        self.assertEqual(store.list_datasets(self.h.engine, self.ws), [])
        self.assertEqual(self.h.api("GET", f"/api/w/{self.ws}/demo")["loaded"], [])


if __name__ == "__main__":
    unittest.main()
