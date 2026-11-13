# 02 · Observability architecture

## What existed before this branch

- PR #71 added a per-engine Prometheus registry (`desk_engine/metrics.py`) with model, tool, run and event-loop metrics.
- It also added the admin-only `GET /api/admin/metrics` and the per-run trace `GET /api/w/{ws}/threads/{tid}/trace`.
- Logs go to stdout; Docker keeps them with its `json-file` driver (20 MB × 5). Health is `/health`; readiness is `/ready` (no authentication, status codes only).

No Prometheus client library, OpenTelemetry SDK or monitoring stack was in the repository. Of the OpenTelemetry
packages, only `opentelemetry-api` is present here (a transitive package, not an engine dependency).

## What this branch adds

```
 engine (one process)                       deploy/monitoring (compose overlay, same network)
 ┌──────────────────────────────┐   scrape  ┌────────────┐  alerts  ┌──────────────┐  webhook  ┌──────────────┐
 │ desk_engine/metrics.py        │◀──────────│ Prometheus │─────────▶│ Alertmanager │──────────▶│ your receiver│
 │  counters, histograms, gauges │  15 s,    │ 30 d       │          │ group/inhibit│ url_file  │ (not set up) │
 │ GET /api/admin/metrics        │  bearer   │ rules:     │          └──────────────┘           └──────────────┘
 │  (admin session or            │  token    │ alerts.yml │
 │   DESK_METRICS_TOKEN)         │           │ slo.yml    │◀──── Grafana (127.0.0.1:3000, sign-in required,
 │ GET …/threads/{tid}/trace     │           └────────────┘       5 provisioned read-only dashboards)
 │  (workspace member only)      │
 └──────────────────────────────┘
```

- **One metrics path.** The engine's own registry, extended. Prometheus text 0.0.4. No second client library or framework was added.
- **Traces.** The in-product run trace (model and tool steps with timing) stays the trace source. OpenTelemetry and Tempo were **not introduced**: the SDK and exporter are not installable here, and adding them unrun would be unverified code. The trace data is already structured, so exporting it to OTel is a follow-up (09, risk 6).
- **Logs.** Loki was **not introduced**: the existing stdout logs work with any collector, and nothing here needed a second log store.

## Metrics (all exported; tested in `engine/tests/test_observability.py`)

| Layer | Metric | Type | Labels (closed vocabularies) |
|---|---|---|---|
| Agent | `desk_agent_runs_started_total` | counter | — |
| | `desk_agent_runs_total` | counter | outcome: completed, failed, incomplete, timed_out, cancelled |
| | `desk_agent_run_seconds` | histogram | outcome |
| | `desk_agent_run_errors_total` | counter | code (`answer_empty`, `answer_incomplete`, `llm_*`, `too_many_steps`, …) |
| | `desk_agent_runs_active` | gauge (concurrent runs) | — |
| | `desk_tool_calls_total` / `desk_tool_seconds` | counter / histogram | tool (built-in name; every MCP tool is `mcp`), status: ok, error, refused, reused |
| LLM | `desk_llm_calls_total` | counter | outcome: ok or error class after retries (`llm_timeout`, `llm_unavailable`, `llm_network`, `llm_auth`, …) |
| | `desk_llm_retries_total` | counter | code |
| | `desk_llm_call_seconds`, `desk_llm_ttfb_seconds` | histogram | — |
| | `desk_llm_tokens_total` | counter | kind: input, output, cache_read, cache_write |
| | `desk_llm_finish_total` | counter | reason: stop, tool_calls, length, content_filter |
| | `desk_llm_request_chars`, `desk_llm_history_budget_ratio` | histogram | — (context-window use) |
| Financial | `desk_figures_total` | counter | provenance: tool, derived, memory, user, unsupported |
| | `desk_answers_total` | counter | figures: verified, unsupported, no_figures |
| | `desk_reconciliation_lines_total`, `desk_reconciliations_total` | counter | result |
| | calculation and data-source failures | from `desk_tool_calls_total{status="error"}` for `calc_*`, KPI, accounting and MCP tools | |
| Backend | `desk_http_requests_total` / `desk_http_request_seconds` | counter / histogram | route **template** (`/api/w/{ws}/threads/{tid}`), method, code class |
| | `desk_db_query_seconds` | histogram | kind: read, write (write includes waiting for the write lock) |
| | `desk_db_connections` | gauge | — (SQLite has no pool: one connection per thread; this is its utilisation) |
| | `desk_event_loop_lag_seconds` | histogram | — |
| | `desk_process_resident_memory_bytes`, `desk_process_cpu_seconds_total`, `desk_process_start_time_seconds`, `desk_up` | gauge / counter | — |
| | `desk_background_failures_total` | counter | kind: scheduler, accounting_sync, webhook_queue, document_processing, file_review, workflow |
| Security | `desk_auth_failures_total` | counter | route template (401) |
| | `desk_access_denied_total` | counter | reason code (403) |
| | `desk_workspace_rejections_total` | counter | — (requests naming a workspace the caller is not a member of) |
| | `desk_tool_refusals_total` | counter | reason: payment, sealed, role, unknown_or_not_offered |
| Self | `desk_metrics_dropped_series_total` | counter | — |

