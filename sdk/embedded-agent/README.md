# @ofintech/embedded-agent

React SDK for OfinTech Desk's **Embedded Agent Gateway**: a read-only financial copilot inside your own application,
speaking the [AG-UI](https://docs.ag-ui.com) protocol. Peer dependency: React 19. No other runtime dependency, no
telemetry, one network destination (your Desk server).

```tsx
import { OfinTechAgentProvider, OfinTechFinancialCopilot } from "@ofintech/embedded-agent";
import "@ofintech/embedded-agent/styles.css";

<OfinTechAgentProvider
  endpoint="https://desk.example.com/api/embedded"
  tokenProvider={async () => (await fetch("/api/ofintech/session", { method: "POST" })).json().then((b) => b.token)}
  locale="ar"                     // "ar" renders right-to-left
  agentId="finance-copilot"       // or "financial-intelligence", "reconciliation-review"
>
  <OfinTechFinancialCopilot context={[{ description: "screen", value: "Customer ACME-001, Q3 2026" }]} />
</OfinTechAgentProvider>
```

`tokenProvider` must call **your backend**, which exchanges the integration key (kept server-side) for a 15-minute
session token: `POST {desk}/api/embedded/sessions` with `Authorization: Bearer oei_…` and
`{"subject": "<your user id>", "locale": "ar"}`. See `docs/embedded/guides/` in the Desk repository.

## What you get

* `OfinTechAgentProvider` / `useOfinTechAgent()` — run state (`turns`, `status`, `error`, `interrupts`), `send`,
  `stop`, `reattach`, `reset`.
* `OfinTechFinancialCopilot` — a complete chat panel (ar/en, RTL, keyboard-operable, dark-mode aware).
* `AgUiClient` — the protocol client if you build your own UI or use CopilotKit's `HttpAgent` instead.
* Component registry — ten financial components rendered **only** from validated, versioned payloads:
  `FinancialKpiCard`, `CashFlowChart`, `RevenueExpenseChart`, `FinancialDataTable`, `BankBalanceSummary`,
  `ReconciliationTable`, `UnmatchedTransactions`, `FinancialSourceReference`, `ApprovalRequestCard`,
  `FinancialRiskIndicator`. Schemas ship in `schemas/v1/`.

## What it never does

* Render HTML or run code from the model or the server (text nodes only; the registry refuses unknown props).
* Follow links from data: sources are text unless you pass `onOpenSource`; the only `<a>` is the approval card's
  `https://` Desk link.
* Approve anything: approvals are decided inside OfinTech Desk by an editor. The SDK shows the card and the link.
* Call anything but `endpoint`.

## Build and test

```
npm install
npm run build      # dist/index.js + dist/styles.css (esbuild)
npm test           # registry validation, static scans (no innerHTML, no external calls), SSR render in ar/en
npm run typecheck
```

Licence: Apache-2.0 (same as OfinTech Desk's UI).
