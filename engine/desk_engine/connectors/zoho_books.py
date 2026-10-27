"""Zoho Books API v3 (read-only), in every Zoho data centre.

The data centre is taken from the token response (``api_domain``) and the OAuth callback
(``accounts-server``), so the same connector works for .com, .eu, .in, .com.au, .jp, .ca, .sa, .uk and
.com.cn accounts.

Report responses are flattened generically (sections → accounts with totals). Invoice and bill
lists use the documented list endpoints.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .accounting import HttpBase, add_months, month_start, to_float
from .xero import _pl_section


def _section_from_name(name: str) -> str | None:
    n = name.lower()
    if "non operating" in n or "non-operating" in n or "other" in n:
        if "income" in n:
            return "Other income"
        if "expense" in n:
            return "Other expenses"
    if "cost of goods" in n or "cost of sales" in n:
        return "Cost of sales"
    return _pl_section(name)


def flatten_report(node: Any, *, section_fn: Any, path: tuple[str, ...] = ()) -> list[tuple[str, str, str, float]]:
    """Walks nested dicts/lists. A dict with a name and a numeric total and no nested list of
    accounts is a leaf account; its section comes from the nearest ancestor whose name maps to one.
    Returns (section, group, account, amount)."""
    out: list[tuple[str, str, str, float]] = []
    if isinstance(node, list):
        for item in node:
            out.extend(flatten_report(item, section_fn=section_fn, path=path))
        return out
    if not isinstance(node, dict):
        return out
    name = str(node.get("name") or node.get("account_name") or "").strip()
    children = [v for v in node.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
    new_path = (*path, name) if name else path
    if children:
        for child in children:
            out.extend(flatten_report(child, section_fn=section_fn, path=new_path))
        return out
    total = to_float(node.get("total", node.get("amount", node.get("balance"))))
    if name and total is not None and not name.lower().startswith(("total", "net ", "gross ")):
        section = None
        group = path[-1] if path else ""
        for p in reversed(path):
            section = section_fn(p)
            if section:
                break
        if section:
            out.append((section, group, name, total))
    return out


def _bs_section(name: str) -> str | None:
    n = name.lower()
    if "equity" in n and "liabilit" not in n:
        return "Equity"
    if "liabilit" in n and "equity" not in n:
        return "Liabilities"
    if "asset" in n:
        return "Assets"
    return None


def _doc(e: dict[str, Any], party_key: str, number_key: str) -> dict[str, Any]:
    status = str(e.get("status", "")).replace("_", " ").title()
    return {"number": e.get(number_key), "party": e.get(party_key), "issue_date": e.get("date"),
            "due_date": e.get("due_date"), "currency": e.get("currency_code"), "total": to_float(e.get("total")),
            "balance": to_float(e.get("balance")) or 0.0, "status": status}


class ZohoBooksClient(HttpBase):
    label = "Zoho Books"

    def auth_header(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Zoho-oauthtoken {token}"}

    @property
    def base(self) -> str:
        return f"{self.config.get('api_domain', 'https://www.zohoapis.com').rstrip('/')}/books/v3"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.get(f"{self.base}/{path}", {"organization_id": self.config["organization_id"], **(params or {})})

    async def company(self) -> dict[str, Any]:
        data = await self.get(f"{self.base}/organizations")
        for org in data.get("organizations", []):
            if str(org.get("organization_id")) == str(self.config.get("organization_id")):
                return {"name": org.get("name"), "country": org.get("country"), "currency": org.get("currency_code")}
        return {}

    async def profit_and_loss(self, start: date, end: date) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        m = month_start(start)
        while m <= end:
            last = min(add_months(m, 1) - timedelta(days=1), end)
            data = await self._get("reports/profitandloss", {"from_date": m.isoformat(), "to_date": last.isoformat()})
            body = {k: v for k, v in data.items() if k not in {"code", "message", "page_context"}}
            for section, _group, account, amount in flatten_report(body, section_fn=_section_from_name):
                if amount:
                    lines.append({"period": m.isoformat(), "section": section, "account": account, "amount": amount})
            m = add_months(m, 1)
        return lines

    async def balance_sheet(self, as_of: date) -> list[dict[str, Any]]:
        data = await self._get("reports/balancesheet", {"to_date": as_of.isoformat()})
        body = {k: v for k, v in data.items() if k not in {"code", "message", "page_context"}}
        return [{"as_of": as_of.isoformat(), "section": s, "group": g, "account": a, "amount": v}
                for s, g, a, v in flatten_report(body, section_fn=_bs_section) if v]

    async def _list(self, path: str, key: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while page <= 100:
            data = await self._get(path, {**params, "page": page, "per_page": 200})
            out.extend(data.get(key, []))
            if not data.get("page_context", {}).get("has_more_page"):
                break
            page += 1
        return out

    async def invoices(self, open_only: bool = True) -> list[dict[str, Any]]:
        since = add_months(month_start(date.today()), -24).isoformat()
        params = {"filter_by": "Status.Unpaid"} if open_only else {"date_start": since}
        return [_doc(e, "customer_name", "invoice_number") for e in await self._list("invoices", "invoices", params)]

    async def bills(self, open_only: bool = True) -> list[dict[str, Any]]:
        since = add_months(month_start(date.today()), -24).isoformat()
        params = {"filter_by": "Status.Unpaid"} if open_only else {"date_start": since}
        return [_doc(e, "vendor_name", "bill_number") for e in await self._list("bills", "bills", params)]
