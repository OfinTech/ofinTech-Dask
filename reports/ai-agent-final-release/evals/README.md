# Final release evaluations

Run from `engine/`. All data is synthetic, and every model is scripted.

| Script | What it measures |
|---|---|
| `l1_release_load.py out.json [--quick]` | Scenarios A–E. A: normal mix at 25 users. B: 10, 25, 50 and 100 users. C: 200 users at once. D: 10 × 60-turn conversations. E: model 503, timeout and network failures, tool timeouts and a slow database, each with recovery. Reports latency, throughput, errors, CPU, memory and peak concurrent runs. |
| `d2_deployment.py out.json` | A real `python -m desk_engine serve` process in server mode, against a temporary directory. Checks config validation, refusal to start without a master key, health and readiness, metrics token access, graceful SIGTERM, restart, and encrypted backup → check → restore → serve. |

The monitoring configuration under `deploy/monitoring/` is checked by `engine/tests/test_observability.py`.
