"""Odoo Accounting (Community, Enterprise, Odoo Online / odoo.sh), read-only, with an API key.

* Odoo 19 and later: the JSON-2 API — ``POST /json/2/<model>/<method>`` with ``Authorization: bearer <api key>``
  and, when the server hosts several databases, ``X-Odoo-Database``.
* Odoo 17 and 18: the external JSON-RPC API (``/jsonrpc``, service ``object``, ``execute_kw``) with the login and
  the API key as password. Odoo has deprecated XML-RPC/JSON-RPC from 19.0 and scheduled their removal, so the
  connector uses JSON-2 whenever the server offers it.

Figures come from posted journal items (``account.move.line``) grouped by account, so the P&L and balance sheet
use Odoo's own account types and balances: balance = debit − credit in company currency.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from typing import Any

import httpx2 as httpx

from ..db import loads
from ..errors import Invalid, UpstreamError
from .accounting import add_months, month_start

PL_TYPES = {"income": "Income", "income_other": "Other income", "expense_direct_cost": "Cost of sales",
            "expense": "Expenses", "expense_depreciation": "Expenses"}
BS_TYPES = {
    "asset_receivable": ("Assets", "Receivables"), "asset_cash": ("Assets", "Bank and cash"),
    "asset_current": ("Assets", "Current assets"), "asset_prepayments": ("Assets", "Prepayments"),
    "asset_non_current": ("Assets", "Non-current assets"), "asset_fixed": ("Assets", "Fixed assets"),
    "liability_payable": ("Liabilities", "Payables"), "liability_credit_card": ("Liabilities", "Credit cards"),
    "liability_current": ("Liabilities", "Current liabilities"),
    "liability_non_current": ("Liabilities", "Non-current liabilities"),
    "equity": ("Equity", "Equity"), "equity_unaffected": ("Equity", "Current year earnings"),
}
PAYMENT_STATUS = {"not_paid": "Open", "partial": "Partially paid", "in_payment": "In payment", "paid": "Paid",
                  "reversed": "Reversed", "invoicing_legacy": "Legacy", "blocked": "Blocked"}
_ids = itertools.count(1)


class OdooClient:
    label = "Odoo"

    def __init__(self, engine: Any, connection: dict[str, Any] | None = None, *, base_url: str | None = None,
                 database: str | None = None, username: str | None = None, api_key: str | None = None,
                 api: str | None = None, company_id: int | None = None) -> None:
        self.engine = engine
        cfg = loads(connection["config"], {}) if connection else {}
        secret = (engine.secrets.get(connection["secret_id"], workspace_id=connection["workspace_id"])
                  if connection else {})
        self.base_url = (base_url or cfg.get("base_url") or "").rstrip("/")
        self.database = database if database is not None else cfg.get("database")
        self.username = username if username is not None else cfg.get("username")
        self.api_key = api_key or secret.get("api_key") or ""
        self.api = api or cfg.get("api")  # json2 | jsonrpc | None (detect)
        self.company_id = company_id if company_id is not None else cfg.get("company_id")
        self._uid: int | None = None

    # -- transport ---------------------------------------------------------------------------------------------
    async def _post(self, url: str, payload: Any, headers: dict[str, str]) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=90, transport=self.engine.http_transport) as client:
                return await client.post(url, json=payload, headers={"User-Agent": "OfinTech-Desk", **headers})
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Odoo is not reachable at {self.base_url} ({type(exc).__name__}).") from exc

    async def _json2(self, model: str, method: str, body: dict[str, Any]) -> Any:
        headers = {"Authorization": f"bearer {self.api_key}"}
        if self.database:
            headers["X-Odoo-Database"] = self.database
        resp = await self._post(f"{self.base_url}/json/2/{model}/{method}", body, headers)
        if resp.status_code == 401:
            raise UpstreamError("Odoo rejected the API key.", code="reconnect")
        if resp.status_code == 403:
            raise UpstreamError(f"The Odoo user may not read {model}; give it the Accounting / Read-only (or "
                                "Billing) access right.", code="forbidden_upstream")
        if resp.status_code >= 400:
            try:
                detail = resp.json()
                detail = detail.get("message") if isinstance(detail, dict) else detail
            except ValueError:
                detail = resp.text
            raise UpstreamError(f"Odoo returned HTTP {resp.status_code} for {model}.{method}: {str(detail)[:200]}")
        return resp.json()

    async def _rpc(self, service: str, method: str, args: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "method": "call", "id": next(_ids),
                   "params": {"service": service, "method": method, "args": args}}
        resp = await self._post(f"{self.base_url}/jsonrpc", payload, {})
        if resp.status_code >= 400:
            raise UpstreamError(f"Odoo returned HTTP {resp.status_code} on /jsonrpc.")
        data = resp.json()
        if data.get("error"):
            err = data["error"]
            msg = (err.get("data") or {}).get("message") or err.get("message") or "error"
            if "AccessDenied" in str(err) or "Access Denied" in str(msg):
                raise UpstreamError("Odoo rejected the login or API key.", code="reconnect")
            raise UpstreamError(f"Odoo: {str(msg)[:200]}")
        return data.get("result")

    async def detect(self) -> str:
        """Prefers JSON-2 (Odoo 19+); falls back to JSON-RPC for Odoo 17/18."""
        if self.api:
            return self.api
        try:
            await self._json2("res.users", "context_get", {})
            self.api = "json2"
        except UpstreamError as exc:
            if exc.code == "reconnect":
                raise
            if not self.username or not self.database:
                raise Invalid("This Odoo server does not offer the JSON-2 API (Odoo 19+). For Odoo 17 or 18 also "
                              "enter the database name and the login of the API user.") from exc
            self.api = "jsonrpc"
        return self.api

    async def call(self, model: str, method: str, *, ids: list[int] | None = None, **kwargs: Any) -> Any:
        api = await self.detect()
        context: dict[str, Any] = {"lang": "en_US"}
        if self.company_id:
            context["allowed_company_ids"] = [int(self.company_id)]
        if api == "json2":
            body: dict[str, Any] = {**kwargs, "context": context}
            if ids is not None:
                body["ids"] = ids
            return await self._json2(model, method, body)
        if self._uid is None:
            uid = await self._rpc("common", "authenticate", [self.database, self.username, self.api_key, {}])
            if not uid:
                raise UpstreamError("Odoo rejected the login or API key.", code="reconnect")
            self._uid = int(uid)
        args: list[Any] = [ids] if ids is not None else []
        if "domain" in kwargs:
            args = [kwargs.pop("domain")]
        return await self._rpc("object", "execute_kw", [self.database, self._uid, self.api_key, model, method, args,
                                                        {**kwargs, "context": context}])

    # -- helpers ---------------------------------------------------------------------------------------------------
    async def _balances(self, domain: list[Any]) -> dict[int, float]:
        """Sum of balance per account. formatted_read_group (19+) or read_group (17/18)."""
        base = [("parent_state", "=", "posted"), *domain]
        if self.company_id:
            base.append(("company_id", "=", int(self.company_id)))
        try:
            rows = await self.call("account.move.line", "formatted_read_group", domain=base, groupby=["account_id"],
                                   aggregates=["balance:sum"])
        except UpstreamError:
            rows = await self.call("account.move.line", "read_group", domain=base, fields=["balance:sum"],
                                   groupby=["account_id"], lazy=False)
        out: dict[int, float] = {}
        for r in rows or []:
            acc = r.get("account_id")
            if not acc:
                continue
            aid = acc[0] if isinstance(acc, (list, tuple)) else int(acc)
            out[aid] = float(r.get("balance:sum", r.get("balance")) or 0.0)
        return out

    async def _accounts(self) -> dict[int, dict[str, Any]]:
        rows = await self.call("account.account", "search_read", domain=[], fields=["code", "name", "account_type"])
        return {r["id"]: r for r in rows or []}

    @staticmethod
    def _name(acc: dict[str, Any]) -> str:
        code = acc.get("code") or ""
        return f"{code} {acc.get('name')}".strip() if code else str(acc.get("name"))

    # -- AccountingClient ---------------------------------------------------------------------------------------------
    async def company(self) -> dict[str, Any]:
        companies = await self.call("res.company", "search_read", domain=[], fields=["name", "currency_id"])
        if not companies:
            raise UpstreamError("This Odoo user cannot see any company.")
        chosen = next((c for c in companies if c["id"] == self.company_id), companies[0])
        cur = chosen.get("currency_id")
        return {"name": chosen["name"], "id": chosen["id"],
                "currency": cur[1] if isinstance(cur, (list, tuple)) else None,
                "companies": [{"id": c["id"], "name": c["name"]} for c in companies]}

    async def profit_and_loss(self, start: date, end: date) -> list[dict[str, Any]]:
        accounts = await self._accounts()
        pl_ids = [a for a, r in accounts.items() if r.get("account_type") in PL_TYPES]
        lines: list[dict[str, Any]] = []
        m = month_start(start)
        while m <= end:
            last = min(add_months(m, 1) - timedelta(days=1), end)
            first = max(m, start)
            bal = await self._balances([("account_id", "in", pl_ids), ("date", ">=", first.isoformat()),
                                        ("date", "<=", last.isoformat())]) if pl_ids else {}
            for aid, b in sorted(bal.items(), key=lambda kv: self._name(accounts.get(kv[0], {}))):
                acc = accounts.get(aid)
                if not acc or round(b, 2) == 0:
                    continue
                section = PL_TYPES[acc["account_type"]]
                amount = -b if section in {"Income", "Other income"} else b  # income is a credit balance
                lines.append({"period": m.isoformat(), "section": section, "account": self._name(acc),
                              "amount": round(amount, 2)})
            m = add_months(m, 1)
        return lines

    async def balance_sheet(self, as_of: date) -> list[dict[str, Any]]:
        accounts = await self._accounts()
        bal = await self._balances([("date", "<=", as_of.isoformat())])
        lines: list[dict[str, Any]] = []
        unallocated = 0.0
        for aid, b in bal.items():
            acc = accounts.get(aid)
            if not acc:
                continue
            t = acc.get("account_type")
            if t in PL_TYPES:
                unallocated += b
                continue
            if t not in BS_TYPES or round(b, 2) == 0:
                continue
            section, group = BS_TYPES[t]
            amount = b if section == "Assets" else -b  # liabilities and equity are credit balances
            lines.append({"as_of": as_of.isoformat(), "section": section, "group": group, "account": self._name(acc),
                          "amount": round(amount, 2)})
        if round(unallocated, 2):
            lines.append({"as_of": as_of.isoformat(), "section": "Equity", "group": "Current year earnings",
                          "account": "Unallocated earnings (P&L to date)", "amount": round(-unallocated, 2)})
        return sorted(lines, key=lambda x: (["Assets", "Liabilities", "Equity"].index(x["section"]), x["group"],
                                            x["account"]))

    async def _moves(self, types: list[str], open_only: bool) -> list[dict[str, Any]]:
        domain: list[Any] = [("move_type", "in", types), ("state", "=", "posted")]
        if open_only:
            domain.append(("payment_state", "in", ["not_paid", "partial"]))
        if self.company_id:
            domain.append(("company_id", "=", int(self.company_id)))
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            rows = await self.call("account.move", "search_read", domain=domain, fields=[
                "name", "partner_id", "invoice_date", "invoice_date_due", "currency_id", "amount_total_signed",
                "amount_residual_signed", "payment_state", "move_type"], limit=500, offset=offset, order="invoice_date desc")
            for r in rows or []:
                refund = r.get("move_type", "").endswith("refund")
                sign = -1 if refund else 1
                cur = r.get("currency_id")
                partner = r.get("partner_id")
                out.append({"number": r.get("name"),
                            "party": partner[1] if isinstance(partner, (list, tuple)) else None,
                            "issue_date": r.get("invoice_date") or None, "due_date": r.get("invoice_date_due") or None,
                            "currency": cur[1] if isinstance(cur, (list, tuple)) else None,
                            "total": sign * abs(float(r.get("amount_total_signed") or 0)),
                            "balance": sign * abs(float(r.get("amount_residual_signed") or 0)),
                            "status": ("Credit note " if refund else "") + PAYMENT_STATUS.get(r.get("payment_state"), "")})
            if not rows or len(rows) < 500 or offset > 20000:
                break
            offset += 500
        return out

    async def invoices(self, open_only: bool = True) -> list[dict[str, Any]]:
        return await self._moves(["out_invoice", "out_refund"], open_only)

    async def bills(self, open_only: bool = True) -> list[dict[str, Any]]:
        return await self._moves(["in_invoice", "in_refund"], open_only)
