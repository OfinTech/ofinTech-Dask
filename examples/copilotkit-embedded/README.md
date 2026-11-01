# CopilotKit + OfinTech Desk embedded agent — example

Two ways to put the read-only financial agent in a partner page, side by side:

1. `<OfinTechFinancialCopilot/>` from `@ofintech/embedded-agent` (no CopilotKit).
2. CopilotKit's `<CopilotChat/>` driven by an AG-UI `HttpAgent` pointed at the gateway.

Both use the **same** endpoint, the **same** session token and the **same** component registry. The partner backend
(`server/index.mjs`) is the only place that holds the integration key.

```
export DESK_URL=https://desk.example.com
export OFINTECH_INTEGRATION_KEY=oei_…           # from Desk → workspace settings → Embedded integrations
npm install && npm start                        # http://localhost:5173 ; set a cookie demo_user=<your user id>
```

Register `http://localhost:5173` as an allowed origin of the integration while developing (http is accepted for
localhost only).

Notes

* CopilotKit's hosted "Copilot Cloud" is **not** used; `showDevConsole` is off; the page's CSP `connect-src` allows
  only itself and the Desk host. Check your CopilotKit version's telemetry setting and keep it off.
* Approvals cannot be decided from this page: if an agent ever pauses, the card links to OfinTech Desk.
* This example is not part of the engine's test suite; it depends on the CopilotKit and `@ag-ui/client` packages
  named in `package.json`, whose APIs change between releases. Treat `src/main.tsx` as a starting point.
