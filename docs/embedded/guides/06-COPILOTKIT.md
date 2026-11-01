# 06 — CopilotKit integration

CopilotKit speaks AG-UI through `HttpAgent` from `@ag-ui/client`, so the gateway can drive a `<CopilotChat/>` with no
adapter code. `examples/copilotkit-embedded/` shows it next to the SDK's own panel.

```tsx
import { HttpAgent } from "@ag-ui/client";
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";

const agent = new HttpAgent({ url: "https://desk.example.com/api/embedded/agents/finance-copilot/run",
                              headers: { authorization: `Bearer ${sessionToken}` } });

<CopilotKit agent="finance-copilot" agents={{ "finance-copilot": agent }} showDevConsole={false}>
  <CopilotChat />
</CopilotKit>
```

Generative UI: components arrive as `CUSTOM` events named `ofintech.component`. Validate with `acceptComponent()`
and render with `<RenderComponent/>` from the SDK — do **not** render raw `value.props`; the SDK's registry is what
guarantees that only known, well-formed components reach the DOM. The exact CopilotKit hook for custom events
differs between CopilotKit releases; the example uses `useCopilotAction` with `available: "frontend"` as a
placeholder and points to CopilotKit's documentation.

Things to keep off or check in CopilotKit:

* Copilot Cloud / hosted runtime — not needed; the gateway is the runtime.
* Telemetry — disable per your CopilotKit version's setting; the SDK itself has none.
* Frontend actions that call your own APIs — fine, but remember the agent's text can be influenced by page content;
  do not wire a frontend action that moves money or changes records to the agent's suggestions without your own
  confirmation step.
* Token refresh — `HttpAgent` takes static headers; refresh them before each run (see the example).

Approvals: CopilotKit's human-in-the-loop UI maps to AG-UI interrupts; the gateway's interrupts can only be decided
inside Desk, so render the `ApprovalRequestCard` and link out. See [08](08-APPROVALS.md).
