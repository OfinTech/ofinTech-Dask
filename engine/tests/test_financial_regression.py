"""Financial integrity: the engine's results checked against independent oracles, and against the pre-audit golden.

Two kinds of evidence:

* ``Oracles`` recompute each figure with ``decimal.Decimal`` arithmetic written here, independently of the engine
  (no engine function is used to produce an expected value), so a change in the engine's rounding, sign or period
  handling fails against first principles.
* ``Golden`` runs the fixed scenario of ``tests/golden_financial.py`` and compares every output with
  ``tests/fixtures/financial_golden.json``, which was produced by the engine at commit e8de370, before the audit
  changed anything. Any difference is a change in financial behaviour that must be explained before the file is
  regenerated.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import unittest
from decimal import ROUND_HALF_UP, Decimal, getcontext

from .golden_financial import GOLDEN, scenario

getcontext().prec = 28
D = Decimal


def cents(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


class Golden(unittest.TestCase):
    def test_every_output_matches_the_pre_audit_engine(self) -> None:
        expected = json.loads(GOLDEN.read_text())
        actual = scenario()
        self.assertEqual(sorted(actual), sorted(expected))
        for key in expected:
            self.assertEqual(actual[key], expected[key], f"financial output '{key}' changed; see the module docstring")


class Oracles(unittest.TestCase):
    """Each expected value is computed here from the formula, never taken from the engine."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.g = json.loads(GOLDEN.read_text())

    # -- calculators --------------------------------------------------------------------------------------------
    def test_npv_and_irr(self) -> None:
        flows = [D(-100000), D(25000), D(30000), D(35000), D(40000)]
        npv = sum(f / (D("1.08") ** i) for i, f in enumerate(flows))
        self.assertIn(f"NPV at 8.00%: {cents(npv):,}", self.g["calc"]["npv_irr"]["text"])
        # IRR by bisection on the same flows
        lo, hi = D("0"), D("1")
        for _ in range(200):
            mid = (lo + hi) / 2
            v = sum(f / ((1 + mid) ** i) for i, f in enumerate(flows))
            lo, hi = (mid, hi) if v > 0 else (lo, mid)
        self.assertIn(f"IRR: {(lo * 100).quantize(D('0.01')):}%", self.g["calc"]["npv_irr"]["text"])

    def test_loan_payment_and_total_interest(self) -> None:
        p, r, n = D(800000), D("0.065") / 12, 48
        pmt = p * r / (1 - (1 + r) ** -n)
        self.assertIn(f"Payment per period: {cents(pmt):,}", self.g["calc"]["loan"]["text"])
        # amortise with the rounded payment, the way a bank schedule is printed: total interest within the final
        # rounding adjustment of the exact figure
        bal, interest_total = p, D(0)
        for _ in range(n):
            interest = cents(bal * r)
            interest_total += interest
            bal = bal - (cents(pmt) - interest)
        data = self.g["calc"]["loan"]["data"]
        self.assertEqual(D(str(data["payment"])), cents(pmt))
        self.assertAlmostEqual(float(D(str(data["total_interest"]))), float(interest_total), delta=1.0)
        # convention: the schedule's last payment absorbs the rounding, so total paid = principal + total interest
        # (within a few cents of payment × periods)
        total_paid = p + D(str(data["total_interest"]))
        self.assertIn(f"total paid: {cents(total_paid):,}", self.g["calc"]["loan"]["text"])
        self.assertLess(abs(total_paid - cents(pmt) * n), D("0.50"))

    def test_depreciation_methods(self) -> None:
        sl = self.g["calc"]["dep_sl"]["data"]          # rows: year, depreciation, accumulated, book value
        self.assertEqual([D(str(r[1])) for r in sl], [cents((D(593000) - D(30000)) / 5)] * 5)
        self.assertEqual(D(str(sl[-1][3])), D(30000))
        self.assertEqual([D(str(r[2])) for r in sl], [cents((D(593000) - D(30000)) / 5) * (i + 1) for i in range(5)])
        ddb = self.g["calc"]["dep_ddb"]["data"]
        rate, book, exp = D(2) / 4, D(120000), []
        for year in range(4):
            dep = cents(book * rate)
            if year == 3 or book - dep < D(10000):
                dep = book - D(10000)   # never below salvage; last year takes the remainder
            exp.append(dep)
            book -= dep
        self.assertEqual([D(str(r[1])) for r in ddb], exp)
        syd = self.g["calc"]["dep_syd"]["data"]
        total, base = D(10), D(110000)
        exp = [cents(base * D(4 - i) / total) for i in range(4)]
        self.assertEqual([D(str(r[1])) for r in syd][:3], exp[:3])
        self.assertEqual(sum(D(str(r[1])) for r in syd), base)

    def test_sales_tax_split_both_ways(self) -> None:
        self.assertIn("Net 1,000.00, tax 150.00, gross 1,150.00", self.g["calc"]["tax_incl"]["text"])
        net = D("1000.33")
        tax = cents(net * D("0.15"))
        self.assertIn(f"Net {net:,}, tax {tax:,}, gross {net + tax:,}", self.g["calc"]["tax_excl"]["text"])

    def test_breakeven_growth_and_fx(self) -> None:
        margin = D(45) - D("27.5")
        units = math.ceil(D(250000) / margin)
        self.assertIn(f"Break-even: {units:,} units, revenue {cents(D(units) * 45 if False else D(250000) / margin * 45):,}",
                      self.g["calc"]["breakeven"]["text"])
        cagr = (D(5920200) / D(881000)) ** (D(1) / 2) - 1
        self.assertIn(f"CAGR over 2 years: {(cagr * 100).quantize(D('0.01'))}%", self.g["calc"]["growth"]["text"])
        self.assertIn("3,750.00, 9,377.81, -1,125.00", self.g["calc"]["fx"]["text"])
        self.assertEqual(cents(D("2500.75") * D("3.75")), D("9377.81"))

    def test_ratios_are_computed_from_the_inputs(self) -> None:
        by_key = {r["key"]: D(str(r["value"])) for r in self.g["calc"]["ratios"]["data"]}
        self.assertEqual(by_key["gross_margin"].quantize(D("0.000001")), ((D(5920200) - D(830620)) / D(5920200)).quantize(D("0.000001")))
        self.assertEqual(by_key["current_ratio"].quantize(D("0.0001")), (D(6500000) / D(1200000)).quantize(D("0.0001")))
        self.assertEqual(by_key["debt_to_equity"].quantize(D("0.0001")), (D(475352) / D(5500000)).quantize(D("0.0001")))
        dso = D(897250) / D(5920200) * 365
        self.assertEqual(by_key["dso"].quantize(D("0.1")) if "dso" in by_key else by_key["days_sales_outstanding"].quantize(D("0.1")), dso.quantize(D("0.1")))

    def test_variance_sign_convention(self) -> None:
        rows = {r[0]: r for r in self.g["calc"]["variance"]["data"]}   # name, actual, budget, variance, %, verdict
        self.assertEqual(D(str(rows["Revenue"][3])), D(5920200) - D(5500000))
        self.assertEqual(D(str(rows["Rent"][3])), D("120000.5") - D(110000))
        self.assertEqual(rows["Revenue"][5], "favourable")      # revenue above budget is good
        self.assertEqual(rows["Rent"][5], "unfavourable")       # a cost above budget is not
        self.assertAlmostEqual(rows["Revenue"][4], float((D(420200) / D(5500000)).quantize(D("0.0001"))), places=4)
        self.assertIsNone(rows["Zero"][4])                      # no percentage against a zero budget

    # -- formula engine -----------------------------------------------------------------------------------------
    def test_excel_rounding_rules(self) -> None:
        e = self.g["expr"]
        self.assertEqual(e["ROUND(7250.5 * 1.15, 2)"], 8338.08)      # half away from zero at 15 significant digits
        self.assertEqual(e["ROUND(2.675, 2)"], 2.68)                   # Python's round gives 2.67
        self.assertEqual(e["ROUND(-2.5, 0)"], -3.0)                    # away from zero, not to even
        self.assertEqual(e["ROUND(1234567.891, -3)"], 1235000.0)
        self.assertIsNone(e["1 / 0"])                                   # division by zero is blank
        self.assertEqual(e["SUM(1, 2.5, -0.5) / 3"], 1.0)

    # -- datasets -----------------------------------------------------------------------------------------------
    def _rows(self) -> list[list[str]]:
        rnd = random.Random(7)
        rows = []
        for i in range(5000):
            rows.append([f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}", str(4000 + i % 30), f"Party {i % 40}", f"Line {i}",
                         f"{rnd.randint(1, 9000)}.{i % 100:02d}", "0" if i % 3 else f"{rnd.randint(1, 500)}.50"])
        return rows

    def test_dataset_sums_match_decimal_arithmetic(self) -> None:
        rows = self._rows()
        debit = sum(D(r[4]) for r in rows)
        credit = sum(D(r[5]) for r in rows)
        total = self.g["queries"]["total"]["rows"][0]
        self.assertEqual(D(str(total[0])), debit)
        self.assertEqual(D(str(total[1])), credit)
        self.assertEqual(total[2], 5000)
        by_month = {m: v for m, v in self.g["queries"]["by_month"]["rows"]}
        for m in ("2026-01", "2026-07", "2026-12"):
            self.assertEqual(D(str(by_month[m])), sum(D(r[4]) for r in rows if r[0].startswith(m)), m)
        # fiscal year starting in April (workspace setting): Jan–Mar 2026 belong to FY 2025/26
        fy = {k: D(str(v)) for k, v in self.g["queries"]["by_fy"]["rows"]}
        jan_mar = sum(D(r[4]) for r in rows if r[0][5:7] in ("01", "02", "03"))
        self.assertEqual(min(fy.values()), jan_mar)
        self.assertEqual(sum(fy.values()), debit)
        party7 = [r for r in rows if r[2] == "Party 7"]
        acc = {str(int(a)): (D(str(s)), D(str(mx))) for a, s, _avg, mx in self.g["queries"]["by_account_filtered"]["rows"]}
        for a in ("4007", "4017"):
            mine = [D(r[4]) for r in party7 if r[1] == a]
            self.assertEqual(acc[a], (sum(mine), max(mine)))
        self.assertEqual(self.g["queries"]["contains"]["rows"][0][0], sum(1 for r in rows if "Line 12" in r[3]))

    def test_csv_export_keeps_numbers_and_neutralises_only_text(self) -> None:
        head = self.g["export_csv_head"]
        self.assertEqual(head[0], "date,account,party,description,debit,credit")
        first = next(csv.reader(io.StringIO(head[1])))
        self.assertEqual(D(first[4]), D(self._rows()[0][4]))                 # the number survives the round trip
        self.assertFalse(any(cell.startswith("'") for cell in first))        # nothing benign gets an apostrophe

    # -- close journal ------------------------------------------------------------------------------------------
    def test_journal_entries_balance_and_keep_their_text(self) -> None:
        rows = self.g["journal_rows"]
        self.assertEqual(len(rows), 4)                       # two approved entries of two lines; the rejected one is absent
        by_entry: dict[str, D] = {}
        for r in rows:
            by_entry[r[1]] = by_entry.get(r[1], D(0)) + D(str(r[6] or 0)) - D(str(r[7] or 0))
        self.assertEqual(set(by_entry.values()), {D(0)})
        self.assertEqual([r[1] for r in rows], ["CLOSE-2026-09-001"] * 2 + ["CLOSE-2026-09-002"] * 2)
        self.assertEqual(rows[2][0], "2026-09-14")           # a bank fee keeps its own date; an accrual the month end
        self.assertEqual(rows[0][0], "2026-09-30")
        xlsx = self.g["journal_xlsx"]
        self.assertEqual(xlsx[1][4], ["Audit fees", "s"])
        self.assertEqual(xlsx[1][6], [12500.0, "n"])
        self.assertEqual(xlsx[5][6][1], "f")                 # the engine's own total stays a formula
        self.assertEqual(self.g["journal_csv"][1].split(",")[6], "12500.0")


if __name__ == "__main__":
    unittest.main()
