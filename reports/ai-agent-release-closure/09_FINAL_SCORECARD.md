# 09 · Final scorecard

The method and weights are unchanged from the audit. Only new evidence moves a score. Model accuracy is
**not scored**: the open-source engine ships no model, so answer accuracy belongs to whichever model a deployment
configures (corrected 14 Nov 2026, see 11).

| Category | Weight | 62 audit | 78 remediation | 82 readiness | 83 release gates | **Now** | Evidence for the change |
|---|---:|---:|---:|---:|---:|---:|---|
| Accuracy (real model) | 20 | — | — | — | — | **not scored** | not applicable: no model ships with the open-source edition |
| Financial grounding | 15 | 70 | 85 | 86 | 86 | **86** | re-runs only |
| Security | 15 | 68 | 84 | 84 | 84 | **84** | re-runs only; model-level injection untested |
| Reliability | 15 | 45 | 78 | 83 | 84 | **86** | + a slow database no longer stalls other users (P4 2.3× throughput); hung tools isolated; shutdown crash found and guarded by a subprocess test |
| Tool usage (engine) | 8 | 72 p | 80 p | 82 p | 82 p | **82 p** | unchanged |
| Context management | 7 | 55 | 72 | 80 | 80 | **80** | unchanged |
| Latency (engine) | 5 | 88 p | 86 p | 86 p | 86 p | **84 p** | − healthy-DB P50 about 40% higher, throughput 12–25% lower (thread handoffs); offset by the slow-DB gains in Reliability |
| Observability | 7 | 40 | 55 | 72 | 78 | **78** | no delivery evidence obtained |
| Scalability | 4 | 55 p | 60 p | 62 p | 66 p | **66 p** | + an engine-only footprint (274 MB, 11 threads at 100 users); − throughput per process lower |
| Maintainability | 4 | 80 | 82 | 83 | 84 | **84** | unchanged |

(p = provisional)

(86·15 + 84·15 + 86·15 + 82·8 + 80·7 + 84·5 + 78·7 + 66·4 + 84·4) / 80
= (1,290 + 1,260 + 1,290 + 656 + 560 + 420 + 546 + 264 + 336) / 80 = 6,622 / 80 = **82.8 → 83 / 100 (provisional)**

| Assessment | Score |
|---|---|
| Initial audit | 62 |
| Remediation | 78 |
| Production readiness | 82 |
| Final release gates | 83 |
| **Current, verified** | **83 (provisional)** |

The score is unchanged. This branch improved resilience and lowered raw engine throughput. The remaining blockers
are about evidence (monitoring runtime, alert receiver, approvals), not code, so they cannot move the score from here.
The decision is 08: **NO-GO**.
