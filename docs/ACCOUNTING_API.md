# Open Accounting Connector

Use the Open Accounting Connector to link any accounting system, ERP or in-house ledger that OfinTech Desk does not
support directly. Examples include Tally, Sage, SAP Business One, Dynamics, MYOB, Busy and your own database.

Once the system is linked, it gets the same features as QuickBooks, Zoho Books, Xero and Odoo:

- datasets for profit and loss by month, the balance sheet, invoices and bills;
- dashboard templates;
- the assistant's `accounting_report` and `list_invoices` tools;
- workflows;
- evidence-linked workbooks and reports.

The machine-readable description is OpenAPI 3.1. You can get it in two places:

- the file [`docs/openapi/accounting-connector.json`](openapi/accounting-connector.json);
- `GET /api/connector-spec/openapi.json` on any OfinTech Desk engine.

Open the file in Swagger UI, Postman or Insomnia, or use it to generate a server stub.

## Choose a mode

| | Pull | Push |
|---|---|---|
| Who calls whom | OfinTech Desk calls **your adapter** (five GET endpoints). | **Your system** posts JSON to OfinTech Desk. |
| Data freshness | On every sync (daily, or when someone clicks *Sync*). The assistant also reads reports live. | Whenever you send. |
| Network | Your adapter needs an HTTPS address that the engine can reach. | Your system needs to reach the Team Server over HTTPS. Nothing needs to be opened towards your network. |
| Typical use | A live system that has an API or a database you can query. | Systems behind a firewall, nightly exports, or systems without an API. |
| Authentication | `Authorization: Bearer <adapter key>`. You choose the key and enter it in OfinTech Desk. | `Authorization: Bearer odk_…`. OfinTech Desk shows the ingest key once when you create the connection. |

To set it up, go to **Connections → Accounting → Other accounting system**. You need the admin role.

## Data format

Every amount is in the company's base currency, except on documents that state their own currency. Dates use the
form `YYYY-MM-DD`.

**Company**: `GET /v1/company` (pull) or `POST …/company` (push).

```json
{ "name": "Acme Trading LLC", "currency": "SAR", "country": "SA", "fiscal_year_start_month": 1 }
```

**Profit and loss**: `GET /v1/profit-and-loss?start=2026-01-01&end=2026-03-31`.

Return one line per account per calendar month, from posted entries only. `section` must be one of `Income`,
`Cost of sales`, `Expenses`, `Other income` or `Other expenses`.

- Income and costs are positive amounts.
- Use a negative amount only for a reversal.
- Do not send totals: OfinTech Desk adds the net profit line itself.

```json
{ "lines": [
  { "period": "2026-03-01", "section": "Income",   "account": "4000 Sales",   "amount": 53000.00 },
  { "period": "2026-03-01", "section": "Expenses", "account": "6100 Rent",    "amount": 4500.00 }
] }
```

**Balance sheet**: `GET /v1/balance-sheet?as_of=2026-03-31`.

`section` must be one of `Assets`, `Liabilities` or `Equity`. Send amounts with their normal sign: debit balances
for assets, credit balances for liabilities and equity, both as positive numbers. Include the profit for the year
that has not been closed to retained earnings, so that the balance sheet balances.

```json
{ "lines": [
  { "as_of": "2026-03-31", "section": "Assets", "group": "Receivables", "account": "1100 Accounts receivable", "amount": 30500.00 }
] }
```

**Invoices and bills**: `GET /v1/invoices?status=open|all` and `GET /v1/bills?status=open|all`.

- Credit notes have negative `total` and `balance`.
- `balance` is the amount still open.
- Return results in pages. Set `next_cursor` to a value when more pages follow, and to `null` on the last page.
  OfinTech Desk passes the value back as `?cursor=`.

```json
{ "documents": [
  { "number": "INV-0042", "party": "Northwind Retail", "issue_date": "2026-02-10", "due_date": "2026-03-11",
    "currency": "USD", "total": 12000.00, "balance": 12000.00, "status": "Open" }
], "next_cursor": null }
```

OfinTech Desk validates every response. If a response does not match the format, the sync stops with a message
that names the field at fault, for example `lines.3.period: String should match pattern`.

