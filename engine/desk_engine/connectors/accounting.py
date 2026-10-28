"""Common model for accounting systems, dataset sync and the agent's accounting tools.

Canonical shapes produced by every connector:

* P&L lines: ``period`` (first day of month), ``section`` (Income, Cost of sales, Expenses, Other income,
  Other expenses, Net profit), ``account``, ``amount`` (positive for income and for costs; Net profit signed)
* Balance sheet lines: ``as_of``, ``section`` (Assets, Liabilities, Equity), ``group``, ``account``, ``amount``
* Documents (invoices and bills): ``number``, ``party``, ``issue_date``, ``due_date``, ``currency``, ``total``,
  ``balance``, ``status``
* General-ledger (journal) lines: ``date``, ``entry`` (groups the lines of one journal entry), ``type``,
  ``account_code``, ``account``, ``account_class`` (one of ``ACCOUNT_CLASSES``), ``debit``, ``credit``,
  ``description``, ``party``, ``tags`` (list of ``{"dimension", "value", "percent"}``: classes, tracking categories,
  analytic accounts, departments). Bank lines are the lines on accounts of class ``Bank``.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import httpx2 as httpx

from ..db import loads, now
from ..errors import Invalid, UpstreamError
from .oauth import TokenManager

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger(__name__)
ACCOUNTING_PROVIDERS = {"quickbooks", "zoho_books", "xero", "odoo", "custom"}
SECTIONS = ["Income", "Cost of sales", "Expenses", "Other income", "Other expenses", "Net profit"]


class AccountingClient(Protocol):
    label: str

    async def company(self) -> dict[str, Any]: ...

    async def profit_and_loss(self, start: date, end: date) -> list[dict[str, Any]]: ...

    async def balance_sheet(self, as_of: date) -> list[dict[str, Any]]: ...

    async def invoices(self, open_only: bool = True) -> list[dict[str, Any]]: ...

    async def bills(self, open_only: bool = True) -> list[dict[str, Any]]: ...

    # optional: ``async def journal_lines(self, start: date, end: date) -> list[dict[str, Any]]``


ACCOUNT_CLASSES = ["Bank", "Receivable", "Payable", "Asset", "Liability", "Equity", "Income", "Other income",
                   "Cost of sales", "Expense", "Other expense"]
COST_CLASSES = {"Cost of sales", "Expense", "Other expense"}
INCOME_CLASSES = {"Income", "Other income"}


class HttpBase:
    """Authorised JSON requests with one retry after a token refresh and clear error messages."""

    label = "Connected system"

    def __init__(self, engine: Engine, connection: dict[str, Any]) -> None:
        self.engine = engine
        self.connection = connection
        self.config = loads(connection["config"], {})
        self.tokens = TokenManager(engine, connection)

    def auth_header(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def extra_headers(self) -> dict[str, str]:
        return {}

    async def get(self, url: str, params: dict[str, Any] | None = None, *, raw: bool = False) -> Any:
        for attempt in (0, 1):
            token = await self.tokens.access_token(force_refresh=attempt == 1)
            headers = {"Accept": "application/json", **self.auth_header(token), **self.extra_headers()}
            async with httpx.AsyncClient(timeout=60, transport=self.engine.http_transport) as client:
                resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 401 and attempt == 0:
                continue
            if resp.status_code == 429:
                raise UpstreamError(f"{self.label} is rate limiting requests; try again in a minute.",
                                    code="rate_limited")
            if resp.status_code >= 400:
                raise UpstreamError(f"{self.label} returned HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.content if raw else resp.json()
        raise UpstreamError(f"{self.label} rejected the access token. Reconnect it.", code="reconnect")


def client_for(engine: Engine, connection: dict[str, Any]) -> AccountingClient:
    provider = connection["provider"]
    if provider == "quickbooks":
        from .quickbooks import QuickBooksClient
        return QuickBooksClient(engine, connection)
    if provider == "zoho_books":
        from .zoho_books import ZohoBooksClient
        return ZohoBooksClient(engine, connection)
    if provider == "xero":
        from .xero import XeroClient
        return XeroClient(engine, connection)
    if provider == "odoo":
        from .odoo import OdooClient
        return OdooClient(engine, connection)
    if provider == "custom":
        from .open_api import PullClient, PushedClient
        if loads(connection["config"], {}).get("mode") == "pull":
            return PullClient(engine, connection)
        return PushedClient(engine, connection)
    raise Invalid(f"{provider} is not an accounting connection.")


# --- helpers ---------------------------------------------------------------------------------------


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def to_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def aging_bucket(days_overdue: int | None) -> str:
    if days_overdue is None or days_overdue <= 0:
        return "0: Not yet due"
    if days_overdue <= 30:
        return "1: 1–30 days"
    if days_overdue <= 60:
        return "2: 31–60 days"
    if days_overdue <= 90:
        return "3: 61–90 days"
    return "4: Over 90 days"


def with_aging(docs: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    out = []
    for d in docs:
        due = d.get("due_date")
        days = None
        if due and (d.get("balance") or 0) > 0:
            try:
                days = (today - date.fromisoformat(str(due)[:10])).days
            except ValueError:
                days = None
        out.append({**d, "days_overdue": max(days, 0) if days is not None else 0, "aging_bucket": aging_bucket(days)})
    return out


def add_net_profit(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adds one 'Net profit' line per period when the source did not provide it."""
    if any(line["section"] == "Net profit" for line in lines):
        return lines
    totals: dict[str, float] = {}
    sign = {"Income": 1, "Other income": 1, "Cost of sales": -1, "Expenses": -1, "Other expenses": -1}
    for line in lines:
        totals[line["period"]] = totals.get(line["period"], 0.0) + sign.get(line["section"], 0) * line["amount"]
    return lines + [{"period": p, "section": "Net profit", "account": "Net profit", "amount": round(v, 2)}
                    for p, v in sorted(totals.items())]


