"""Daftra (read-only), from its API v2: the company, profit and loss, the balance sheet, sales and purchase invoices,
and journal lines with their accounts.

Every Daftra account has its own address (``<name>.daftra.com``, or ``<name>.daftara.com`` for the Arabic site), and
the API lives under ``/api2`` on it. The address is kept in the connection's ``site`` and is accepted only when it is
one of those two forms: a token for one account is never sent anywhere else.

Field names follow Daftra's published API reference (docs.daftara.dev, read 28 Nov 2026). Where it says nothing (a
purchase invoice's totals, an invoice's due date), the connector reads the field when it is there and otherwise leaves
it empty rather than inventing it.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..errors import Invalid, UpstreamError
from .accounting import HttpBase, month_start, to_float

SITE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.(?:daftra|daftara)\.com$")
PAGE = 100          # rows per page; Daftra allows up to 100 journals and 1,000 accounts a page
MAX_PAGES = 500     # 50,000 rows of one kind in one read; beyond that the read stops and says so
COST_OF_SALES = re.compile(r"cost of (sales|goods|revenue)|تكلفة (المبيعات|البضاعة|الإيرادات)", re.I)
BANK = re.compile(r"\bbank|\bcash|بنك|البنك|مصرف|نقد|النقدية|صندوق|الصندوق", re.I)
RECEIVABLE = re.compile(r"receivable|عملاء|العملاء|ذمم مدينة|مدينون|المدينون", re.I)
PAYABLE = re.compile(r"payable|موردين|الموردين|موردون|ذمم دائنة|دائنون|الدائنون", re.I)


def site_of(value: Any) -> str:
    """A Daftra account's address, or ``Invalid`` when the value is not one."""
    site = str(value or "").strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not SITE.fullmatch(site):
        raise Invalid("That is not a Daftra account address (it looks like yourcompany.daftra.com).")
    return site


def token_url(site: str) -> str:
    return f"https://{site_of(site)}/api2/v2/oauth/token"


def _leaves(nodes: list[dict[str, Any]], top: str | None = None):
    """Each account in a report tree with the name of the top-level node it sits under."""
    for n in nodes or []:
        under = top if top is not None else str(n.get("name") or "")
        children = n.get("children") or []
        if children and not n.get("truncated_children"):
            yield from _leaves(children, under)
        else:   # an account, or a branch Daftra cut off: read at its own value
            yield n, under


