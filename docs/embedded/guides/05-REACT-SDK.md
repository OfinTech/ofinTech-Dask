# 05 — React SDK (`@ofintech/embedded-agent`)

Install from the `sdk/embedded-agent` folder of the Desk repository (`npm install <path>` or publish it to your
private registry). Peer dependency: React 19. No other runtime dependency; no telemetry.

```tsx
import { OfinTechAgentProvider, OfinTechFinancialCopilot, useOfinTechAgent } from "@ofintech/embedded-agent";
import "@ofintech/embedded-agent/styles.css";
```

## Provider

| Prop | Meaning |
|---|---|
| `endpoint` | `https://desk.example.com/api/embedded` |
| `tokenProvider` | `() => Promise<string> \| string` returning a session token from **your backend** |
| `agentId` | `finance-copilot` (default), `financial-intelligence`, `reconciliation-review` |
| `locale` | `"en"` or `"ar"` (RTL) |
| `threadId` | stable id per conversation; defaults to one per mounted provider |

## The ready-made panel

`<OfinTechFinancialCopilot title? context? onOpenSource? showToolCalls? className? />`

* `context` — screen facts the agent may use (treated as untrusted page content).
* `onOpenSource` — when set, `FinancialSourceReference` items become buttons that call it with
  `{document_id, page, name}`; when unset they are plain text. The SDK never opens URLs on its own.
* Styling: CSS custom properties on `.ofe-copilot` (`--ofe-accent`, `--ofe-bg`, `--ofe-font`, …); dark mode via
  `prefers-color-scheme`.

## Your own UI

```tsx
const { turns, status, error, send, stop, reattach, reset, interrupts } = useOfinTechAgent();
```

`turns` is a list of `{kind: "user", text}` and `{kind: "assistant", text, toolCalls, components, notices,
unverifiedFigures, done}`. Render `components` with `<RenderComponent payload={c} locale="ar" />` — the registry has
already validated them. `status` is `idle | running | waiting_approval | error`.

Protocol-level access: `new AgUiClient({endpoint, tokenProvider})` with `run()`, `attach()`, `cancel()`, `agents()`.

## Security properties you inherit

* The only `<a>` element is the approval card's `https://` Desk link (`rel="noopener noreferrer"`).
* No `dangerouslySetInnerHTML`, no `eval`, no HTML from data — enforced by `test/no_dangerous_html.test.mjs`.
* One network destination — enforced by `test/no_external_calls.test.mjs`.
* Tokens live in memory; nothing is written to `localStorage`/`sessionStorage`.
