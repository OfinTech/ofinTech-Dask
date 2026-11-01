# AG-UI protocol mapping

The gateway speaks AG-UI (https://docs.ag-ui.com) over Server-Sent Events. Event type strings below were checked
against the AG-UI documentation (`EventType` enum of `@ag-ui/core`, the Events concept page and the Interrupts
concept page) on 2026-11-01. No event name is invented; everything OfinTech-specific travels inside the standard
`CUSTOM` event or inside `metadata` objects the specification reserves for that purpose.

## 1. Transport

* `POST /api/embedded/agents/{agent_id}/run` with `Content-Type: application/json` and `Accept: text/event-stream`.
* Response `200 text/event-stream`; each event is one `data: <json>\n\n` frame; a comment frame `: keep-alive`
  every 15 seconds while the run is idle; the stream closes after `RUN_FINISHED` or `RUN_ERROR`.
* Errors detected before the run starts are plain JSON (`401`, `403`, `404`, `409`, `422`, `429`) with
  `{"error": code, "message": text}` — the same error envelope as the rest of the Desk API.
* Re-attach: `GET /api/embedded/agents/{agent_id}/runs/{run_id}/events` replays the run's events from the
  engine's channel buffer (the same mechanism the Desk UI uses) and continues live. Useful after a network drop.
* Cancel: `POST /api/embedded/agents/{agent_id}/runs/{run_id}/cancel`, or simply close the stream — a client
  disconnect cancels the run.

## 2. Input: `RunAgentInput`

| AG-UI field | Gateway behaviour |
|---|---|
| `threadId` | Required. Opaque to the gateway; mapped with the session's subject to a Desk thread (`embedded_threads`). Echoed back in `RUN_STARTED`. **Never** a Desk thread id and never used for authorization. |
| `runId` | Required. Echoed back in `RUN_STARTED`/`RUN_FINISHED`. The engine's own run id is reported in `RUN_STARTED.metadata.ofintech.desk_run_id` and is the id used by the re-attach and cancel endpoints. |
| `messages` | The **last** message with `role == "user"` is the new turn; `content` may be a string or an array of `{type: "text", text}` parts (other part types are rejected with 422). Earlier messages are ignored: the Desk thread holds the authoritative history. Max 64 messages, 8 000 characters of new text. |
| `state` | Accepted, not interpreted. Returned unchanged in the first `STATE_SNAPSHOT` so CopilotKit's shared state stays coherent. |
| `tools` | Accepted and ignored in this release (the model never sees client tools). Audited once per run as `embedded.input.ignored_fields` when non-empty. |
| `context` | Items are appended to the turn as plain-text "Context from the host application" lines, capped at 2 000 characters total, after `audit.sanitize()`-style cleaning. Treated as **untrusted text**, same as a pasted document. |
| `forwardedProps` | Only `locale` (`"ar"`/`"en"`) is read. Any `tenant`, `workspace`, `workspace_id`, `user`, `user_id`, `role`, `scopes` key is ignored and audited. |
| `resume` | See §5. |
| `parentRunId` | Ignored. |

## 3. Output: event sequence of a normal run

```
RUN_STARTED        { threadId, runId, metadata: { ofintech: { version: 1, desk_run_id, agent_id } } }
STATE_SNAPSHOT     { snapshot: <client state or {}> }
STEP_STARTED       { stepName: "model" }            # once per model call (the engine's step counter)
TEXT_MESSAGE_START { messageId, role: "assistant" }  # from Desk `assistant_start`
TEXT_MESSAGE_CONTENT { messageId, delta }            # from Desk `delta` (non-empty deltas only)
TEXT_MESSAGE_END   { messageId }                     # when the Desk `message` for that id arrives
TOOL_CALL_START    { toolCallId, toolCallName, parentMessageId }   # from Desk `tool_start`
TOOL_CALL_ARGS     { toolCallId, delta: <full JSON arguments> }    # one frame; the engine does not stream args
TOOL_CALL_END      { toolCallId }
TOOL_CALL_RESULT   { messageId, toolCallId, content, role: "tool" }   # from the Desk `message` with role "tool"
CUSTOM             { name: "ofintech.component", value: { version: 1, component, props, toolCallId } }
                                                     # one per validated block of a tool result (§6)
CUSTOM             { name: "ofintech.unverified_figures", value: { version: 1, messageId, figures: [...] } }
                                                     # when the engine's figure check flags numbers without a source
STEP_FINISHED      { stepName: "model" }
RUN_FINISHED       { threadId, runId, outcome: { type: "success" }, result: { text }, usage? }
```

Desk event → AG-UI event table:

| Desk event (channel = thread id) | AG-UI |
|---|---|
| `assistant_start {message_id, model}` | `STEP_STARTED {stepName:"model"}` then `TEXT_MESSAGE_START` |
| `delta {message_id, text}` | `TEXT_MESSAGE_CONTENT` (skipped when `text` is empty — the spec requires a non-empty delta) |
| `message` with `role == "assistant"` | `TEXT_MESSAGE_END`, then `CUSTOM ofintech.unverified_figures` if the payload has any, then `STEP_FINISHED` |
| `message` with `role == "user"` | dropped (the client already has it) |
| `tool_start {call}` | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END` |
| `message` with `role == "tool"` | `TOOL_CALL_RESULT` with the tool's text (bank identifiers already masked by the engine), then one `CUSTOM ofintech.component` per block that validates against the registry |
| `message` with `role == "event"` (engine notices, errors) | `CUSTOM {name: "ofintech.notice", value: {version:1, kind, text}}` |
| `approval` | see §4 |
| `run {status:"done"}` | `RUN_FINISHED outcome success` |
| `run {status:"waiting_approval"}` | `RUN_FINISHED outcome interrupt` (§4) |
| `run {status:"error"}` | `RUN_ERROR {message, code}` — the engine's message, never a stack trace |

Events for message ids the adapter has not seen a start for (replay gaps) are emitted with a synthesized
`TEXT_MESSAGE_START` first, so the sequence stays valid for strict clients.

## 4. Approvals → AG-UI interrupts (standard mechanism, no custom event name)

When the engine pauses a run for an approval (`agent_runs.status = waiting_approval`), the adapter ends the run
with the specification's interrupt outcome:

```json
{"type": "RUN_FINISHED", "threadId": "...", "runId": "...",
 "outcome": {"type": "interrupt", "interrupts": [
   {"id": "<approval id>", "reason": "tool_call", "toolCallId": "<call id>",
    "message": "<approval summary from the engine>",
    "expiresAt": "<approvals.expires_at as ISO-8601>",
    "responseSchema": {"type": "object", "properties": {"approved": {"type": "boolean"}, "note": {"type": "string", "maxLength": 500}}, "required": ["approved"]},
    "metadata": {"ofintech": {"version": 1, "tool": "<tool name>", "arguments": {...}, "decided_in": "desk",
                              "approval_url": "<desk url>/w/<ws>/approvals/<id>"}}}
 ]}}
```

`reason` uses the core value `tool_call` (the interrupt is tied to a prior tool call). Everything app-specific sits
under `metadata.ofintech` with its own `version`, as the specification suggests for framework data.

**In this release no embedded agent offers a write tool, so this outcome is never produced in practice.** The
mapping exists and is covered by a protocol test that forces a `waiting_approval` state, because a later
write-capable agent must not need a protocol change.

## 5. `resume` → approvals engine

A new run on a thread with an open interrupt must carry `resume`. Each item `{interruptId, status, payload}` is
handled by the **existing** approvals engine — the gateway adds no second decision path:

| Resume item | Gateway action |
|---|---|
| `interruptId` unknown, or not an approval of this thread | `RUN_ERROR code=interrupt_unknown` (spec: unmatched id is an error) |
| approval already decided/expired | `RUN_ERROR code=interrupt_expired` |
| `status: "cancelled"` | The gateway stops the waiting run the way `POST /threads/{tid}/stop` does (the run is the service user's own): pending approvals become `expired`, the run ends. The new turn then proceeds normally and the stream starts with `RUN_STARTED`. |
| `status: "resolved"` (`payload.approved` true **or** false) | `Agent.decide(...)` as the service user. Deciding an approval — approving or rejecting — needs the **editor** role in the engine (`approvals.decide`), and the service user is a **viewer**, so the engine refuses and audits `approval.denied`; the adapter emits `RUN_ERROR code=approval_requires_desk_editor` with the Desk approval URL in `metadata`. Deciding from the partner application is **not** possible by construction; a Desk editor decides in Desk and the embedded client re-attaches (`GET …/runs/{run_id}/events`) or starts a new turn. |
| `resume` missing while interrupts are open | `RUN_ERROR code=resume_required` |
| payload fails `responseSchema` | `RUN_ERROR code=resume_invalid` |

## 6. Generative UI: `CUSTOM ofintech.component`

```json
{"type": "CUSTOM", "name": "ofintech.component",
 "value": {"version": 1, "toolCallId": "...", "component": "FinancialDataTable", "props": {...}}}
```

| Desk block | Component (registry `components.py`, schemas `sdk/embedded-agent/src/registry/schemas/*.json`) |
|---|---|
| `kpis` | `FinancialKpiCard` (one event per KPI item) |
| `table` from `financial_kpis`/`kpi_detail`/`cost_centre_*`/`query_dataset` | `FinancialDataTable` |
| `table` whose columns are a dated revenue/expense pair | `RevenueExpenseChart` |
| `table` whose columns are dated cash movements | `CashFlowChart` |
| `table` from `list_invoices` with status columns | `UnmatchedTransactions` (overdue/unpaid rows) |
| `table` from `accounting_report` of bank/cash accounts | `BankBalanceSummary` (balances only; identifiers masked by the engine) |
| `table` from `query_dataset` on a reconciliation dataset | `ReconciliationTable` |
| `sources` | `FinancialSourceReference` |
| `kpis` item with `tone: "bad"`/`"warn"` or the engine's unverified-figure notice | `FinancialRiskIndicator` |
| `approval` (interrupt metadata) | `ApprovalRequestCard` (read-only card with the Desk link) |

Mapping is by **structure** (column keys/formats the Desk tools already emit), validated with the component's JSON
schema; a block that does not validate against any component is sent only as `TOOL_CALL_RESULT` text. The SDK
performs the same validation client-side before rendering (`ajv`-free, hand-written validators matching the
schemas) and renders unknown component names as a plain text card.

## 7. Versioning

* `metadata.ofintech.version` and `value.version` are integers, currently `1`. A breaking change to a payload
  increments it; the SDK renders only versions it knows.
* Component schemas carry `"$id": "https://desk.ofintech.ai/schemas/embedded/v1/<Component>.json"` and a `version`
  property; the registry refuses props without the version it expects.

## 8. CopilotKit compatibility

CopilotKit's `HttpAgent` (`@ag-ui/client`) sends `RunAgentInput` and consumes the SSE event stream above. The
example in `examples/copilotkit-embedded/` wires `HttpAgent` with the session token in the `Authorization` header
through `tokenProvider`. CopilotKit generative-UI hooks subscribe to `CUSTOM` events by `name`; the SDK's
`<OfinTechFinancialCopilot/>` does the same without requiring CopilotKit.
