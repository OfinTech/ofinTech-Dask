# 10 — Rate limits, usage and entitlements

Defaults per integration (admins can change them; the input cap can only be lowered):

| Limit | Default | Scope |
|---|---|---|
| `runs_per_minute` | 20 | integration |
| `runs_per_minute_session` | 6 | one session token |
| `runs_per_day` | 2 000 | integration (UTC day) |
| `tokens_per_day` | 2 000 000 | integration (model tokens in + out) |
| `max_input_chars` | 8 000 | one message |
| unauthenticated requests | 60 / min | client address |
| session issuance | 60 / min | integration key |

Exceeding a per-minute limit → `429 rate_limited`; a daily limit → `403 entitlement_exceeded` (audited). Input caps →
`422 message_too_long` / `body_too_large`. One run at a time per conversation → `409 thread_busy`.

## Usage

Every run writes a row to `embedded_usage` (integration, session, run, subject hash, agent, status, tokens, tool calls,
duration). Admins read it at `GET /api/w/{ws}/embedded/integrations/{id}/usage?days=30` (per day, agent and status,
plus today's totals). It is metering, **not billing**: no prices, no invoices.

## Entitlements (interface)

`engine.embedded.entitlements` implements `check_run(principal, agent_id) -> Decision`. The default
(`LimitEntitlements`) enforces the daily limits above. A plan or billing system replaces the object; the gateway calls
only `check_run` before starting a run and refuses with the decision's code when it is not allowed. Nothing in the
repository implements plans or payment collection.
