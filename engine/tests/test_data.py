import io
import unittest
from datetime import date, datetime

from openpyxl import Workbook

from desk_engine.data import importer
from tests.helpers import Harness


def xlsx(rows: list[list[object]], title: str = "Sheet1", extra_sheet: bool = False) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    if extra_sheet:
        wb.create_sheet("Notes").append(["just text"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class Parsing(unittest.TestCase):
    def test_numbers(self) -> None:
        p = importer.parse_number
        self.assertEqual(p("1,234.50"), 1234.5)
        self.assertEqual(p("(1,234.50)"), -1234.5)
        self.assertEqual(p("$ 99"), 99)
        self.assertEqual(p("EUR 1.234,56", decimal_comma=True), 1234.56)
        self.assertEqual(p("12.5%"), 0.125)
        self.assertEqual(p("1 234"), 1234)
        self.assertEqual(p("₹2,50,000"), 250000)
        self.assertEqual(p("500-"), -500)
        self.assertIsNone(p("n/a"))
        self.assertIsNone(p("—"))

    def test_dates(self) -> None:
        d = importer.parse_date
        self.assertEqual(d("2026-03-31"), date(2026, 3, 31))
        self.assertEqual(d("31/03/2026"), date(2026, 3, 31))
        self.assertEqual(d("03/31/2026"), date(2026, 3, 31))
        self.assertEqual(d("04/03/2026", "MDY"), date(2026, 4, 3))
        self.assertEqual(d("04/03/2026", "DMY"), date(2026, 3, 4))
        self.assertEqual(d("Mar 2026"), date(2026, 3, 1))
        self.assertEqual(d("31 Mar 26"), date(2026, 3, 31))
        self.assertEqual(d("March 5, 2026"), date(2026, 3, 5))
        self.assertIsNone(d("not a date"))

    def test_header_detection_and_types(self) -> None:
        raw = [["Acme Ltd - Expenses export"], [], ["Date", "Account", "Amount", "Memo"],
               [datetime(2026, 1, 5), "Rent", 1200, "Jan"], [datetime(2026, 2, 5), "Rent", "1,200.00", None],
               [datetime(2026, 2, 9), "Travel", "(50.00)", "refund"]]
        t = importer.shape(importer.Table("x", raw))
        self.assertEqual(t.header_row, 2)
        self.assertEqual([(c.name, c.type) for c in t.columns],
                         [("Date", "date"), ("Account", "text"), ("Amount", "number"), ("Memo", "text")])
        self.assertEqual(t.rows[2], ["2026-02-09", "Travel", -50.0, "refund"])

    def test_csv_european(self) -> None:
        data = "Datum;Konto;Betrag\n31.01.2026;Miete;1.200,00\n28.02.2026;Reise;-45,50\n".encode()
        t = importer.shape(importer.read_workbook(data, "export.csv")[0])
        self.assertEqual([c.type for c in t.columns], ["date", "text", "number"])
        self.assertEqual(t.rows[1], ["2026-02-28", "Reise", -45.5])

    def test_unpivot_months(self) -> None:
        raw = [["Account", "Jan 2026", "Feb 2026", "Mar 2026"], ["Sales", 100, 120, 130], ["Rent", 50, 50, None]]
        t = importer.shape(importer.Table("pl", raw))
        self.assertEqual(importer.period_columns(t), ["Jan 2026", "Feb 2026", "Mar 2026"])
        long = importer.unpivot(t, ["Account"])
        self.assertEqual([c.name for c in long.columns], ["Account", "Period", "Amount"])
        self.assertEqual(long.columns[1].type, "date")
        self.assertEqual(len(long.rows), 5)
        self.assertIn(["Rent", "2026-02-01", 50.0], long.rows)


class DataApi(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace(with_model=False)

    def tearDown(self) -> None:
        self.h.close()

    def _import(self) -> dict:
        rows = [["Date", "Account", "Department", "Amount"]]
        rows += [[datetime(2025, m, 15), acct, dept, amt] for m in (3, 4, 5, 6)
                 for acct, dept, amt in (("Sales", "North", 1000.0 * m), ("Rent", "HQ", 500.0), ("Travel", "North", 75.5))]
        up = self.h.api("POST", f"/api/w/{self.ws}/imports",
                        files={"file": ("ledger.xlsx", xlsx(rows, "Ledger", extra_sheet=True))})
        self.assertEqual([s["sheet"] for s in up["sheets"]], ["Ledger", "Notes"])
        self.assertEqual(up["sheets"][0]["row_count"], 12)
        return self.h.api("POST", f"/api/w/{self.ws}/imports/{up['upload_id']}/commit",
                          json={"sheet": "Ledger", "name": "General ledger"})

    def test_import_query_fiscal_year_and_dashboard(self) -> None:
        h, ws = self.h, self.ws
        ds = self._import()
        self.assertEqual(ds["row_count"], 12)
        # fiscal year starts in April (workspace setting): March 2025 is FY2025, April-June are FY2026
        res = h.api("POST", f"/api/w/{ws}/query", json={
            "dataset": "General ledger", "measures": [{"column": "Amount", "agg": "sum"}],
            "group_by": [{"column": "Date", "grain": "fiscal_year"}]})
        self.assertEqual(res["rows"], [["FY2025", 3575.5], ["FY2026", 4000 + 5000 + 6000 + 3 * 575.5]])
        res = h.api("POST", f"/api/w/{ws}/query", json={
            "dataset": ds["id"], "measures": [{"column": "Amount", "agg": "sum"}], "group_by": [{"column": "Account"}],
            "filters": [{"column": "Department", "op": "in", "value": ["North"]}], "sort": [{"by": "Amount", "dir": "desc"}]})
        self.assertEqual(res["rows"][0], ["Sales", 18000.0])
        # quarter grain and last_n_months
        res = h.api("POST", f"/api/w/{ws}/query", json={
            "dataset": ds["id"], "measures": [{"agg": "count"}], "group_by": [{"column": "Date", "grain": "quarter"}],
            "filters": [{"column": "Date", "op": "last_n_months", "value": 2}]})
        self.assertEqual(res["rows"], [["2025-Q2", 6]])
        # bad column and injection attempts are rejected, not executed
        r = h.api("POST", f"/api/w/{ws}/query", json={"dataset": ds["id"], "measures": [
            {"column": "Amount); DROP TABLE users; --", "agg": "sum"}]}, expect=422)
        self.assertIn("has no column", r["message"])
        res = h.api("POST", f"/api/w/{ws}/query", json={"dataset": ds["id"], "measures": [{"agg": "count"}],
                                                        "filters": [{"column": "Account", "op": "eq",
                                                                     "value": "x' OR '1'='1"}]})
        self.assertEqual(res["rows"], [[0]])
        # dashboards from template and explicit widgets
        dash = h.api("POST", f"/api/w/{ws}/dashboards/from-template", json={"template": "dataset_overview",
                                                                              "dataset_id": ds["id"]})
        view = h.api("GET", f"/api/w/{ws}/dashboards/{dash['id']}")
        self.assertTrue(all("data" in w for w in view["widgets"]), view["widgets"])
        self.assertEqual(view["widgets"][0]["data"]["rows"], [[12]])
        self.assertEqual(view["currency"], "GBP")
        h.api("POST", f"/api/w/{ws}/dashboards", json={"name": "Bad", "widgets": [
            {"title": "x", "type": "kpi", "query": {"dataset": ds["id"], "measures": [{"column": "Nope"}]}}]}, expect=422)
        # CSV export neutralises formulas
        h.engine.db.execute(f"UPDATE ds_{ds['id']} SET c1 = '=HYPERLINK(\"http://x\")' WHERE _row = 1")
        csv_text = h.client.get(f"/api/w/{ws}/datasets/{ds['id']}/export.csv").text
        self.assertIn("'=HYPERLINK", csv_text)

    def test_reshape_preview_and_unpivot_commit(self) -> None:
        h, ws = self.h, self.ws
        rows = [["Budget FY26"], ["Account", "Apr-25", "May-25", "Jun-25"], ["Revenue", 100, 110, 120],
                ["Payroll", 60, 60, 61]]
        up = h.api("POST", f"/api/w/{ws}/imports", files={"file": ("budget.xlsx", xlsx(rows))})
        self.assertEqual(up["sheets"][0]["period_columns"], ["Apr-25", "May-25", "Jun-25"])
        prev = h.api("POST", f"/api/w/{ws}/imports/{up['upload_id']}/preview",
                     json={"unpivot": {"id_columns": ["Account"], "value_name": "Budget", "variable_name": "Month"}})
        self.assertEqual(prev["row_count"], 6)
        ds = h.api("POST", f"/api/w/{ws}/imports/{up['upload_id']}/commit",
                   json={"name": "Budget", "unpivot": {"id_columns": ["Account"], "value_name": "Budget",
                                                       "variable_name": "Month"}})
        self.assertEqual([c["name"] for c in ds["columns"]], ["Account", "Month", "Budget"])
        # the upload is consumed
        h.api("POST", f"/api/w/{ws}/imports/{up['upload_id']}/commit", json={}, expect=404)

    def test_rejects_old_xls_and_garbage(self) -> None:
        r = self.h.api("POST", f"/api/w/{self.ws}/imports", files={"file": ("a.xls", b"\xd0\xcf\x11\xe0")}, expect=422)
        self.assertEqual(r["error"], "unsupported_file")
        r = self.h.api("POST", f"/api/w/{self.ws}/imports", files={"file": ("a.xlsx", b"not a zip")}, expect=422)
        self.assertEqual(r["error"], "unreadable_file")


if __name__ == "__main__":
    unittest.main()
