"""QuickBooks Online Accounting API (read-only)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..data.importer import parse_date
from .accounting import HttpBase, add_months, debit_credit, month_start, to_float

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


ACCOUNT_CLASS = {"Bank": "Bank", "Accounts Receivable": "Receivable", "Other Current Asset": "Asset",
                 "Fixed Asset": "Asset", "Other Asset": "Asset", "Accounts Payable": "Payable",
                 "Credit Card": "Liability", "Other Current Liability": "Liability", "Long Term Liability": "Liability",
                 "Equity": "Equity", "Income": "Income", "Other Income": "Other income",
                 "Cost of Goods Sold": "Cost of sales", "Expense": "Expense", "Other Expense": "Other expense"}
GL_COLUMNS = "tx_date,txn_type,doc_num,name,memo,account_name,klass_name,dept_name,debt_amt,credit_amt,subt_nat_amount"


def parse_general_ledger(report: dict[str, Any], accounts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows of the GeneralLedger report, grouped under account sections. ``accounts`` maps account id and name to
    ``{"code", "class"}`` (from the Account query)."""
    keys: list[str] = []
    for col in report.get("Columns", {}).get("Column", []):
        meta = {m.get("Name"): m.get("Value") for m in col.get("MetaData", []) or []}
        keys.append(meta.get("ColKey") or str(col.get("ColTitle") or "").lower())
    out: list[dict[str, Any]] = []

    def cell(cells: list[dict[str, Any]], key: str) -> dict[str, Any]:
        return cells[keys.index(key)] if key in keys and keys.index(key) < len(cells) else {}

    def walk(rows: list[dict[str, Any]], account: dict[str, Any]) -> None:
        for row in rows:
            if "Rows" in row:
                head = (row.get("Header", {}).get("ColData") or [{}])[0]
                acc = accounts.get(str(head.get("id") or "")) or accounts.get(str(head.get("value") or "")) or {}
                walk(row["Rows"].get("Row", []), {"name": head.get("value") or account.get("name"), **acc}
                     if head.get("value") else account)
                continue
            cells = row.get("ColData") or []
            if row.get("type") not in (None, "Data") or not cells:
                continue
            day = cell(cells, "tx_date").get("value")
            if not day or not str(day)[:4].isdigit():
                continue  # beginning-balance rows have no date
            name = cell(cells, "account_name").get("value") or account.get("name") or ""
            acc = accounts.get(str(cell(cells, "account_name").get("id") or "")) or accounts.get(name) or account
            dr = to_float(cell(cells, "debt_amt").get("value"))
            cr = to_float(cell(cells, "credit_amt").get("value"))
            if dr is None and cr is None:
                dr, cr = debit_credit(to_float(cell(cells, "subt_nat_amount").get("value")) or 0.0)
            dr, cr = dr or 0.0, cr or 0.0
            if not dr and not cr:
                continue
            txn = cell(cells, "txn_type")
            tags = [{"dimension": "Class", "value": cell(cells, "klass_name").get("value")},
                    {"dimension": "Location", "value": cell(cells, "dept_name").get("value")}]
            out.append({"date": str(day)[:10], "entry": f"{txn.get('value')}:{txn.get('id') or cell(cells, 'doc_num').get('value')}",
                        "type": txn.get("value"), "account_code": acc.get("code"), "account": name,
                        "account_class": acc.get("class") or "Asset", "debit": dr, "credit": cr,
                        "description": cell(cells, "memo").get("value") or cell(cells, "doc_num").get("value"),
                        "party": cell(cells, "name").get("value") or None,
                        "tags": [t for t in tags if t["value"]]})

    walk(report.get("Rows", {}).get("Row", []), {})
    return out


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

    async def journal_lines(self, start: date, end: date) -> list[dict[str, Any]]:
        """The GeneralLedger report, a quarter at a time, with each line's class and location."""
        accounts: dict[str, dict[str, Any]] = {}
        for a in await self._query("Account", ""):
            info = {"code": a.get("AcctNum"), "class": ACCOUNT_CLASS.get(a.get("AccountType") or "", "Asset")}
            for k in (a.get("Id"), a.get("Name"), a.get("FullyQualifiedName")):
                if k:
                    accounts[str(k)] = info
        lines: list[dict[str, Any]] = []
        chunk = month_start(start)
        while chunk <= end:
            last = min(add_months(chunk, 3) - timedelta(days=1), end)
            report = await self._get("reports/GeneralLedger", {
                "start_date": max(chunk, start).isoformat(), "end_date": last.isoformat(), "columns": GL_COLUMNS,
                "accounting_method": "Accrual"})
            lines.extend(parse_general_ledger(report, accounts))
            chunk = add_months(chunk, 3)
        return lines
