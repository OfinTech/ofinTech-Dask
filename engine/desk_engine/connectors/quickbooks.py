"""QuickBooks Online Accounting API (read-only)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..data.importer import parse_date
from .accounting import HttpBase, add_months, month_start, to_float

MINOR_VERSION = "75"
PL_GROUPS = {"Income": "Income", "COGS": "Cost of sales", "Expenses": "Expenses", "OtherIncome": "Other income",
             "OtherExpenses": "Other expenses"}


def _col_periods(report: dict[str, Any]) -> list[str | None]:
    """ISO period start for each report column (None for the label and total columns)."""
    out: list[str | None] = []
    for col in report.get("Columns", {}).get("Column", []):
        meta = {m.get("Name"): m.get("Value") for m in col.get("MetaData", []) or []}
        if col.get("ColType") != "Money" or str(col.get("ColTitle", "")).strip().lower() == "total":
            out.append(None)
        elif meta.get("StartDate"):
            out.append(month_start(date.fromisoformat(meta["StartDate"][:10])).isoformat())
        else:
            parsed = parse_date(col.get("ColTitle"))
            out.append(month_start(parsed).isoformat() if parsed else None)
    return out


def parse_profit_and_loss(report: dict[str, Any]) -> list[dict[str, Any]]:
    periods = _col_periods(report)
    lines: list[dict[str, Any]] = []

    def amounts(coldata: list[dict[str, Any]]) -> list[tuple[str, float]]:
        out = []
        for i, cell in enumerate(coldata):
            if i < len(periods) and periods[i]:
                v = to_float(cell.get("value"))
                if v is not None:
                    out.append((periods[i], v))
        return out

    def walk(rows: list[dict[str, Any]], section: str) -> None:
        for row in rows:
            if row.get("type") == "Data" or ("ColData" in row and "Rows" not in row):
                cells = row.get("ColData", [])
                name = cells[0].get("value", "") if cells else ""
                for period, v in amounts(cells):
                    if v != 0:
                        lines.append({"period": period, "section": section, "account": name, "amount": v})
            elif "Rows" in row:
                walk(row["Rows"].get("Row", []), section)

    for top in report.get("Rows", {}).get("Row", []):
        group = top.get("group")
        if group in PL_GROUPS:
            walk(top.get("Rows", {}).get("Row", []), PL_GROUPS[group])
        elif group == "NetIncome":
            for period, v in amounts(top.get("Summary", {}).get("ColData", [])):
                lines.append({"period": period, "section": "Net profit", "account": "Net profit", "amount": v})
    return lines


def parse_balance_sheet(report: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    value_index = None
    for i, col in enumerate(report.get("Columns", {}).get("Column", [])):
        if col.get("ColType") == "Money":
            value_index = i  # the last money column is the total
    if value_index is None:
        return lines

    def section_of(path: list[str]) -> tuple[str, str] | None:
        """Deepest header that names a section, e.g. 'Current Liabilities' -> Liabilities."""
        for p in reversed(path):
            low = p.lower()
            if "equity" in low and "liabilit" not in low:
                return "Equity", p
            if "liabilit" in low and "equity" not in low:
                return "Liabilities", p
            if "asset" in low:
                return "Assets", p
        return None

    def walk(rows: list[dict[str, Any]], path: list[str]) -> None:
        for row in rows:
            if "Rows" in row:
                header = (row.get("Header", {}).get("ColData") or [{}])[0].get("value", "")
                walk(row["Rows"].get("Row", []), [*path, header])
            elif "ColData" in row:
                cells = row["ColData"]
                v = to_float(cells[value_index].get("value")) if len(cells) > value_index else None
                found = section_of(path)
                if v is None or found is None or v == 0:
                    continue
                lines.append({"as_of": as_of.isoformat(), "section": found[0], "group": found[1],
                              "account": cells[0].get("value", ""), "amount": v})

    walk(report.get("Rows", {}).get("Row", []), [])
    return lines


def _doc(e: dict[str, Any], party_key: str) -> dict[str, Any]:
    balance = to_float(e.get("Balance")) or 0.0
    due = e.get("DueDate")
    status = "Paid" if balance == 0 else ("Overdue" if due and due < date.today().isoformat() else "Open")
    return {"number": e.get("DocNumber") or e.get("Id"), "party": (e.get(party_key) or {}).get("name"),
            "issue_date": e.get("TxnDate"), "due_date": due, "currency": (e.get("CurrencyRef") or {}).get("value"),
            "total": to_float(e.get("TotalAmt")), "balance": balance, "status": status}


class QuickBooksClient(HttpBase):
    label = "QuickBooks Online"

    @property
    def base(self) -> str:
        host = ("https://sandbox-quickbooks.api.intuit.com" if self.config.get("environment") == "sandbox"
                else "https://quickbooks.api.intuit.com")
        return f"{host}/v3/company/{self.config['realm_id']}"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.get(f"{self.base}/{path}", {**(params or {}), "minorversion": MINOR_VERSION})

    async def company(self) -> dict[str, Any]:
        info = (await self._get(f"companyinfo/{self.config['realm_id']}")).get("CompanyInfo", {})
        currency = None
        try:
            prefs = (await self._get("preferences")).get("Preferences", {})
            currency = prefs.get("CurrencyPrefs", {}).get("HomeCurrency", {}).get("value")
        except Exception:  # preferences need extra permission on some companies
            currency = None
        return {"name": info.get("CompanyName"), "country": info.get("Country"), "currency": currency}

    async def profit_and_loss(self, start: date, end: date) -> list[dict[str, Any]]:
        report = await self._get("reports/ProfitAndLoss", {"start_date": start.isoformat(), "end_date": end.isoformat(),
                                                           "summarize_column_by": "Month"})
        return parse_profit_and_loss(report)

    async def balance_sheet(self, as_of: date) -> list[dict[str, Any]]:
        report = await self._get("reports/BalanceSheet", {"start_date": as_of.replace(month=1, day=1).isoformat(),
                                                          "end_date": as_of.isoformat()})
        return parse_balance_sheet(report, as_of)

    async def _query(self, entity: str, where: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = 1
        while True:
            q = f"select * from {entity} {where} startposition {start} maxresults 1000"
            data = (await self._get("query", {"query": q})).get("QueryResponse", {})
            batch = data.get(entity, [])
            out.extend(batch)
            if len(batch) < 1000 or len(out) >= 20000:
                return out
            start += 1000

    async def invoices(self, open_only: bool = True) -> list[dict[str, Any]]:
        since = add_months(month_start(date.today()), -24).isoformat()
        where = "where Balance > '0'" if open_only else f"where TxnDate >= '{since}'"
        return [_doc(e, "CustomerRef") for e in await self._query("Invoice", where)]

    async def bills(self, open_only: bool = True) -> list[dict[str, Any]]:
        since = (date.today() - timedelta(days=730)).isoformat()
        where = "where Balance > '0'" if open_only else f"where TxnDate >= '{since}'"
        return [_doc(e, "VendorRef") for e in await self._query("Bill", where)]
