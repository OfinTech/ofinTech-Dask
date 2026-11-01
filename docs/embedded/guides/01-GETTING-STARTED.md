# 01 — Getting started

What you need: an OfinTech Desk **Team Server** (server mode, https) where you are an **admin** of the workspace whose
data the agent should answer from, and a web application of your own with a backend.

## 1. Register the integration (in Desk)

As an admin with a fresh sign-in (Desk asks for your password again when needed):

```
POST /api/w/{ws}/embedded/integrations
{"name": "Partner ERP", "allowed_origins": ["https://erp.partner.example"], "capabilities": ["ask_finance"]}
```

The response contains `key` (`oei_…`) **once**. Store it in your backend's secret store. The response also names the
`service_user_id`: a viewer member Desk created for this integration; every question asked through the gateway is
attributed to it in Desk's audit log.

While developing you may add `http://localhost:5173`-style origins; `http` is accepted for localhost only.

## 2. Mint a session token (from your backend)

When one of your signed-in users opens the page that embeds the copilot:

```
POST https://desk.example.com/api/embedded/sessions
Authorization: Bearer oei_…
{"subject": "<the user's id in your system>", "locale": "ar"}
```

→ `{"token": "oes_…", "expires_at": "...", "agents": [...]}`. Tokens last 15 minutes by default (60 to 3600 seconds
allowed). Return the token to the browser; never the integration key.

## 3. Embed the SDK (in your page)

```tsx
import { OfinTechAgentProvider, OfinTechFinancialCopilot } from "@ofintech/embedded-agent";
import "@ofintech/embedded-agent/styles.css";

<OfinTechAgentProvider endpoint="https://desk.example.com/api/embedded" tokenProvider={fetchTokenFromMyBackend} locale="ar">
  <OfinTechFinancialCopilot />
</OfinTechAgentProvider>
```

Ask: "كم بلغ صافي الربح هذا الشهر؟" — the answer streams in, with KPI cards or tables when a tool returned figures.

## 4. What happens behind the scenes

`POST /api/embedded/agents/finance-copilot/run` with an AG-UI `RunAgentInput`; the gateway maps your `threadId` to a
private Desk conversation owned by the service user, runs the workspace's own model with a **read-only** tool set,
and streams AG-UI events back. Every run is audited and metered per integration.

Next: [02 — Authentication](02-AUTHENTICATION.md).
