# 11 — Errors

Before the run starts (plain JSON, `{"error": code, "message": text}`):

| Status | Code | Meaning / what to do |
|---|---|---|
| 401 | `unauthorized` | token unknown, expired or revoked (indistinguishable) → mint a new session |
| 403 | `origin_required` / `origin_not_allowed` | add the page's origin to the integration in Desk |
| 403 | `capability_missing` | the integration lacks the agent's capability |
| 403 | `integration_suspended` / `integration_unbound` / `egress_blocked` | ask the organisation's Desk admin |
| 403 | `entitlement_exceeded` | daily limit reached; try tomorrow or ask for a higher limit |
| 404 | `agent_not_found` / `not_found` | wrong agent id, or a run id that is not yours |
| 409 | `thread_busy` | the previous turn is still running; wait for its `RUN_FINISHED` or cancel it |
| 422 | `invalid`, `empty_message`, `message_too_long`, `unsupported_content`, `body_too_large` | fix the request |
| 429 | `rate_limited` | back off; do not retry in a loop |

After the run starts (`RUN_ERROR {message, code, metadata?}`):

| Code | Meaning |
|---|---|
| `run_failed` | the engine's own error message (model unavailable, too many steps, …) |
| `run_timeout` | 15 minutes elapsed or 10 minutes without an event; the run was stopped |
| `stream_dropped` | your client read too slowly; re-attach |
| `resume_required` / `interrupt_unknown` / `interrupt_expired` / `resume_invalid` | see 08 — Approvals |
| `approval_requires_desk_editor` | decisions are made in Desk; `metadata.ofintech.approval_url` links there |

The engine never sends stack traces to the client; `x-request-id` on every response correlates with Desk's audit log.
