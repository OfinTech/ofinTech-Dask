"""Xero Accounting API (read-only). Uses the granular scopes required for apps created after
2 March 2026; older apps can set broad scopes in the OAuth app options."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..data.importer import parse_date
from .accounting import HttpBase, add_months, month_start, to_float

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
