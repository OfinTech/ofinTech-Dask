# Release-closure evaluations

Run from `engine/`. Every model is scripted, so the results measure the engine, not model quality.

| Script | Measures |
|---|---|
| `p3_loop_db_profile.py [out]` | Database calls on the event-loop thread per agent turn, by caller |
| `p4_loop_benchmark.py out [delay]` | `/health` and turn latency, and throughput, with 20 concurrent turns and every DB read delayed by `delay` seconds (run in each checkout; `DESK_AGENT_OFFLOAD_READS=0` for the off column) |
| `r2_process_footprint.py out` | The engine's own RSS, threads and CPU under 10, 50 and 100 HTTP users (a real process and a local scripted model server) |

L1 (`../../ai-agent-final-release/evals/l1_release_load.py`) and D2 (`.../d2_deployment.py`) were re-run on this
branch; their results are in `../results/after/`. `../results/before/` holds PR #72 head results from the same session.
`l1_release_load_shared_pool_superseded.json` is the run that found the thread-pool starvation, before the dedicated
read pool.