**AI inference infrastructure.** OfinTech Desk calls providers over HTTP (bring your own model), so it sees no GPU, queue depth or model-server internals. Those metrics are **not reported** and none are invented. Model availability, inference latency, TTFT, token throughput (`rate(desk_llm_tokens_total{kind="output"})`), errors, timeouts and cancellations are measured from the client side. GPU metrics belong to whoever runs a self-hosted model (e.g. vLLM's or Ollama's own exporters).

## Data protection

| Requirement | How it is met | Verified by |
|---|---|---|
| Admin-only diagnostics | `/api/admin/metrics`: instance administrator session, or `DESK_METRICS_TOKEN` (≥ 32 characters, constant-time compare). The token opens this route and nothing else; unset means no token access. | `ScrapeAccess` tests; D2 (real process: 200 with token, 401 without or wrong) |
| No identifiers or content in labels | Labels are route templates, codes and closed vocabularies; MCP tool names collapse to `mcp` | `test_no_customer_data_in_metrics`: workspace, thread and user ids, email, a secret project name in the prompt, an amount in the answer, the API key and the provider host are all absent; every label value matches `[A-Za-z0-9_{}/.:+-]` |
| Cardinality and rate limits | 300 label sets per family in the engine (then dropped and counted); `sample_limit: 20000` in Prometheus | `test_cardinality_is_capped` |
| Trace authorisation | Unchanged: workspace role plus thread visibility; another workspace or run → 404 | PR #71 probes, re-run |
| No secrets in logs or alerts | Alert annotations use only exported labels (tool, kind, reason); existing log-secret tests | `test_every_alert_is_complete`; `test_isolation` |
| Dashboard access | Grafana on 127.0.0.1 only, anonymous access and sign-up off, admin password required, dashboards read-only | compose file (not run here) |
| Transport | Scrapes stay inside the compose network; Caddy terminates TLS for users; nothing in the monitoring overlay is published publicly | compose file |
| Retention | Prometheus 30 days; Alertmanager 120 h; engine metrics in memory (reset on restart, which Prometheus handles) | configuration |
| Database load | Timing hooks are two `perf_counter` calls per query; gauges are read at scrape time; no extra queries | E6 / L1 unchanged within noise |

## Dashboards (`deploy/monitoring/grafana/dashboards/`, provisioned)

| Dashboard | Panels |
|---|---|
| AI agent overview | runs/min, success rate, duration P50/P95, outcomes, active runs, tool failure rate, run errors by code, model errors by class |
| LLM performance | call duration, TTFT, output tokens/s, availability, history-budget use, characters per call, finish reasons, retries |
| Financial reliability | financial tool errors, figures by provenance, answers with unsupported figures, reconciliation lines and results, data-source errors |
| System health | up, RSS, CPU, event-loop lag, API P95 by route, requests by status, DB P95 by kind, connections, background failures, dropped series |
| Security and access | 401 by route, 403 by reason, cross-workspace requests, guard refusals |

Every query references a metric the engine exports (`test_rules_and_dashboards_reference_real_metrics`).
**Whether each panel renders data was not tested**: Grafana and Prometheus cannot run in this environment.
