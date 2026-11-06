"""Every chart of accounts in fixtures/charts-of-accounts is classified on every build.

A ledger arrives with a class column in whatever words the customer's system uses -- *debtors*, *creditors*,
*Sales*, *ايرادات* -- and an account name in whatever language the company keeps its books in. Two steps turn
that into something the statements and the ratios can use: the class word becomes one of ACCOUNT_CLASSES, and
the class plus the name suggest a role. Both steps are word lists, and word lists are only as good as the
charts they have been shown.

So a contributor sends a chart from their country with the role each account should get, and this test holds
the classification to it. A fixture may also record a **gap**: a class word the engine does not know, or a role
it suggests wrongly. A recorded gap is asserted to still reproduce, so the list cannot quietly rot: when
somebody fixes the word list, this test fails and tells them to delete the gap from the fixture.

See fixtures/charts-of-accounts/README.md.
"""

from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path

from desk_engine.analysis import roles
from desk_engine.costcentres.templates import _cls

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "charts-of-accounts"


def fixtures() -> list[Path]:
    return sorted(p.parent for p in CORPUS.rglob("expected.json")) if CORPUS.is_dir() else []


def rows(folder: Path) -> list[dict[str, str]]:
    with (folder / "accounts.csv").open(encoding="utf-8-sig", newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


class ChartCorpus(unittest.TestCase):
    def test_the_corpus_is_not_empty(self) -> None:
        self.assertTrue(fixtures(), f"no fixtures under {CORPUS}")

    def test_every_chart_is_classified_as_its_country_writes_it(self) -> None:
        for folder in fixtures():
            meta = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
            gaps = meta.get("gaps") or {}
            class_gaps = {str(g["written"]): g for g in gaps.get("classes") or []}
            role_gaps = {str(g["code"]): g for g in gaps.get("roles") or []}
            self.assertIs(meta.get("invented"), True, "fixtures hold invented data only")

            for row in rows(folder):
                code, name, written = row["Code"], row["Account"], row["Account class"]
                expected = row["Expected role"]
                with self.subTest(fixture=str(folder.relative_to(CORPUS)), account=f"{code} {name}"):
                    self.assertIn(expected, roles.ROLES, "the expected role is not one the product has")
                    cls = _cls(written)

                    if written in class_gaps:
                        self.assertIsNone(cls, f"“{written}” is recognised now — delete it from the gaps in "
                                               "expected.json, and give the row its real expectation")
                        continue
                    self.assertIsNotNone(cls, f"the account class “{written}” is not recognised. If that is the "
                                              "finding, record it under gaps.classes in expected.json")

                    suggested = roles.suggest(cls, name)
                    if code in role_gaps:
                        recorded = role_gaps[code]
                        self.assertNotEqual(expected, suggested,
                                            f"{code} is suggested as {suggested} now, which is what the fixture "
                                            "expects — delete it from the gaps in expected.json")
                        self.assertEqual(recorded.get("got"), suggested,
                                         f"{code} is suggested as {suggested}, but the gap records "
                                         f"{recorded.get('got')} — update expected.json")
                        continue
                    self.assertEqual(expected, suggested, f"{code} {name} (class {cls})")
                    self.assertTrue(roles.role_fits(suggested, cls),
                                    "a balance-sheet account took a profit-and-loss role, or the other way round")

    def test_a_gap_is_recorded_with_a_reason(self) -> None:
        # A gap without a reason is a hidden failing test, which is worse than a failing test.
        for folder in fixtures():
            meta = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
            gaps = meta.get("gaps") or {}
            for gap in (gaps.get("classes") or []):
                with self.subTest(fixture=str(folder.relative_to(CORPUS)), gap=gap.get("written")):
                    self.assertTrue(str(gap.get("expected") or "").strip(), "a class gap needs an expected class")
                    self.assertGreater(len(str(gap.get("why") or "")), 40, "a gap needs a reason worth reading")
            for gap in (gaps.get("roles") or []):
                with self.subTest(fixture=str(folder.relative_to(CORPUS)), gap=gap.get("code")):
                    self.assertIn(str(gap.get("expected")), roles.ROLES)
                    self.assertIn(str(gap.get("got")), roles.ROLES)
                    self.assertGreater(len(str(gap.get("why") or "")), 40, "a gap needs a reason worth reading")


if __name__ == "__main__":
    unittest.main()
