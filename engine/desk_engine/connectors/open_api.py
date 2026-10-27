"""Open Accounting Connector: connect any accounting system, ERP or in-house ledger.

Two ways, one data format (the models below):

* **Pull** — you run a small HTTPS adapter in front of your system that answers five GET endpoints. OfinTech Desk
  calls it with a bearer key on every sync and whenever the assistant asks for a report.
* **Push** — your system sends the same JSON to OfinTech Desk's ingest API with a per-connection ingest key
  (useful behind firewalls, or from scheduled exports).

The OpenAPI 3.1 description is built from these models (``openapi()``) and served at
``/api/connector-spec/openapi.json``; docs/ACCOUNTING_API.md explains it with examples.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Literal

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import VERSION
from ..db import loads
from ..errors import Invalid, UpstreamError

if TYPE_CHECKING:
    from ..app import Engine

PLSection = Literal["Income", "Cost of sales", "Expenses", "Other income", "Other expenses"]
BSSection = Literal["Assets", "Liabilities", "Equity"]
ISODate = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", examples=["2026-03-31"])


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Company(_M):
    """The company (legal entity) whose books are exposed."""
    name: str = Field(min_length=1, max_length=200, examples=["Acme Trading LLC"])
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 code of the base currency",
                          examples=["SAR"])
    country: str | None = Field(default=None, max_length=2, description="ISO 3166-1 alpha-2", examples=["SA"])
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)


class PLLine(_M):
    """One account's net amount for one calendar month."""
    period: str = Field(pattern=r"^\d{4}-\d{2}-01$", description="first day of the month", examples=["2026-03-01"])
    section: PLSection
    account: str = Field(min_length=1, max_length=200, description="account code and name",
                         examples=["4000 Sales"])
    amount: float = Field(description="positive for income and for costs; negative only for reversals",
                          examples=[125400.0])


class BSLine(_M):
    as_of: str = ISODate
    section: BSSection
    group: str = Field(default="", max_length=120, examples=["Current assets"])
    account: str = Field(min_length=1, max_length=200, examples=["1100 Accounts receivable"])
    amount: float = Field(description="positive for normal balances (assets debit; liabilities and equity credit)",
                          examples=[48210.5])


class Document(_M):
    """A customer invoice (receivable) or supplier bill (payable). Credit notes have negative amounts."""
    number: str = Field(min_length=1, max_length=80, examples=["INV-2026-0042"])
    party: str | None = Field(default=None, max_length=200, examples=["Northwind Retail"])
    issue_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    total: float
    balance: float = Field(description="amount still open; 0 when paid")
    status: str | None = Field(default=None, max_length=40, examples=["Open"])


class PLResponse(_M):
    lines: list[PLLine] = Field(max_length=200000)


class BSResponse(_M):
    lines: list[BSLine] = Field(max_length=50000)


class DocumentsResponse(_M):
    documents: list[Document] = Field(max_length=200000)
    next_cursor: str | None = Field(default=None, description="pull only: pass as ?cursor= to get the next page")


class IngestResult(_M):
    kind: str
    rows: int
    dataset_id: str | None = None


KINDS = {"profit-and-loss": "profit_and_loss", "balance-sheet": "balance_sheet", "invoices": "invoices",
         "bills": "bills", "company": "company"}