def parse_profit_and_loss(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Daftra's accrual profit and loss, monthly, as the canonical lines. Expense values arrive negative with a
    multiplier of -1; value times multiplier gives every line as a positive amount, as the other connectors do."""
    periods: dict[str, str] = {}
    for col in data.get("columns") or []:
        if col.get("year") and col.get("month"):
            periods[str(col["key"])] = date(int(col["year"]), int(col["month"]), 1).isoformat()
    lines: list[dict[str, Any]] = []
    for key, section in (("income", "Income"), ("expenses", "Expenses")):
        for node, top in _leaves(data.get(key) or []):
            sec = "Cost of sales" if key == "expenses" and COST_OF_SALES.search(top) else section
            mult = to_float(node.get("multiplier")) or (1.0 if key == "income" else -1.0)
            name = " ".join(x for x in (str(node.get("code") or ""), str(node.get("name") or "")) if x)
            for col, value in (node.get("periods") or {}).items():
                v = to_float(value)
                if v is None or v == 0 or str(col) not in periods:
                    continue
                lines.append({"period": periods[str(col)], "section": sec, "account": name, "amount": round(v * mult, 2)})
    return lines


def parse_balance_sheet(data: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    """Daftra's balance sheet as the canonical lines. A group whose total is negative (credit balances shown as
    minus) is turned so that each section reads as it does in the report."""
    sections = {"assets": "Assets", "liabilities": "Liabilities", "owners_equity": "Equity"}
    lines: list[dict[str, Any]] = []
    for group in data.get("groups") or []:
        section = sections.get(str(group.get("key")))
        if section is None:
            continue
        sign = -1.0 if (to_float(group.get("total")) or 0) < 0 else 1.0
        for node, top in _leaves(group.get("accounts") or []):
            v = to_float(node.get("value"))
            if v is None or v == 0:
                continue
            name = " ".join(x for x in (str(node.get("code") or ""), str(node.get("name") or "")) if x)
            lines.append({"as_of": as_of.isoformat(), "section": section, "group": top or section, "account": name,
                          "amount": round(v * sign, 2)})
    return lines


def _doc(rec: dict[str, Any], party_key: str) -> dict[str, Any]:
    total = to_float(rec.get("summary_total"))
    balance = to_float(rec.get("summary_unpaid"))
    if balance is None and total is not None and to_float(rec.get("summary_paid")) is not None:
        balance = round(total - (to_float(rec.get("summary_paid")) or 0), 2)
    if balance is None:
        status = "unknown"
    elif balance <= 0:
        status = "paid"
    elif total is not None and balance < total:
        status = "partially paid"
    else:
        status = "open"
    return {"number": str(rec.get("no") or rec.get("id") or ""), "party": rec.get(party_key) or "",
            "issue_date": rec.get("date"), "due_date": rec.get("due_date"), "currency": rec.get("currency_code"),
            "total": total, "balance": balance, "status": status}


def account_classes(bs: dict[str, Any], pl: dict[str, Any]) -> dict[str, str]:
    """Each account id's class, from where the account sits in the two reports: a balance-sheet group or a
    profit-and-loss side, then its name for Bank, Receivable, Payable and Cost of sales."""
    out: dict[str, str] = {}
    for group in bs.get("groups") or []:
        base = {"assets": "Asset", "liabilities": "Liability", "owners_equity": "Equity"}.get(str(group.get("key")))
        if base is None:
            continue
        for node, top in _leaves(group.get("accounts") or []):
            label = f"{top} {node.get('name') or ''}"
            cls = base
            if base == "Asset" and BANK.search(label):
                cls = "Bank"
            elif base == "Asset" and RECEIVABLE.search(label):
                cls = "Receivable"
            elif base == "Liability" and PAYABLE.search(label):
                cls = "Payable"
            if node.get("id") is not None:
                out[str(node["id"])] = cls
    for key, base in (("income", "Income"), ("expenses", "Expense")):
        for node, top in _leaves(pl.get(key) or []):
            if node.get("id") is not None:
                out[str(node["id"])] = "Cost of sales" if key == "expenses" and COST_OF_SALES.search(top) else base
    return out


class DaftraClient(HttpBase):
    label = "Daftra"

    def __init__(self, engine: Any, connection: dict[str, Any]) -> None:
        super().__init__(engine, connection)
        self.base = f"https://{site_of(self.config.get('site'))}/api2"

    async def _data(self, path: str, params: dict[str, Any] | None = None) -> Any:
        body = await self.get(f"{self.base}{path}", params)
        if not isinstance(body, dict) or body.get("result") not in (None, "successful"):
            raise UpstreamError(f"Daftra answered {path} with an error: {str(body)[:200]}")
        return body.get("data")

    async def _pages(self, path: str, wrapper: str, params: dict[str, Any] | None = None,
                     limit: int = PAGE) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            body = await self.get(f"{self.base}{path}", {**(params or {}), "limit": limit, "page": page})
            items = (body or {}).get("data") or []
            rows += [it.get(wrapper, it) for it in items if isinstance(it, dict)]
            pg = (body or {}).get("pagination") or {}
            if not items or not pg.get("next") or page >= int(pg.get("page_count") or page):
                return rows
        raise UpstreamError(f"Daftra has more than {MAX_PAGES * limit:,} rows at {path}; read a shorter period.")

    async def company(self) -> dict[str, Any]:
        site = (await self._data("/site_info.json") or {}).get("Site") or {}
        return {"name": site.get("business_name"), "currency": site.get("currency_code"),
                "country": site.get("country_code")}

    async def _pl(self, start: date, end: date) -> dict[str, Any]:
        return await self._data("/reports/accounts_profit_accrual", {
            "date_from": month_start(start).isoformat(), "date_to": end.isoformat(),
            "group_by_date": "monthly", "max_level": 7}) or {}

    async def _bs(self, as_of: date) -> dict[str, Any]:
        return await self._data("/reports/balance_sheet", {"date_to": as_of.isoformat()}) or {}

    async def profit_and_loss(self, start: date, end: date) -> list[dict[str, Any]]:
        return parse_profit_and_loss(await self._pl(start, end))

    async def balance_sheet(self, as_of: date) -> list[dict[str, Any]]:
        return parse_balance_sheet(await self._bs(as_of), as_of)

    async def _docs(self, path: str, wrapper: str, party_key: str, open_only: bool) -> list[dict[str, Any]]:
        docs = [_doc(r, party_key) for r in await self._pages(path, wrapper) if not int(r.get("draft") or 0)]
        return [d for d in docs if d["balance"] and d["balance"] > 0] if open_only else docs

    async def invoices(self, open_only: bool = True) -> list[dict[str, Any]]:
        return await self._docs("/invoices.json", "Invoice", "client_business_name", open_only)

    async def bills(self, open_only: bool = True) -> list[dict[str, Any]]:
        return await self._docs("/purchase_invoices.json", "PurchaseOrder", "supplier_business_name", open_only)

    async def journal_lines(self, start: date, end: date) -> list[dict[str, Any]]:
        accounts = {str(a.get("id")): a for a in await self._pages("/journal_accounts.json", "JournalAccount",
                                                                    limit=1000)}
        classes = account_classes(await self._bs(end), await self._pl(start, end))
        lines: list[dict[str, Any]] = []
        for j in await self._pages("/journals.json", "Journal", {"date_from": start.isoformat(),
                                                                  "date_to": end.isoformat(), "draft": 0}):
            for t in j.get("JournalTransaction") or []:
                acc = accounts.get(str(t.get("journal_account_id"))) or {}
                aid = str(t.get("journal_account_id") or "")
                lines.append({
                    "date": j.get("date"), "entry": str(j.get("number") or j.get("id") or ""),
                    "type": str(j.get("entity_type") or ("journal" if not j.get("is_automatic") else "")),
                    "account_code": str(acc.get("code") or ""), "account": acc.get("name") or aid,
                    "account_class": classes.get(aid) or ("Asset" if str(acc.get("type")) == "1" else "Liability"),
                    "debit": to_float(t.get("debit")) or 0.0, "credit": to_float(t.get("credit")) or 0.0,
                    "description": t.get("description") or j.get("description") or "", "party": "", "tags": []})
        return lines
