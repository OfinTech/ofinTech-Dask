# Webhooks

Webhooks live under **Connections → Webhooks** and link a workspace to anything with HTTPS, in both directions:

- an **incoming** webhook gives another system an address to call, with a secret, and can start a workflow with
  what it sent;
- an **outgoing** webhook posts signed JSON to an address of yours when things happen in the workspace.

Administrators create, change and delete webhooks; everyone with access to the workspace can read the delivery log.

## Incoming

**Create** → *Add incoming webhook*, give it a name (who will call it) and, optionally, a workflow to start. The app
shows the endpoint and the secret once:

```
Endpoint  https://desk.example.com/api/hooks/in/wh_…
Secret    whs_…
```

The sender posts JSON to the endpoint and proves itself in one of two ways:

```http
POST /api/hooks/in/wh_6b794ad8546c49e692b5
Authorization: Bearer whs_…
Content-Type: application/json
X-OfinTech-Event: invoice.paid

{"invoice": "INV-77", "amount": 18400}
```

or, when the sender cannot set an `Authorization` header, with an HMAC-SHA256 signature of the body using the secret
as the key: `X-OfinTech-Signature-256: sha256=<hex>` (`X-Hub-Signature-256`, GitHub's name for the same thing, is
accepted too). The optional `X-OfinTech-Event` header names the event; without it, the JSON's `event` or `type`
field is used.

Responses:

| Status | Meaning |
|---|---|
| `202` | recorded: `{"delivery_id": "wd_…", "status": "received" \| "workflow_started", "run_id": "wr_…" \| null}` |
| `401` | unknown webhook or wrong secret (the two cannot be told apart); the attempt is logged as *rejected* |
| `409` | the webhook is switched off |
| `413` | more than 256 KB |
| `429` | more than 60 deliveries a minute for this webhook |

**With a workflow.** The delivery becomes the run's trigger: `{{trigger.data.invoice}}` for one field,
`{{trigger.text}}` for the whole body, `{{trigger.event}}` for the event name, `{{trigger.source}}` for the webhook's
name. The workflow's own approval steps still apply, and the data is treated like any untrusted document. If the
linked workflow is missing or switched off, the delivery is recorded with a note and no run starts.

**Without a workflow**, deliveries are only logged, which is enough to keep an eye on a feed or to confirm that the
other system can reach the server before linking a workflow.

Incoming webhooks need a public HTTPS address, so they are a Team Server feature; on the desktop app the endpoint is
reachable only from the same computer. (Workflows also keep their own, older per-workflow webhook, see
WORKFLOWS.md; the Connections webhooks add the delivery log, HMAC signatures and the outgoing direction.)

## Outgoing

**Create** → *Add outgoing webhook*, with a name, the HTTPS address to post to and the event groups to send, or
*everything that is audited*. The secret is shown once.

Every delivery is a `POST` with:

```http
Content-Type: application/json
User-Agent: OfinTech-Desk/1.0.0
X-OfinTech-Event: approval.approved
X-OfinTech-Delivery: wd_…
X-OfinTech-Signature-256: sha256=<HMAC-SHA256 of the body with the secret, hex>

{"id": "wd_…", "event": "approval.approved", "occurred_at": 1761600000,
 "workspace": {"id": "ws_…", "name": "Acme"}, "user": {"id": "u_…", "name": "Olivia Grant"},
 "target": "ap_…", "detail": {"tool": "send_email"}}
```

Verify the signature before trusting the payload, and answer with any `2xx` status within 15 seconds. Anything else
counts as a failure: the delivery is retried after 1, 10 and 60 minutes, then marked *failed*. The log keeps the
HTTP status, the first 1,000 characters of the response and the number of attempts; *Send again* queues a delivery
once more. *Send a test* posts a `test.ping` event so the receiver can be checked before real events flow.

### Event groups

Events are the workspace's audit actions, grouped by their first word:

| Group | Events |
|---|---|
| `workflow` | runs started, finished or failed (`workflow.run.done`, `workflow.run.failed`, …); workflows changed |
| `approval` | approvals decided (`approval.approved`, `approval.rejected`) |
| `file` | files created, revised, reviewed, edited, uploaded or deleted |
| `forecast` | forecasts created, submitted, approved, archived or made the plan |
| `cost_centre` | cost centres and their budgets |
| `close` | month-end close prepared, entries and items decided, month closed or reopened |
| `kpi` | KPI targets and formulas changed |
| `memory` | memory proposed, saved, edited or deleted |
| `dataset` | datasets imported or deleted |
| `connection` | connections and syncs |
| `member` | members added, roles changed, removed |
| `document` | documents added or deleted |
| `test` | test events sent from the Connections page |

Payloads never carry credentials: keys named `secret`, `token`, `password`, `api_key` and the like are dropped from
the detail, and events about webhooks themselves are not sent. A payload is at most 64 KB.

## Security

- Secrets are random (30 bytes), shown once, stored encrypted with the workspace's key, and compared in constant
  time; *New secret* replaces one at once and the old one stops working.
- Outgoing addresses must be HTTPS and, on the Team Server, must not resolve to private, loopback or link-local
  networks (the same rule as for MCP servers and custom model endpoints). Redirects are not followed.
- Incoming: 256 KB per delivery, 60 deliveries a minute per webhook, refused while switched off; the last 500
  deliveries are kept per webhook.
- Creating, changing, deleting, rotating and testing a webhook, and re-sending a delivery, are audited.

## For the assistant

The read-only tool `webhook_events` lists recent incoming deliveries (event, status, a short excerpt of the data and
the run it started), so the assistant can answer "what did the bank portal send this morning?" without anyone
opening the log.
