# 07 — Generative UI components

The model never produces UI. Desk tools return typed blocks; the gateway maps a block to a component by its
**structure**, validates the props against a versioned JSON schema and sends `CUSTOM {name: "ofintech.component",
value: {version: 1, component, props, toolCallId}}`. The SDK validates again before rendering; an unknown component
or a prop that does not fit the schema is never rendered.

| Component | Rendered from | Key props |
|---|---|---|
| `FinancialKpiCard` | `kpis` items | `label, value, format, currency, period?, change?, tone?` |
| `CashFlowChart` | dated table with inflow/outflow/balance columns | `points[{period, inflow?, outflow?, net?, balance?}]` |
| `RevenueExpenseChart` | dated table with revenue and expense columns | `points[{period, revenue?, expenses?, profit?}]` |
| `FinancialDataTable` | any table | `columns[{key,label,format?}], rows[][], truncated?, total_rows?` |
| `BankBalanceSummary` | balance sheet bank/cash lines | `accounts[{name, balance, currency?}], total?, as_of?` |
| `ReconciliationTable` | `query_dataset` on a reconciliation dataset | table props + `matched, unmatched` |
| `UnmatchedTransactions` | `list_invoices` | `items[{reference, party?, date?, amount, days_overdue?, status?}]` |
| `FinancialSourceReference` | `sources` | `items[{n, name, page?, snippet?, source?, document_id?}]` (no links) |
| `ApprovalRequestCard` | an interrupt | `approval_id, tool, summary, expires_at?, decided_in: "desk", approval_url?` |
| `FinancialRiskIndicator` | warn/bad KPI tones; unverified figures | `level, title, detail?, figures[]` |

Schemas: `sdk/embedded-agent/schemas/v1/*.json` (`$id: https://desk.ofintech.ai/schemas/embedded/v1/<Name>.json`).
They are generated from `engine/desk_engine/embedded/components.py` and a test fails if the two drift.

Caps: 500 rows, 40 columns, 120 chart points, 50 sources, strings ≤ 200 characters (snippets 300). Bank identifiers
are masked by the engine before any tool result or component is produced.

Versioning: `version` is an integer on every payload. A breaking change increments it; SDKs render only versions
they know and fall back to the tool's text. Additive changes (new optional props) keep the version.

Arabic: labels inside props come from Desk's tools (the workspace language); the SDK's own strings follow `locale`
and numbers are formatted with `Intl.NumberFormat` (`ar-SA` for Arabic).
