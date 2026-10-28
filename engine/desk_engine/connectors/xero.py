"""Xero Accounting API (read-only). Uses the granular scopes required for apps created after
2 March 2026; older apps can set broad scopes in the OAuth app options."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..data.importer import parse_date
from .accounting import HttpBase, add_months, debit_credit, month_start, to_float

API = "https://api.xero.com/api.xro/2.0"


def _pl_section(title: str) -> str | None:
    t = title.lower()
    if "cost of sales" in t or "cost of goods" in t or "direct cost" in t:
        return "Cost of sales"
    if "other income" in t:
        return "Other income"
    if "other expense" in t:
        return "Other expenses"
    if "expense" in t or "overhead" in t:
        return "Expenses"
    if "income" in t or "revenue" in t or "sales" in t:
        return "Income"
    return None


def parse_profit_and_loss(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = report.get("Rows", [])
    periods: list[str | None] = []
    for row in rows:
        if row.get("RowType") == "Header":
            for i, cell in enumerate(row.get("Cells", [])):
                parsed = parse_date(cell.get("Value")) if i > 0 else None
                periods.append(month_start(parsed).isoformat() if parsed else None)
    lines: list[dict[str, Any]] = []
    for row in rows:
        if row.get("RowType") != "Section":
            continue
        section = _pl_section(row.get("Title") or "")
        for r in row.get("Rows", []):
            cells = r.get("Cells", [])
            if not cells:
                continue
            name = str(cells[0].get("Value", ""))
            is_net = name.lower() in {"net profit", "net loss", "net profit (loss)", "net income"}
            if r.get("RowType") != "Row" or (section is None and not is_net):
                continue
            for i, cell in enumerate(cells[1:], start=1):
                if i < len(periods) and periods[i]:
                    v = to_float(cell.get("Value"))
                    if v is None or (v == 0 and not is_net):
                        continue
                    lines.append({"period": periods[i], "section": "Net profit" if is_net else section,
                                  "account": "Net profit" if is_net else name, "amount": v})
    return lines


def parse_balance_sheet(report: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    lines = []
    for row in report.get("Rows", []):
        if row.get("RowType") != "Section":
            continue
        title = row.get("Title") or ""
        low = title.lower()
        section = ("Liabilities" if "liabilit" in low else "Equity" if "equity" in low else
                   "Assets" if ("asset" in low or "bank" in low) else None)
        if section is None:
            continue
        for r in row.get("Rows", []):
            if r.get("RowType") != "Row":
                continue
            cells = r.get("Cells", [])
            if len(cells) < 2:
                continue
            v = to_float(cells[1].get("Value"))
            if v is None or v == 0:
                continue
            lines.append({"as_of": as_of.isoformat(), "section": section, "group": title,
                          "account": str(cells[0].get("Value", "")), "amount": v})
    return lines


def _xero_date(s: str | None) -> str | None:
    if not s:
        return None
    return s[:10] if s[:4].isdigit() else None


def _doc(e: dict[str, Any]) -> dict[str, Any]:
    balance = to_float(e.get("AmountDue")) or 0.0
    status = {"AUTHORISED": "Open", "PAID": "Paid", "VOIDED": "Void", "DRAFT": "Draft", "SUBMITTED": "Submitted",
              "DELETED": "Deleted"}.get(e.get("Status", ""), e.get("Status"))
    due = _xero_date(e.get("DueDateString"))
    if status == "Open" and due and due < date.today().isoformat():
        status = "Overdue"
    return {"number": e.get("InvoiceNumber") or e.get("Reference") or e.get("InvoiceID"),
            "party": (e.get("Contact") or {}).get("Name"), "issue_date": _xero_date(e.get("DateString")),
            "due_date": due, "currency": e.get("CurrencyCode"), "total": to_float(e.get("Total")),
            "balance": balance, "status": status}


ACCOUNT_CLASS = {"BANK": "Bank", "REVENUE": "Income", "SALES": "Income", "OTHERINCOME": "Other income",
                 "DIRECTCOSTS": "Cost of sales", "EXPENSE": "Expense", "OVERHEADS": "Expense",
                 "DEPRECIATN": "Expense", "CURRENT": "Asset", "FIXED": "Asset", "INVENTORY": "Asset",
                 "NONCURRENT": "Asset", "PREPAYMENT": "Asset", "CURRLIAB": "Liability", "LIABILITY": "Liability",
                 "TERMLIAB": "Liability", "PAYGLIABILITY": "Liability", "SUPERANNUATIONEXPENSE": "Expense",
                 "SUPERANNUATIONLIABILITY": "Liability", "WAGESEXPENSE": "Expense",
                 "WAGESPAYABLELIABILITY": "Liability", "EQUITY": "Equity"}
SYSTEM_CLASS = {"DEBTORS": "Receivable", "CREDITORS": "Payable"}
_MS_DATE = re.compile(r"/Date\((-?\d+)")


def xero_date(v: Any) -> str | None:
    """Xero JSON dates: ``/Date(1458086400000+0000)/`` or ISO."""
    if not v:
        return None
    m = _MS_DATE.search(str(v))
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date().isoformat()
    return str(v)[:10] if str(v)[:4].isdigit() else None


def parse_journals(journals: list[dict[str, Any]], classes: dict[str, str]) -> list[dict[str, Any]]:
    """Journal lines from ``GET /Journals``. NetAmount is positive for a debit. Xero journals carry no contact,
    so ``party`` is empty."""
    out = []
    for j in journals:
        day = xero_date(j.get("JournalDate"))
        for ln in j.get("JournalLines") or []:
            amount = to_float(ln.get("NetAmount")) or 0.0
            if not amount:
                continue
            dr, cr = debit_credit(amount)
            cls = classes.get(ln.get("AccountID") or "") or ACCOUNT_CLASS.get(str(ln.get("AccountType") or ""), "Asset")
            tags = [{"dimension": t.get("Name"), "value": t.get("Option")} for t in ln.get("TrackingCategories") or []]
            out.append({"date": day, "entry": f"J{j.get('JournalNumber')}", "type": j.get("SourceType"),
                        "account_code": ln.get("AccountCode"), "account": ln.get("AccountName") or "",
                        "account_class": cls, "debit": dr, "credit": cr,
                        "description": ln.get("Description") or j.get("Reference"), "party": None, "tags": tags})
    return out


class XeroClient(HttpBase):
    label = "Xero"

    def extra_headers(self) -> dict[str, str]:
        return {"xero-tenant-id": self.config["tenant_id"]}

    async def company(self) -> dict[str, Any]:
        org = (await self.get(f"{API}/Organisation")).get("Organisations", [{}])[0]
        return {"name": org.get("Name"), "country": org.get("CountryCode"), "currency": org.get("BaseCurrency")}

    async def profit_and_loss(self, start: date, end: date) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        chunk_end = month_start(end)
        first = month_start(start)
        while chunk_end >= first:
            chunk_start = max(add_months(chunk_end, -11), first)
            periods = (chunk_end.year - chunk_start.year) * 12 + chunk_end.month - chunk_start.month
            last_day = end if chunk_end == month_start(end) else add_months(chunk_end, 1) - timedelta(days=1)
            params: dict[str, Any] = {"fromDate": chunk_end.isoformat(), "toDate": last_day.isoformat()}
            if periods:
                params |= {"periods": periods, "timeframe": "MONTH"}
            data = await self.get(f"{API}/Reports/ProfitAndLoss", params)
            lines.extend(parse_profit_and_loss((data.get("Reports") or [{}])[0]))
            chunk_end = add_months(chunk_start, -1)
        return lines

    async def balance_sheet(self, as_of: date) -> list[dict[str, Any]]:
        data = await self.get(f"{API}/Reports/BalanceSheet", {"date": as_of.isoformat()})
        return parse_balance_sheet((data.get("Reports") or [{}])[0], as_of)

    async def _documents(self, kind: str, open_only: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        since = add_months(month_start(date.today()), -24)
        where = f'Type=="{kind}"'
        if not open_only:
            where += f" AND Date>=DateTime({since.year},{since.month:02d},01)"
        page = 1
        while page <= 100:
            params = {"where": where, "page": page, "Statuses": "AUTHORISED" if open_only else "AUTHORISED,PAID"}
            batch = (await self.get(f"{API}/Invoices", params)).get("Invoices", [])
            out.extend(_doc(e) for e in batch)
            if len(batch) < 100:
                break
            page += 1
        return out

    async def invoices(self, open_only: bool = True) -> list[dict[str, Any]]:
        return await self._documents("ACCREC", open_only)

    async def bills(self, open_only: bool = True) -> list[dict[str, Any]]:
        return await self._documents("ACCPAY", open_only)

    async def journal_lines(self, start: date, end: date) -> list[dict[str, Any]]:
        """Every posted journal (``accounting.journals.read``), 100 per call, kept within the period."""
        classes: dict[str, str] = {}
        for a in (await self.get(f"{API}/Accounts")).get("Accounts", []):
            cls = SYSTEM_CLASS.get(str(a.get("SystemAccount") or "")) or ACCOUNT_CLASS.get(str(a.get("Type") or ""))
            if cls and a.get("AccountID"):
                classes[a["AccountID"]] = cls
        journals: list[dict[str, Any]] = []
        offset = 0
        for _ in range(500):
            batch = (await self.get(f"{API}/Journals", {"offset": offset})).get("Journals", [])
            for j in batch:
                d = xero_date(j.get("JournalDate"))
                if d and start.isoformat() <= d <= end.isoformat():
                    journals.append(j)
            if len(batch) < 100:
                break
            offset = max(int(j.get("JournalNumber") or 0) for j in batch)
        return parse_journals(journals, classes)
