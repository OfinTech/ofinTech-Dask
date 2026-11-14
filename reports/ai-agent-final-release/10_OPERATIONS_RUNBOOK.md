# 10 · Operations runbook (Team Server)

No contacts, receivers or URLs are filled in here. The operator supplies them.

## Monitoring access

- **Grafana:** `http://127.0.0.1:3000` on the server only. Use an SSH tunnel (`ssh -L 3000:127.0.0.1:3000 <server>`). Sign in as admin (`GRAFANA_ADMIN_PASSWORD`). The dashboards are in the "OfinTech Desk" folder.
- **Prometheus and Alertmanager:** no published port. Use `docker compose exec prometheus wget -qO- localhost:9090/api/v1/alerts` and `docker compose exec alertmanager amtool alert --alertmanager.url=http://localhost:9093`.
- **Raw metrics:** `curl -H "Authorization: Bearer $(cat deploy/monitoring/secrets/metrics_token)" http://engine:8700/api/admin/metrics` from inside the compose network, or signed in as the instance administrator.
- **One conversation's steps:** `GET /api/w/{ws}/threads/{tid}/trace`, signed in as a member of that workspace.

## Incident severity

| Level | Meaning | Examples (alerts) | Response |
|---|---|---|---|
| SEV1 | Service down or data at risk | `DeskServiceDown`, `DeskApiAvailabilityBudgetBurn`, `DeskPaymentOrSealedRefusals` | immediately; inform users |
| SEV2 | Main function degraded | `DeskModelUnavailable`, `DeskApi5xxHigh`, `DeskAgentErrorRate`, `DeskFinancialCalculationFailures` | within 1 hour |
| SEV3 | Degradation with a workaround | latency, timeouts, tool failures, database saturation, memory growth, event-loop lag | next working day |
| SEV4 | Information | reconciliation exception spike, background job failures | review weekly |

Escalate one level when an alert stays firing for twice its expected fix time, or when a financial figure shown to a
user may have been wrong.

## Alert response

### service-down
1. Check the engine container: `docker compose ps engine; docker compose logs --tail 200 engine`.
2. Check readiness: `curl -s localhost:8700/ready` (inside the container). Each check returns a reason code: disk, database, storage.
3. If the database is corrupt or the disk is full: see [database](#database). Otherwise restart with `docker compose restart engine` (it shuts down gracefully on SIGTERM; D2 measured the shutdown time).

### api-errors
Check the System health dashboard, "API requests by status class" and "API P95 by route", to find the route. Then read the engine log around the time (`unhandled error on` lines). If a deploy just happened, [roll back](#rollback).

### agent-errors
On the AI agent overview, open "Run errors by code":

| Code | Meaning | Action |
|---|---|---|
| `llm_*` | model side | [model-outage](#model-outage) |
| `answer_incomplete` | replies cut by the output limit | ask users for shorter answers, or raise the provider's output limit |
| `answer_empty` | the provider returned nothing | provider issue; watch whether it recurs |
| `too_many_steps` | runaway tool loops | collect `/trace` of one run for the engineering team |
| `internal` | a bug | engine log, then escalate |

### model-outage
1. The LLM performance dashboard → "Model errors by class":
   - `llm_auth`: the key was revoked or expired. The workspace admin replaces it under Settings → Models.
   - `llm_not_found`: the model name was retired. The admin chooses another model.
   - `llm_unavailable`, `llm_network`, `llm_timeout`: provider or network trouble. Check the provider's status page.
2. Retries are already applied (`DESK_LLM_RETRIES`, default 2). Do not raise them during an outage; it adds load.
3. Tell users the assistant is unavailable. The rest of the product (ledger, dashboards, close) does not depend on the model.

### tool-failures
`DeskToolFailures` names the tool. For financial tools, check the ledger import (the "Data" page) and the engine log for that tool's exceptions (`tool <name> failed`). Accounting or MCP tools: check the connection's status page.

### unsupported-figures
A model is stating figures no tool returned. Compare models used in the period (usage table). Re-run E8 against the model in use. Warn users to check marked figures.

### reconciliation
Many unmatched bank lines usually mean a statement in another format or currency, or the wrong bank account mapped in the close. Check the close's bank view for the affected month.

### database
SQLite runs in WAL mode in one process.
- **Slow writes:** look for long imports or exports. The hourly WAL checkpoint keeps the log small.
- **Disk full:** free space. `/ready` reports `disk`.
- **Corruption:** stop the engine. Restore the latest backup (below) into a new data directory, start, and check `/ready`.

### memory
`DeskMemoryGrowth`: compare `desk_agent_runs_active` and `desk_db_connections`. Both should be small and flat. If they are flat and memory still grows, capture `docker stats` and the engine log, restart the engine (in-memory metrics reset; Prometheus keeps history), and escalate.

### slow-or-blocked
`DeskEventLoopLag` or latency alerts: a slow database or CPU-bound analysis. Check "Database call P95" and CPU. The agent loop's own reads still run on the event loop (known risk).

### background-jobs
The `kind` label names the job (document processing, file review, workflow, accounting sync, scheduler). The engine log has the exception.

### security-events
- **Payment or sealed refusal:** something (a model following injected instructions, or an MCP server) tried to move money or send data out. The guard held. Find the event in the workspace audit log (`GET /api/w/{ws}/audit`, admin role; action `tool.refused`) and review the conversation and connected MCP servers.
- **Cross-workspace or auth spikes:** check the audit log (`access.denied`) for the user or IP pattern. Lock affected accounts (`POST /api/admin/users/{id}/sign-out`).

## Rollback

There is no schema change in this release, so rollback is a code rollback:

```bash
cd server
docker compose pull engine   # or: git checkout <previous tag> && docker compose build engine
docker compose up -d engine  # same data volume; D1 and D2 showed both versions serve the same data
```

To roll back only the monitoring: `docker compose -f server/docker-compose.yml -f deploy/monitoring/docker-compose.monitoring.yml rm -sf prometheus alertmanager grafana`. The engine is unaffected.

Operational switches (environment, restart required):
- `DESK_LLM_RETRIES=0`: no model retries.
- `DESK_LLM_HISTORY_TOKENS`: history budget.
- Unset `DESK_METRICS_TOKEN`: only the admin session can read metrics.

## Backup and recovery

`server/backup.sh` writes an encrypted, consistent online backup. Check one with
`python -m desk_engine backup-check <file> --key-file <backup.key>`, and restore with
`python -m desk_engine restore <file> --key-file <backup.key>`, into an empty data directory. D2 ran
backup → check → restore → start against a temporary directory (see 08).

## Troubleshooting commands

```bash
docker compose logs --since 30m engine | grep -E "ERROR|failed|unhandled"
docker compose exec engine python -m desk_engine check            # configuration validation
docker compose exec engine python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8700/ready').read())"
curl -s -H "Authorization: Bearer $TOKEN" http://engine:8700/api/admin/metrics | grep -E "^desk_(up|agent_runs_active|db_connections|llm_calls_total)"
```