# --- dataset sync ----------------------------------------------------------------------------------

PL_COLUMNS = [{"key": "c0", "name": "Period", "type": "date"}, {"key": "c1", "name": "Section", "type": "text"},
              {"key": "c2", "name": "Account", "type": "text"}, {"key": "c3", "name": "Amount", "type": "number"}]
BS_COLUMNS = [{"key": "c0", "name": "As of", "type": "date"}, {"key": "c1", "name": "Section", "type": "text"},
              {"key": "c2", "name": "Group", "type": "text"}, {"key": "c3", "name": "Account", "type": "text"},
              {"key": "c4", "name": "Amount", "type": "number"}]


GL_COLUMNS = [{"key": f"c{i}", "name": n, "type": t} for i, (n, t) in enumerate([
    ("Date", "date"), ("Entry", "text"), ("Type", "text"), ("Account code", "text"), ("Account", "text"),
    ("Account class", "text"), ("Debit", "number"), ("Credit", "number"), ("Description", "text"), ("Party", "text"),
    ("Cost centre tags", "text")])]


def format_tags(tags: list[dict[str, Any]] | None) -> str:
    """``Project: Riyadh tower | Department: Site (60%)`` — the form stored in the general-ledger dataset."""
    parts = []
    for t in tags or []:
        value = str(t.get("value") or "").strip()
        if not value:
            continue
        dim = str(t.get("dimension") or "").strip()
        text = f"{dim}: {value}" if dim else value
        pct = t.get("percent")
        if pct is not None and abs(float(pct) - 100) > 1e-9:
            text += f" ({round(float(pct), 4):g}%)"
        parts.append(text.replace("|", "/"))
    return " | ".join(parts)


def gl_rows(lines: list[dict[str, Any]]) -> list[list[Any]]:
    out = []
    for ln in lines:
        cls = ln.get("account_class") if ln.get("account_class") in ACCOUNT_CLASSES else "Asset"
        out.append([str(ln.get("date"))[:10], str(ln.get("entry") or ""), ln.get("type") or None,
                    ln.get("account_code") or None, ln.get("account") or "", cls,
                    round(float(ln.get("debit") or 0), 2), round(float(ln.get("credit") or 0), 2),
                    ln.get("description") or None, ln.get("party") or None, format_tags(ln.get("tags")) or None])
    return out


def debit_credit(amount: float) -> tuple[float, float]:
    """A signed amount (positive = debit) as a debit and a credit."""
    return (round(amount, 2), 0.0) if amount >= 0 else (0.0, round(-amount, 2))


