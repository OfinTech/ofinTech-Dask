# 03 · Alertmanager validation

## Result: BLOCKED. No alert has been evaluated, routed or delivered.

| Requirement | Status | Reason |
|---|---|---|
| Monitoring runtime | **unavailable** | no Docker daemon (`/var/run/docker.sock` absent); no `prometheus`, `alertmanager`, `promtool` or `amtool` binaries; package installs are disabled |
| Upstream binaries | **not obtained** | The official Prometheus 3.4.1 archive was downloaded from its GitHub release. The next step (checksum check, extraction, running the binaries) was refused by the environment's permission system. The archive was deleted, and the refusal was not worked around. |
| Authorised test receiver | **not configured** | `deploy/monitoring/secrets/` holds only its README; no `alert_webhook_url` was provided, and none was invented |

Static validation from PR #72 (configuration parses, every referenced metric exists, signals move under injected
failures) still holds. It is **not** a substitute for delivery and is not counted as such.

## What was re-checked here

| Check | Result |
|---|---|
| Prometheus scrapes `/api/admin/metrics` with `DESK_METRICS_TOKEN` (`prometheus.yml`, `credentials_file`) | configured |
| The token opens only that route | `ScrapeAccess` tests pass; D2 (PR #72): 200 with the token, 401 without or wrong |
| Merged compose (server + monitoring overlay) | `docker compose config` exit 0 (06) |
| Rules and dashboards reference only exported metrics | `test_observability` passes |

## Procedure to close this gate (staging, about 30 minutes)

The steps below have not been run here.

1. On a host with Docker, prepare the secrets:
   ```bash
   openssl rand -hex 32 > deploy/monitoring/secrets/metrics_token
   echo "DESK_METRICS_TOKEN=$(cat deploy/monitoring/secrets/metrics_token)" >> server/.env
   printf '%s' 'https://<authorised test receiver>' > deploy/monitoring/secrets/alert_webhook_url
   ```
2. Start the stack and check the configuration:
   ```bash
   docker compose -f server/docker-compose.yml -f deploy/monitoring/docker-compose.monitoring.yml up -d
   docker compose exec prometheus promtool check rules /etc/prometheus/alerts.yml /etc/prometheus/slo.yml
   docker compose exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
   ```
3. Check that the target is up: `docker compose exec prometheus wget -qO- localhost:9090/api/v1/targets` should show `health: up`.
4. **Service unavailable:** `docker compose stop engine`. After about 2 minutes `DeskServiceDown` should fire. The receiver should get one `firing` payload, and inhibition should suppress everything else. `docker compose start engine` should bring a `resolved` payload.
5. **Model unavailable or agent errors:** point a workspace's provider at an unreachable URL and send 6 chat messages. `DeskModelUnavailable` should fire, and `DeskAgentErrorRate` should be inhibited. Restore the provider and confirm `resolved`.
6. **Latency:** temporarily lower the `DeskAgentLatencyHigh` threshold in a copy of `alerts.yml` and reload. Confirm it fires; then revert.
7. **Burn rate:** keep step 5 running for 10 minutes and confirm `DeskAgentSuccessBudgetBurn`.
8. Save every receiver payload. Confirm each contains only alert names, severities, route templates and codes: no workspace, user, prompt or amount.
9. Restart Alertmanager while an alert is firing and confirm it is not re-sent as new (deduplication; the data volume persists the notification log).

The gate becomes PASS only with these payloads attached.