def _validate(model: type[_M], data: Any, what: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[:3]
        detail = "; ".join(".".join(str(x) for x in e["loc"]) + f": {e['msg']}" for e in first)
        raise UpstreamError(f"The connector's {what} response does not match the Open Accounting Connector format "
                            f"({detail}).", code="bad_connector_response") from exc


# --- pull ------------------------------------------------------------------------------------------------------

class PullClient:
    """Calls a customer-run adapter."""

    label = "Accounting connector"

    def __init__(self, engine: Engine, connection: dict[str, Any] | None = None, *, base_url: str | None = None,
                 api_key: str | None = None) -> None:
        self.engine = engine
        cfg = loads(connection["config"], {}) if connection else {}
        secret = (engine.secrets.get(connection["secret_id"], workspace_id=connection["workspace_id"])
                  if connection and connection.get("secret_id") else {})
        self.base_url = (base_url or cfg.get("base_url") or "").rstrip("/")
        self.api_key = api_key or secret.get("api_key") or ""

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=120, transport=self.engine.http_transport) as client:
                resp = await client.get(f"{self.base_url}{path}", params=params,
                                        headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json",
                                                 "User-Agent": f"OfinTech-Desk/{VERSION}",
                                                 "X-OAC-Version": "1"})
        except httpx.HTTPError as exc:
            raise UpstreamError(f"The accounting connector at {self.base_url} is not reachable "
                                f"({type(exc).__name__}).") from exc
        if resp.status_code in (401, 403):
            raise UpstreamError("The accounting connector rejected the key.", code="reconnect")
        if resp.status_code >= 400:
            raise UpstreamError(f"The accounting connector returned HTTP {resp.status_code} for {path}: "
                                f"{resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise UpstreamError(f"The accounting connector did not return JSON for {path}.") from exc

    async def company(self) -> dict[str, Any]:
        return _validate(Company, await self._get("/v1/company"), "company").model_dump()

    async def profit_and_loss(self, start: date, end: date) -> list[dict[str, Any]]:
        data = _validate(PLResponse, await self._get("/v1/profit-and-loss", {"start": start.isoformat(),
                                                                              "end": end.isoformat()}), "profit-and-loss")
        return [ln.model_dump() for ln in data.lines]

    async def balance_sheet(self, as_of: date) -> list[dict[str, Any]]:
        data = _validate(BSResponse, await self._get("/v1/balance-sheet", {"as_of": as_of.isoformat()}), "balance-sheet")
        return [ln.model_dump() for ln in data.lines]

    async def _docs(self, path: str, open_only: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(400):
            params: dict[str, Any] = {"status": "open" if open_only else "all"}
            if cursor:
                params["cursor"] = cursor
            page = _validate(DocumentsResponse, await self._get(path, params), path.rsplit("/", 1)[1])
            out.extend(d.model_dump() for d in page.documents)
            cursor = page.next_cursor
            if not cursor:
                break
        return out

    async def invoices(self, open_only: bool = True) -> list[dict[str, Any]]:
        return await self._docs("/v1/invoices", open_only)

    async def bills(self, open_only: bool = True) -> list[dict[str, Any]]:
        return await self._docs("/v1/bills", open_only)


# --- push: stored datasets serve the same reads ------------------------------------------------------------------

class PushedClient:
    """Answers the assistant's report questions from the data a system pushed in."""

    label = "Accounting connector"

    def __init__(self, engine: Engine, connection: dict[str, Any]) -> None:
        self.engine = engine
        self.connection = connection

    def _rows(self, kind: str) -> list[list[Any]]:
        from ..data.store import table_name

        name = f"{self.connection['label']} · {kind}"
        ds = self.engine.db.one("SELECT id, columns FROM datasets WHERE workspace_id = ? AND name = ? AND source = ?",
                                (self.connection["workspace_id"], name, "custom"))
        if ds is None:
            raise UpstreamError(f"No {kind.lower()} has been pushed to this connection yet.", code="no_data")
        keys = ", ".join(c["key"] for c in loads(ds["columns"], []))
        return [list(r) for r in self.engine.db.conn.execute(f"SELECT {keys} FROM {table_name(ds['id'])}").fetchall()]

    async def company(self) -> dict[str, Any]:
        return loads(self.connection["config"], {}).get("company") or {"name": self.connection["label"]}

    async def profit_and_loss(self, start: date, end: date) -> list[dict[str, Any]]:
        out = []
        for p, s, a, amt in self._rows("Profit and loss"):
            if s != "Net profit" and start.isoformat()[:7] <= str(p)[:7] <= end.isoformat()[:7]:
                out.append({"period": str(p)[:10], "section": s, "account": a, "amount": amt})
        return out

    async def balance_sheet(self, as_of: date) -> list[dict[str, Any]]:
        rows = self._rows("Balance sheet")
        dates = sorted({str(r[0])[:10] for r in rows if str(r[0])[:10] <= as_of.isoformat()})
        if not dates:
            raise UpstreamError(f"No balance sheet on or before {as_of} has been pushed.", code="no_data")
        return [{"as_of": d, "section": s, "group": g, "account": a, "amount": amt}
                for d, s, g, a, amt in rows if str(d)[:10] == dates[-1]]

    def _documents(self, kind: str, open_only: bool) -> list[dict[str, Any]]:
        keys = ["number", "party", "issue_date", "due_date", "currency", "total", "balance", "status"]
        docs = [dict(zip(keys, r[:8], strict=False)) for r in self._rows(kind)]
        return [d for d in docs if not open_only or (d.get("balance") or 0) != 0]

    async def invoices(self, open_only: bool = True) -> list[dict[str, Any]]:
        return self._documents("Invoices", open_only)

    async def bills(self, open_only: bool = True) -> list[dict[str, Any]]:
        return self._documents("Bills", open_only)


async def ingest(engine: Engine, connection: dict[str, Any], kind: str, payload: Any) -> IngestResult:
    """Validates pushed data and replaces the matching dataset."""
    from ..data.store import upsert_named
    from ..db import dumps, now
    from .accounting import BS_COLUMNS, PL_COLUMNS, add_net_profit, doc_columns, doc_rows, with_aging

    if kind not in KINDS:
        raise Invalid(f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}.")
    ws_id = connection["workspace_id"]
    label = connection["label"]
    ref = {"connection_id": connection["id"], "provider": "custom", "pushed_at": now()}

    def bad(exc: ValidationError) -> Invalid:
        return Invalid("The data does not match the Open Accounting Connector format.",
                       details=exc.errors(include_url=False)[:20])

    try:
        if kind == "company":
            company = Company.model_validate(payload).model_dump()
            cfg = loads(connection["config"], {}) | {"company": company}
            engine.db.update("connections", connection["id"], {"config": dumps(cfg)}, workspace_id=ws_id)
            return IngestResult(kind=kind, rows=1)
        if kind == "profit-and-loss":
            lines = [ln.model_dump() for ln in PLResponse.model_validate(payload).lines]
            lines = add_net_profit(lines)
            ds = upsert_named(engine, ws_id, name=f"{label} · Profit and loss", source="custom", source_ref=ref,
                              columns=PL_COLUMNS, rows=[[x["period"], x["section"], x["account"], x["amount"]]
                                                        for x in lines], created_by=None)
        elif kind == "balance-sheet":
            bs = [ln.model_dump() for ln in BSResponse.model_validate(payload).lines]
            ds = upsert_named(engine, ws_id, name=f"{label} · Balance sheet", source="custom", source_ref=ref,
                              columns=BS_COLUMNS, rows=[[x["as_of"], x["section"], x["group"], x["account"],
                                                         x["amount"]] for x in bs], created_by=None)
        else:
            docs = with_aging([d.model_dump() for d in DocumentsResponse.model_validate(payload).documents])
            party = "Customer" if kind == "invoices" else "Vendor"
            ds = upsert_named(engine, ws_id, name=f"{label} · {kind.title()}", source="custom", source_ref=ref,
                              columns=doc_columns(party), rows=doc_rows(docs), created_by=None)
    except ValidationError as exc:
        raise bad(exc) from exc
    engine.db.update("connections", connection["id"], {"last_sync_at": now(), "status": "connected",
                                                       "last_error": None}, workspace_id=ws_id)
    return IngestResult(kind=kind, rows=ds["row_count"], dataset_id=ds["id"])


# --- OpenAPI ------------------------------------------------------------------------------------------------------

def openapi(public_url: str = "https://desk.example.com") -> dict[str, Any]:
    models = [Company, PLLine, BSLine, Document, PLResponse, BSResponse, DocumentsResponse, IngestResult]
    schemas: dict[str, Any] = {}
    for m in models:
        s = m.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(s.pop("$defs", {}))
        schemas[m.__name__] = s
    schemas["Error"] = {"type": "object", "properties": {"error": {"type": "string"}, "message": {"type": "string"},
                                                         "details": {}}, "required": ["error", "message"]}

    def ref(name: str) -> dict[str, Any]:
        return {"$ref": f"#/components/schemas/{name}"}

    def ok(name: str, desc: str) -> dict[str, Any]:
        return {"200": {"description": desc, "content": {"application/json": {"schema": ref(name)}}},
                "401": {"description": "Missing or wrong key"}}

    status_param = {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["open", "all"]},
                    "description": "open: balance ≠ 0; all: every posted document"}
    cursor_param = {"name": "cursor", "in": "query", "schema": {"type": "string"}, "required": False}
    adapter = {
        "/v1/company": {"get": {"tags": ["Adapter (you implement)"], "operationId": "adapterCompany",
                                "summary": "The company whose books are exposed", "responses": ok("Company", "Company")}},
        "/v1/profit-and-loss": {"get": {
            "tags": ["Adapter (you implement)"], "operationId": "adapterProfitAndLoss",
            "summary": "Net amount per account per calendar month (posted entries only)",
            "parameters": [{"name": "start", "in": "query", "required": True, "schema": {"type": "string", "format": "date"}},
                           {"name": "end", "in": "query", "required": True, "schema": {"type": "string", "format": "date"}}],
            "responses": ok("PLResponse", "P&L lines")}},
        "/v1/balance-sheet": {"get": {
            "tags": ["Adapter (you implement)"], "operationId": "adapterBalanceSheet",
            "summary": "Balance per account at a date",
            "parameters": [{"name": "as_of", "in": "query", "required": True,
                            "schema": {"type": "string", "format": "date"}}],
            "responses": ok("BSResponse", "Balance sheet lines")}},
        "/v1/invoices": {"get": {"tags": ["Adapter (you implement)"], "operationId": "adapterInvoices",
                                 "summary": "Customer invoices and credit notes", "parameters": [status_param, cursor_param],
                                 "responses": ok("DocumentsResponse", "One page of documents")}},
        "/v1/bills": {"get": {"tags": ["Adapter (you implement)"], "operationId": "adapterBills",
                              "summary": "Supplier bills and credit notes", "parameters": [status_param, cursor_param],
                              "responses": ok("DocumentsResponse", "One page of documents")}},
    }
    body = {"profit-and-loss": "PLResponse", "balance-sheet": "BSResponse", "invoices": "DocumentsResponse",
            "bills": "DocumentsResponse", "company": "Company"}
    push = {
        f"/api/ingest/v1/accounting/{kind}": {"post": {
            "tags": ["Push (OfinTech Desk implements)"], "operationId": f"push{kind.title().replace('-', '')}",
            "summary": f"Replace the {kind.replace('-', ' ')} held for this connection",
            "security": [{"ingestKey": []}],
            "servers": [{"url": public_url}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": ref(model)}}},
            "responses": {"200": {"description": "Stored", "content": {"application/json": {"schema": ref("IngestResult")}}},
                          "401": {"description": "Missing or wrong ingest key"},
                          "422": {"description": "The data does not match the format",
                                  "content": {"application/json": {"schema": ref("Error")}}},
                          "429": {"description": "Too many requests"}}}}
        for kind, model in body.items()
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "OfinTech Desk — Open Accounting Connector", "version": "1.0.0",
                 "description": "Connect any accounting system. Pull: implement the five GET endpoints in an HTTPS "
                                "adapter in front of your system; OfinTech Desk calls them with `Authorization: Bearer "
                                "<adapter key>`. Push: send the same JSON to the ingest endpoints with the connection's "
                                "ingest key. Amounts are in the company's base currency unless a document says "
                                "otherwise. See docs/ACCOUNTING_API.md."},
        "servers": [{"url": "https://your-adapter.example.com", "description": "your adapter (pull)"}],
        "paths": {**adapter, **push},
        "components": {"schemas": schemas, "securitySchemes": {
            "adapterKey": {"type": "http", "scheme": "bearer", "description": "the key you configure in the connection"},
            "ingestKey": {"type": "http", "scheme": "bearer", "description": "the connection's ingest key (odk_…)"}}},
        "security": [{"adapterKey": []}],
    }
