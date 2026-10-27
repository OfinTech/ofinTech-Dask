import unittest
from datetime import date

from desk_engine.agent import finance_tools as f


class FinanceMath(unittest.TestCase):
    def test_npv_irr(self) -> None:
        cfs = [-1000, 300, 400, 500]
        self.assertAlmostEqual(f.npv(0.1, cfs), -1000 + 300 / 1.1 + 400 / 1.21 + 500 / 1.331, places=8)
        r = f.irr(cfs)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(f.npv(r, cfs), 0, places=6)
        self.assertAlmostEqual(r, 0.0889633947, places=6)  # matches spreadsheet IRR
        self.assertIsNone(f.irr([100, 200]))  # no sign change

    def test_xirr(self) -> None:
        # Excel documentation example for XIRR: 0.373362535
        cfs = [-10000, 2750, 4250, 3250, 2750]
        dates = [date(2008, 1, 1), date(2008, 3, 1), date(2008, 10, 30), date(2009, 2, 15), date(2009, 4, 1)]
        self.assertAlmostEqual(f.xirr(cfs, dates), 0.373362535, places=5)
        self.assertAlmostEqual(f.xnpv(0.09, cfs, dates), 2086.6476, places=2)  # Excel XNPV example

    def test_amortization(self) -> None:
        rows = f.amortization(200000, 0.06, 360)
        self.assertEqual(float(rows[0]["payment"]), 1199.10)
        self.assertEqual(float(rows[-1]["balance"]), 0.0)
        self.assertAlmostEqual(float(sum(r["principal"] for r in rows)), 200000, places=2)

    def test_depreciation(self) -> None:
        sl = f.depreciation(10000, 1000, 5)
        self.assertEqual([float(r["depreciation"]) for r in sl], [1800.0] * 5)
        ddb = f.depreciation(10000, 1000, 5, "double_declining")
        self.assertEqual(float(ddb[0]["depreciation"]), 4000.0)
        self.assertEqual(float(ddb[-1]["book_value"]), 1000.0)
        syd = f.depreciation(10000, 1000, 5, "sum_of_years")
        self.assertEqual(float(syd[0]["depreciation"]), 3000.0)
        self.assertAlmostEqual(sum(float(r["depreciation"]) for r in syd), 9000.0, places=2)

    def test_ratios_and_ccc(self) -> None:
        rows = {r["key"]: r["value"] for r in f.ratios({"revenue": 1000, "cogs": 600, "receivables": 100,
                                                         "inventory": 60, "payables": 50, "current_assets": 300,
                                                         "current_liabilities": 150})}
        self.assertAlmostEqual(rows["gross_margin"], 0.4)
        self.assertAlmostEqual(rows["current_ratio"], 2.0)
        self.assertAlmostEqual(rows["dso"], 36.5)
        self.assertAlmostEqual(rows["ccc"], 36.5 + 36.5 - 30.416666, places=4)
        self.assertNotIn("roe", rows)

    def test_tax_split(self) -> None:
        r = f.tax_split(120, 0.2, True)
        self.assertEqual((float(r["net"]), float(r["tax"])), (100.0, 20.0))
        r = f.tax_split(100, 0.15, False)
        self.assertEqual(float(r["gross"]), 115.0)


if __name__ == "__main__":
    unittest.main()
