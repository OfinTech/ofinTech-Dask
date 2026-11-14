# 10 · Operations handover

This extends `reports/ai-agent-final-release/10_OPERATIONS_RUNBOOK.md` (alert responses, severities, troubleshooting), which still applies. Only what changed or is new is listed here.

## Runtime deployment

```bash
cd server && cp .env.example .env            # DESK_DOMAIN, ACME_EMAIL, DESK_MASTER_KEY (python -m desk_engine keygen)
docker compose up -d --build
docker compose exec engine python -m desk_engine check
```

New in this branch: resource limits in `server/docker-compose.yml`. Raise them for large ledgers or many users.

| Service | CPU | Memory limit | Memory reservation | pids | Stop grace |
|---|---|---|---|---|---|
| engine | 2 | 2 GB | 512 MB | 512 | 30 s |
| caddy | 0.5 | 256 MB | — | 128 | — |

**Verify the limits after deploying.** They were validated only as configuration here; see 06.

```bash
docker inspect $(docker compose ps -q engine) --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.PidsLimit}}'
docker stats --no-stream
```

## Metrics verification

- Prometheus scrapes with `DESK_METRICS_TOKEN`, set in `server/.env` and `deploy/monitoring/secrets/metrics_token`.
- Quick check from inside the compose network: `curl -s -H "Authorization: Bearer $TOKEN" http://engine:8700/api/admin/metrics | grep -E '^desk_(up|agent_runs_active|db_connections) '`.
- New signal for this release: `desk_event_loop_lag_seconds`. P99 above 0.5 s fires `DeskEventLoopLag`.

## Alert testing

Follow `03_ALERTMANAGER_VALIDATION.md`, "Procedure to close this gate", before relying on alerts. It needs an authorised test receiver URL from the operator.

## Incident response

Unchanged; see the runbook. One addition: if the engine logs `worker threads still busy after 10s; database connections left to the process exit` at shutdown, a query was still running at stop time. The process exits without closing that connection, which is the safe choice (SQLite recovers the WAL on the next start). Raise `stop_grace_period` if it happens often.

## Resource tuning

| Symptom | Metric | Change |
|---|---|---|
| OOM kills (`docker inspect … State.OOMKilled`) | `desk_process_resident_memory_bytes` near the limit | raise `mem_limit`; find the cause first (`DeskMemoryGrowth`) |
| CPU throttling, slow turns, loop lag | `rate(desk_process_cpu_seconds_total)` near `cpus` | raise `cpus` |
| Thread creation errors | `pids_limit` | raise it; the engine normally needs tens of threads (see 06) |

## Performance troubleshooting

- **Slow turns:** start with the LLM dashboard. With a real model, model time dominates.
- **Engine time:** `desk_agent_run_seconds` minus `desk_llm_call_seconds`.
- **Database:** use `desk_db_query_seconds{kind}`. The agent loop's context and history reads now run in worker threads, so a slow database delays the turn but no longer stalls other requests (05).

## E8 execution

Use `02_E8_LIVE_MODEL_RESULTS.md`. Run it in a non-production environment with an evaluation key from the secret manager, and record the model id, the date and the `sha256sum` of the evaluation scripts with the result.

## Rollback

```bash
cd server && git checkout <previous release tag> -- . && docker compose up -d --build engine
```

There is no schema change. D1 and D2 (PRs #71 and #72) showed each version serves the other's data. To remove only the resource limits, revert the `cpus` / `mem_limit` / `mem_reservation` / `pids_limit` / `stop_grace_period` lines.
