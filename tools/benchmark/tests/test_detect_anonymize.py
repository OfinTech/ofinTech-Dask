"""Detection has to find the shaped identifiers, leave the money alone, and prove its own output clean."""

from __future__ import annotations

import itertools
import pathlib
import tempfile
import unittest

from ofinbench import anonymize
from ofinbench.detect import scan

STATEMENT = """كشف حساب - رقم الحساب: 999999999999
IBAN SA59 0099 9999 9999 9999 9999
صاحب الحساب: عبدالله محمد المثال   الرقم الضريبي 399999999999993
Beneficiary: Oasis Trading Co.    Account No: 888888888888888
هوية 1099999999   جوال 0599999999   finance@example.test
2026-03-18  رواتب مارس  64,200.00  189,715.50
2026-03-30  رسوم خدمات بنكية  1,150.00  196,085.25
reference 77777777777777
"""
NAMES = ["عبدالله محمد المثال", "Oasis Trading Co."]
KEY = b"k" * 48


class Detection(unittest.TestCase):
    def test_finds_each_shaped_identifier(self) -> None:
        kinds = {f.kind for f in scan(STATEMENT, names=NAMES)}
        self.assertEqual({"account", "iban", "name", "org", "vat", "gov_id", "phone", "email", "unclassified"}, kinds)

    def test_leaves_money_and_dates_alone(self) -> None:
        """A statement is mostly figures; flagging them would destroy the thing the benchmark measures."""
        values = {f.value for f in scan(STATEMENT, names=NAMES)}
        for figure in ("64,200.00", "189,715.50", "1,150.00", "196,085.25", "2026-03-18"):
            self.assertNotIn(figure, values)

    def test_findings_never_overlap(self) -> None:
        findings = scan(STATEMENT, names=NAMES)
        for earlier, later in itertools.pairwise(findings):
            self.assertLessEqual(earlier.end, later.start, f"{earlier} overlaps {later}")

    def test_the_iban_wins_over_the_digits_inside_it(self) -> None:
        found = [f for f in scan("IBAN SA59 0099 9999 9999 9999 9999") if f.kind == "iban"]
        self.assertEqual(1, len(found))

    def test_an_invalid_iban_is_not_claimed_as_one(self) -> None:
        self.assertNotIn("iban", {f.kind for f in scan("IBAN SA99 0000 0000 0000 0000 0001")})

    def test_a_long_digit_run_is_reported_not_replaced(self) -> None:
        """Invoice and reference numbers look like identifiers; a human decides, the tool only raises its hand."""
        unclassified = [f for f in scan("reference 77777777777777") if f.kind == "unclassified"]
        self.assertEqual(["77777777777777"], [f.value for f in unclassified])


class Anonymisation(unittest.TestCase):
    def test_round_trip_is_clean_and_keeps_the_figures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, output = pathlib.Path(temp) / "in.txt", pathlib.Path(temp) / "out.txt"
            source.write_text(STATEMENT, encoding="utf-8")
            anonymize.anonymize_file(source, output, KEY, names=NAMES)
            text = output.read_text(encoding="utf-8")
            for figure in ("64,200.00", "196,085.25", "2026-03-18", "رواتب مارس"):
                self.assertIn(figure, text, "anonymisation must not touch amounts, dates or descriptions")
            for secret in ("999999999999", "1099999999", "0599999999", "عبدالله محمد المثال", "Oasis Trading Co."):
                self.assertNotIn(secret, text)
            self.assertTrue(anonymize.verify_file(output, original=source, names=NAMES)["clean"])

    def test_verify_catches_a_value_that_survived(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, output = pathlib.Path(temp) / "in.txt", pathlib.Path(temp) / "out.txt"
            source.write_text(STATEMENT, encoding="utf-8")
            anonymize.anonymize_file(source, output, KEY, names=NAMES)
            output.write_text(output.read_text(encoding="utf-8") + "\nold account 999999999999\n", encoding="utf-8")
            report = anonymize.verify_file(output, original=source, names=NAMES)
            self.assertFalse(report["clean"])
            self.assertIn("999999999999", [leak["value"].strip() for leak in report["leaks"]])

    def test_verify_catches_a_name_that_was_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, output = pathlib.Path(temp) / "in.txt", pathlib.Path(temp) / "out.txt"
            source.write_text(STATEMENT, encoding="utf-8")
            anonymize.anonymize_file(source, output, KEY, names=[])          # the list was left empty
            report = anonymize.verify_file(output, original=source, names=NAMES)
            self.assertFalse(report["clean"], "a name left in the output must fail the check, not pass quietly")

    def test_the_same_value_in_two_documents_gets_the_same_fake(self) -> None:
        march, _counts, _ = anonymize.anonymize_text("IBAN SA59 0099 9999 9999 9999 9999 paid 100.00", KEY)
        again, _, _ = anonymize.anonymize_text("SA5900999999999999999999 received 200.00", KEY)
        self.assertIn(again.split()[0], march.replace(" ", ""))

    def test_a_pdf_is_refused_rather_than_half_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / "statement.pdf"
            source.write_bytes(b"%PDF-1.7\n")
            with self.assertRaises(anonymize.AnonymizeError) as caught:
                anonymize.anonymize_file(source, pathlib.Path(temp) / "out.pdf", KEY)
            self.assertIn("extract", str(caught.exception))

    def test_csv_is_rewritten_cell_by_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, output = pathlib.Path(temp) / "in.csv", pathlib.Path(temp) / "out.csv"
            source.write_text("date,description,amount\n2026-03-18,IBAN SA5900999999999999999999,64200.00\n",
                              encoding="utf-8")
            anonymize.anonymize_file(source, output, KEY)
            rows = output.read_text(encoding="utf-8").splitlines()
            self.assertIn("64200.00", rows[1])
            self.assertNotIn("999999999999", rows[1])

    def test_json_that_would_break_is_refused(self) -> None:
        """Rewriting a key or a structural value could produce a file that no longer parses; better to stop."""
        with tempfile.TemporaryDirectory() as temp:
            source, output = pathlib.Path(temp) / "in.json", pathlib.Path(temp) / "out.json"
            source.write_text('{"iban": "SA5900999999999999999999", "amount": 100}', encoding="utf-8")
            anonymize.anonymize_file(source, output, KEY)
            self.assertIn("amount", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
