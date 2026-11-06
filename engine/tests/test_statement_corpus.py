"""Every bank-statement fixture in fixtures/statements is read on every build.

A contributor sends the shape of their bank's statement, invented row by row, with what the file says in
`expected.json`. From then on the build fails if that shape stops reading, which is the only way a reader that
works for one country's banks keeps working for them while it is changed for another's.

The fixture folder is the contribution; this test is just the thing that runs it. See fixtures/README.md.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from desk_engine.close.statement import read_statement

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "statements"
MONEY = 0.005


def fixtures() -> list[Path]:
    return sorted(p.parent for p in CORPUS.rglob("expected.json")) if CORPUS.is_dir() else []


class StatementCorpus(unittest.TestCase):
    def test_the_corpus_is_not_empty(self) -> None:
        self.assertTrue(fixtures(), f"no fixtures under {CORPUS}")

    def test_every_fixture_reads_as_its_file_says(self) -> None:
        for folder in fixtures():
            with self.subTest(fixture=str(folder.relative_to(CORPUS))):
                meta = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
                self.assertIs(meta.get("invented"), True, "fixtures hold invented data only")
                path = folder / str(meta.get("file") or "statement.csv")
                got = read_statement(path.read_bytes(), path.name)
                expect = meta.get("expect") or {}
                self.assertTrue(expect, "expected.json needs an \"expect\" block: run fixtures/validate.py")

                if "lines" in expect:
                    self.assertEqual(expect["lines"], len(got["lines"]), "number of statement lines")
                for key in ("first_date", "last_date"):
                    if key in expect:
                        self.assertEqual(expect[key], got[key], key)
                if "total" in expect:
                    total = round(sum(line["amount"] for line in got["lines"]), 2)
                    self.assertAlmostEqual(float(expect["total"]), total, delta=MONEY,
                                           msg="the statement's lines do not add up to what the file says")
                if "closing" in expect:
                    want, have = expect["closing"], got["closing"]
                    if want is None:
                        self.assertIsNone(have, "this file has no balance column, so there is no closing balance")
                    else:
                        self.assertIsNotNone(have, "the closing balance was not read")
                        self.assertEqual(want["date"], have["date"], "closing balance date")
                        self.assertAlmostEqual(float(want["amount"]), have["amount"], delta=MONEY,
                                               msg="closing balance")

    def test_money_in_is_positive_and_money_out_is_negative(self) -> None:
        # Every reader in the product agrees on this sign, and a fixture that disagrees would teach the next
        # person the wrong rule.
        for folder in fixtures():
            meta = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
            path = folder / str(meta.get("file") or "statement.csv")
            got = read_statement(path.read_bytes(), path.name)
            with self.subTest(fixture=str(folder.relative_to(CORPUS))):
                self.assertTrue(all(abs(line["amount"]) > MONEY for line in got["lines"]),
                                "a line with no amount is not a statement line")
                self.assertTrue(any(line["amount"] < 0 for line in got["lines"])
                                or any(line["amount"] > 0 for line in got["lines"]))


if __name__ == "__main__":
    unittest.main()
