# 06 · Resource limits

## Result: configuration validated; runtime enforcement NOT VERIFIED (no Docker daemon). Gate BLOCKED.

## Limits added

| Service | File | cpus | mem_limit | mem_reservation | pids_limit | stop_grace_period |
|---|---|---:|---:|---:|---:|---|
| engine | `server/docker-compose.yml` | 2.0 | 2 GB | 512 MB | 512 | 30 s |
| caddy | `server/docker-compose.yml` | 0.5 | 256 MB | — | 128 | default |
| prometheus | `deploy/monitoring/docker-compose.monitoring.yml` | 1.0 | 1 GB | — | 256 | default |
| alertmanager | same | 0.25 | 128 MB | — | 64 | default |
| grafana | same | 0.5 | 512 MB | — | 256 | default |

MinIO (`docker-compose.minio.yml`, optional) and the AIStor overlay got **no limits**: I have no memory measurement
for them, and a guessed limit risks out-of-memory kills. There is no GPU or inference container in this repository:
models are external providers, so no GPU limits apply.

## Evidence used for the engine's numbers

`evals/r2_process_footprint.py` ran a real `serve` process, driven over HTTP from another process with a scripted
local model server.

| Users | Turns/s | Errors | P95 | Engine peak RSS | Engine peak threads | Engine CPU |
|---:|---:|---:|---:|---:|---:|---:|
| idle | — | — | — | 139 MB | 1 | — |
| 10 | 25.8 | 0 | 457 ms | 144 MB | 10 | 0.99 cores |
| 50 | 22.6 | 0 | 3.04 s | 203 MB | 11 | 0.95 |
| 100 | 21.1 | 0 | 6.09 s | 274 MB | 11 | 0.95 |

Clients poll the run every 100 ms over HTTP, so throughput here is lower than in-process. Latency is queueing on
one CPU core.

How the numbers were chosen:
- **Memory:** 2 GB is 7× the measured peak, leaving room for large ledgers (analysis caches) and document processing. The 512 MB reservation is above the measured working set.
- **CPU:** one engine process uses about one core (Python); 2 CPUs leave room for worker threads and the document pipeline.
- **pids:** the peak was 11 threads; 512 leaves room for the read pool (8), the default pool, worker loops and OCR subprocesses.
- **Stop grace:** 30 s is above the 10 s shutdown drain (05); measured graceful shutdowns took 0.16–0.26 s (D2).

## Validation

| Check | Result |
|---|---|
| Compose syntax, base + monitoring overlay merged (`docker compose config`, scratch copy with dummy env) | exit 0; the effective values above appear in the merged model |
| Base + sealed overlay (`config -q`) | exit 0 |
| Startup, health, graceful shutdown, restart, backup and restore of the engine **process** (D2, re-run on this branch) | pass: 3 of 3 graceful shutdowns, 0.16–0.26 s |
| Limits applied by a container runtime (`docker inspect` HostConfig), behaviour at the memory limit, CPU throttling | **NOT VERIFIED**: no Docker daemon in this environment |

## To verify at deployment

```bash
docker compose up -d
docker inspect $(docker compose ps -q engine) --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemoryReservation}} {{.HostConfig.PidsLimit}}'
# expected: 2000000000 2147483648 536870912 512
docker stats --no-stream            # under a normal day's load: memory well below 2 GB
docker compose stop engine && docker compose logs --tail 20 engine   # graceful: "Application shutdown complete"
```