def doc_columns(party: str) -> list[dict[str, str]]:
    names = [("Number", "text"), (party, "text"), ("Issue date", "date"), ("Due date", "date"), ("Currency", "text"),
             ("Total", "number"), ("Balance", "number"), ("Status", "text"), ("Days overdue", "number"),
             ("Aging bucket", "text")]
    return [{"key": f"c{i}", "name": n, "type": t} for i, (n, t) in enumerate(names)]


def doc_rows(docs: list[dict[str, Any]]) -> list[list[Any]]:
    return [[d.get("number"), d.get("party"), d.get("issue_date"), d.get("due_date"), d.get("currency"),
             d.get("total"), d.get("balance"), d.get("status"), d.get("days_overdue"), d.get("aging_bucket")]
            for d in docs]


async def sync_connection(engine: Engine, connection: dict[str, Any], *, months: int = 24,
                          user_id: str | None = None) -> dict[str, Any]:
    from ..data.store import upsert_named

    if is_push(connection):
        raise Invalid("This connection receives data from your system (push); there is nothing to fetch.")
    client = client_for(engine, connection)
    ws_id = connection["workspace_id"]
    today = date.today()
    start = add_months(month_start(today), -(months - 1))
    label = connection["label"]
    ref = {"connection_id": connection["id"], "provider": connection["provider"]}
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    try:
        pl = add_net_profit(await client.profit_and_loss(start, today))
        ds = upsert_named(engine, ws_id, name=f"{label} · Profit and loss", source=connection["provider"],
                          source_ref=ref, columns=PL_COLUMNS,
                          rows=[[r["period"], r["section"], r["account"], r["amount"]] for r in pl], created_by=user_id)
        results["profit_and_loss"] = ds["id"]
    except UpstreamError as exc:
        errors["profit_and_loss"] = exc.message
    try:
        bs = await client.balance_sheet(today)
        ds = upsert_named(engine, ws_id, name=f"{label} · Balance sheet", source=connection["provider"],
                          source_ref=ref, columns=BS_COLUMNS,
                          rows=[[r["as_of"], r["section"], r["group"], r["account"], r["amount"]] for r in bs],
                          created_by=user_id)
        results["balance_sheet"] = ds["id"]
    except UpstreamError as exc:
        errors["balance_sheet"] = exc.message
    for kind, party, fetch in (("invoices", "Customer", client.invoices), ("bills", "Vendor", client.bills)):
        try:
            docs = with_aging(await fetch(open_only=False))
            ds = upsert_named(engine, ws_id, name=f"{label} · {kind.title()}", source=connection["provider"],
                              source_ref=ref, columns=doc_columns(party), rows=doc_rows(docs), created_by=user_id)
            results[kind] = ds["id"]
        except UpstreamError as exc:
            errors[kind] = exc.message
    if hasattr(client, "journal_lines"):
        try:
            gl = await client.journal_lines(start, today)  # type: ignore[attr-defined]
            ds = upsert_named(engine, ws_id, name=f"{label} · General ledger", source=connection["provider"],
                              source_ref=ref | {"kind": "general_ledger"}, columns=GL_COLUMNS, rows=gl_rows(gl),
                              created_by=user_id)
            results["general_ledger"] = ds["id"]
        except UpstreamError as exc:
            errors["general_ledger"] = exc.message
        except Exception as exc:  # noqa: BLE001 - the journal lines are optional; the other reports still count
            log.warning("journal lines from %s could not be read", connection["provider"], exc_info=True)
            errors["general_ledger"] = f"the journal lines could not be read ({type(exc).__name__})"
    status = "connected" if results else "error"
    engine.db.update("connections", connection["id"], {
        "last_sync_at": now(), "status": status,
        "last_error": "; ".join(f"{k}: {v}" for k, v in errors.items()) or None}, workspace_id=ws_id)
    return {"datasets": results, "errors": errors}


def is_push(connection: dict[str, Any]) -> bool:
    return connection["provider"] == "custom" and loads(connection["config"], {}).get("mode") != "pull"


def default_period(start: str | None, end: str | None) -> tuple[date, date]:
    today = date.today()
    e = date.fromisoformat(end) if end else today
    s = date.fromisoformat(start) if start else month_start(e)
    if s > e:
        raise Invalid("start_date is after end_date.")
    if (e - s) > timedelta(days=366 * 5):
        raise Invalid("Choose a period of at most five years.")
    return s, e