## Pull: build an adapter

[`examples/accounting-adapter/adapter.py`](../examples/accounting-adapter/adapter.py) is a complete reference
adapter. It uses only the Python standard library and serves an in-memory ledger.

1. Replace the four functions under "your system" with queries against your system. Keep the response shapes.
2. Run it behind HTTPS, for example with Caddy or nginx. On a Team Server, OfinTech Desk refuses plain HTTP and
   private or loopback addresses for connectors. The desktop app also accepts local addresses.
3. In OfinTech Desk, add an *Other accounting system* in **Pull** mode with the adapter's address and key. The first
   sync starts immediately.

```bash
ADAPTER_KEY=$(openssl rand -hex 24) python examples/accounting-adapter/adapter.py
curl -H "Authorization: Bearer $ADAPTER_KEY" "http://127.0.0.1:8787/v1/profit-and-loss?start=2026-01-01&end=2026-03-31"
```

Build the adapter so that it:

- exposes read-only data and nothing else;
- uses a database user or API user that can only read;
- answers within 120 seconds, the engine's timeout per request.

## Push: send data from your system

Create an *Other accounting system* connection in **Push** mode and copy the ingest key. The key is shown only once.
Then send JSON to the Team Server:

```bash
curl -X POST "https://desk.example.com/api/ingest/v1/accounting/profit-and-loss" \
  -H "Authorization: Bearer odk_…" -H "Content-Type: application/json" \
  -d '{"lines":[{"period":"2026-03-01","section":"Income","account":"4000 Sales","amount":53000}]}'
# → {"kind":"profit-and-loss","rows":2,"dataset_id":"ds_…"}
```

| Endpoint | Body |
|---|---|
| `POST /api/ingest/v1/accounting/company` | Company |
| `POST /api/ingest/v1/accounting/profit-and-loss` | `{"lines": [PLLine…]}` |
| `POST /api/ingest/v1/accounting/balance-sheet` | `{"lines": [BSLine…]}` |
| `POST /api/ingest/v1/accounting/invoices` | `{"documents": [Document…]}` |
| `POST /api/ingest/v1/accounting/bills` | `{"documents": [Document…]}` |

How the ingest endpoints behave:

- **Full replacement.** Each call replaces the whole data set of that kind for the connection. Send the full
  history you want to analyse, for example 24 months of profit and loss.
- **Responses.**

  | Status | Meaning |
  |---|---|
  | `200` | Success. The body names the dataset that was updated. |
  | `401` | The key is wrong or has been revoked. |
  | `422` | The data does not match the format. `details` lists the first problems. |
  | `413` | The request is over 100 MB. |
  | `429` | More than 60 requests a minute were sent with the key. |
- **Key storage.** Ingest keys are stored as hashes, so they cannot be shown again. **Rotate key** on the
  connection issues a new key and revokes the old one immediately.
- **Audit.** Every push is written to the audit log.
- **Desktop app.** The desktop engine listens only on the computer itself. Use push with the Team Server, or pull
  with the desktop app.

## Odoo

Odoo has its own connector under **Connections → Accounting → Odoo**, so it does not need this API. You need:

- the Odoo address;
- an API key (*Preferences → Account Security → New API Key*), created for a user with read access to Accounting;
- the database name, if the server hosts more than one.

How the connector talks to Odoo:

| Odoo version | API used | What else you need |
|---|---|---|
| 19 and later | The JSON-2 API: `POST /json/2/<model>/<method>`, bearer key, `X-Odoo-Database` header. | Nothing else. |
| 17 and 18 | JSON-RPC (`/jsonrpc`). | The login of the API user. |

Odoo deprecated XML-RPC and JSON-RPC in version 19.0 and has announced that they will be removed, so prefer
version 19 or later.

What the connector reads:

- Figures come from posted journal items, grouped by account, using Odoo's account types. Profit and loss comes
  from `income`, `income_other`, `expense`, `expense_direct_cost` and `expense_depreciation`. The balance sheet
  comes from the asset, liability and equity types.
- Profit or loss that has not yet been closed into equity appears as *Unallocated earnings*, so the balance sheet
  balances.
- With several companies, choose one. The connector filters every query by that company.
