"""A UI fix has to be able to reach a browser that has already loaded the app.

`cache-control: public, max-age=31536000, immutable` tells a browser it need not revalidate at all, so a response
served under it is effectively frozen at that URL. That is correct for a file whose name carries its content hash,
and wrong for anything else: sending it for a fixed name such as assets/app.css pins the stylesheet in every
returning browser for a year, where no redeploy, image rebuild or restart can reach it. These tests hold both
halves of that rule, because only the pair is safe.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from desk_engine.app import _is_hashed_asset

from .helpers import Harness

IMMUTABLE = "public, max-age=31536000, immutable"


class HashedNames(unittest.TestCase):
    def test_a_content_hashed_asset_may_be_pinned(self) -> None:
        for rel in ("assets/app-2O3PPUM7.js", "assets/app-XLWWT4PU.css",
                    "assets/IBMPlexSansArabic-Regular-UP6WURK5.woff"):
            with self.subTest(rel=rel):
                self.assertTrue(_is_hashed_asset(rel))

    def test_a_fixed_name_never_may_be(self) -> None:
        """These are exactly the names the build used to emit, and the reason a CSS fix did not reach anyone."""
        for rel in ("assets/app.css", "assets/app.js", "assets/style.css", "assets/app-.js", "assets/app-SHORT.js"):
            with self.subTest(rel=rel):
                self.assertFalse(_is_hashed_asset(rel))

    def test_nothing_outside_assets_may_be(self) -> None:
        for rel in ("index.html", "favicon.svg", "app-2O3PPUM7.js", "assets/app-2O3PPUM7.js.LEGAL.txt"):
            with self.subTest(rel=rel):
                self.assertFalse(_is_hashed_asset(rel))


class ServedHeaders(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ui = Path(self.tmp.name) / "ui"
        (ui / "assets").mkdir(parents=True)
        (ui / "index.html").write_text(
            '<link rel="stylesheet" href="./assets/app-XLWWT4PU.css"><script src="./assets/app-2O3PPUM7.js"></script>',
            encoding="utf-8")
        (ui / "assets" / "app-XLWWT4PU.css").write_text(".kpi{min-width:0}", encoding="utf-8")
        (ui / "assets" / "app-2O3PPUM7.js").write_text("export {}", encoding="utf-8")
        (ui / "assets" / "app.css").write_text(".kpi{min-width:0}", encoding="utf-8")
        self.h = Harness(ui_dir=ui)
        self.addCleanup(self.h.close)
        self.client = self.h.client

    def test_the_hashed_bundle_is_served_immutable(self) -> None:
        for path in ("/assets/app-XLWWT4PU.css", "/assets/app-2O3PPUM7.js"):
            with self.subTest(path=path):
                self.assertEqual(IMMUTABLE, self.client.get(path).headers["cache-control"])

    def test_a_fixed_name_is_revalidated_every_load(self) -> None:
        self.assertEqual("no-cache", self.client.get("/assets/app.css").headers["cache-control"])

    def test_index_html_is_never_pinned(self) -> None:
        """index.html is what carries the new bundle URLs, so a stale copy of it hides every later fix."""
        for path in ("/", "/index.html", "/dashboard"):
            with self.subTest(path=path):
                self.assertEqual("no-cache", self.client.get(path).headers["cache-control"])


if __name__ == "__main__":
    unittest.main()
