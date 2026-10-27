# Workflows

A workflow is a trigger plus ordered steps. Runs are stored with their step index, context, log and timings, so they
pause at approvals and resume after a decision or an engine restart. New workflows and templates are saved as
drafts (switched off); a draft can be saved incomplete, but it must be complete before it is switched on or run.

## Triggers

| Type | Fields | Notes |
|---|---|---|
| `manual` | — | **Run now**, or the assistant's `run_workflow` tool (with approval) |
| `schedule` | `cron`, `timezone` | Five-field cron, evaluated in the time zone (default: the workspace's). The editor previews the next five runs. |
| `email` | `query`, `connection_id` | Gmail search, polled about every 5 minutes; one run per new message (`{{trigger.message_id}}`, `{{trigger.subject}}`) |
| `channel_command` | `keyword` | A linked Telegram/WhatsApp member sends `/keyword`; the result is sent back to the chat |
| `webhook` | — | Another system (an ERP, a payment provider, a form) posts an event; see below |

### Webhooks

Save a workflow with the *From another system (webhook)* trigger, then **Create secret**. The app shows the address
and the secret once:

```http
POST https://desk.example.com/api/hooks/workflows/<workflow id>
Authorization: Bearer dwh_…
Content-Type: application/json

{"invoice": "INV-77", "supplier": "Gulf Freight LLC", "amount": 18400}
```

- The response is `202 {"run_id": "wr_…"}`. In steps, use `{{trigger.data.invoice}}` for one field or
  `{{trigger.text}}` for the whole body (a body that is not JSON arrives as text).
- Only a hash of the secret is stored. **Create a new secret** replaces it at once; **Remove** stops all events.
- Limits: 256 KB per event and 30 events a minute per workflow. Events are refused (`409`) while the workflow is
  switched off, and with `401` for an unknown workflow or a wrong secret (the two cannot be told apart).
- On the desktop app the address is only reachable from the same computer; use the Team Server for events from
  other systems.

## Steps

| Type | Does | Output |
|---|---|---|
| `agent` | Runs the assistant with a prompt, optional pinned skills and model; creates a conversation you can open | `{{steps.<id>.output}}` (final answer) |
| `tool` | Calls one tool with templated arguments; tools that write wait for approval | `{{steps.<id>.output}}`, `{{steps.<id>.data...}}` |
| `file` | **Refresh** an evidence-linked workbook or report with the latest data, or **apply edits** to one of your own Excel/Word files (the same edits the assistant makes: `set_cell`, `append_row`, `fill`, `set_table_cell`, …). Saves a new version. | `{{steps.<id>.output}}`, `{{steps.<id>.data.link}}`, `.version`, `.unverified` |
| `approval` | Waits for an editor/admin/owner; rejecting ends the run | — |
| `condition` | Compares a value (`gt, gte, lt, lte, eq, ne, contains, empty, not_empty`); stops or skips the next step when false | `true/false` |
| `notify` | Notifies workspace members (me, admins, editors, everyone) in the app, by e-mail (via connected Gmail), Telegram or WhatsApp | — |

Notifications reach **workspace members only**. Messages to outside parties go through tool steps, which need
approval.

### When a step fails

Tool, file and notify steps can **try again** up to five times. The first wait is set per step (10 seconds to 15
minutes) and doubles after each try. After the last try the step either **stops the run** (the default; the
workflow's owner is notified) or **continues with the next step**, in which case `{{steps.<id>.error}}` holds the
reason. Assistant steps can also continue after an error.

### Run timeline

Each run shows one line per step: done, failed, running, waiting, skipped or not run; how long it took; how many
tries it needed; its output; and links to the assistant's conversation or the file version it produced. The full log
stays available underneath.

## Templating

`{{trigger.text}}`, `{{trigger.data.<field>}}`, `{{steps.<id>.output}}`, `{{steps.<id>.data.<field>}}`,
`{{today}}`, `{{workspace.name}}`, `{{workspace.currency}}`. These are plain lookups: there are no expressions or
code. A value that is exactly one variable keeps its type, for example a number in tool arguments.

## Templates

The Workflows page offers ready-made routines, shown in each person's language and saved switched off:

| Template | Trigger | Steps |
|---|---|---|
| Month-end pack | 2nd of the month, 07:00 | build the actual-vs-budget workbook → controller approval → tell the team |
| Weekly receivables follow-up | Mondays 07:30 | overdue invoices by age and reminder drafts (not sent) → anything overdue? → post the summary |
| Supplier bills due this week | Thursdays 08:00 | bills due in seven days and cash needed → approve the payment list → confirm |
| Bank statements from e-mail | an e-mail from the bank with a statement | save and read the statement → share |
| Event from another system | webhook | check the event → needs a decision? → approval |
| Refresh a workbook every morning | working days 06:00 | refresh the chosen workbook (two retries) → tell me |

The API: `GET /api/w/{ws}/workflow-templates?lang=ar` and `POST /api/w/{ws}/workflows/from-template`
(`{"key": "month_end_pack"}`).

## Running

Schedules and e-mail triggers run while the engine runs. On the desktop that means while the app is open; the Team
Server runs around the clock.
