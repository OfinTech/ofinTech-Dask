# Production readiness

## Decision

**Not production-ready for enterprise financial environments.**

Every HIGH code defect from the audit is fixed and verified, but one required release gate is open: no real model
has been evaluated inside the agent loop (AI-04). Without it, nobody knows how often the assistant gives a wrong
figure, picks the wrong tool, or follows instructions planted in a file. That is the core risk for a financial product.

## Release gates

| Gate | Status | Evidence |
|---|---|---|
| No open HIGH code defect from the audit (AI-01, AI-02, AI-03) | **Met** | SECURITY_VERIFICATION.md, RELIABILITY_TEST_RESULTS.md |
| Engine suite passes, no test removed or weakened | **Met** | 530 / 530; 100 / 100 existing security tests |
| Calculator and ledger edge cases | **Met** | E1 1,385 / 1,385; E2 86 / 86 |
| Tenant isolation | **Met** | E5 0 / 657 |
| Live-model evaluation per supported model (AI-04) | **NOT MET: blocked** | LIVE_MODEL_EVALUATION.md |
| Prompt-injection resistance with a real model | **Not met** (part of E8) | — |
| CI green on the PR (UI typecheck, `pip-audit`, `gitleaks`) | **Pending** | not available in this environment; CI will run them |
| Human review and merge by the owner | **Pending** | this PR is not merged |
| Monitoring in production (metrics, alerts on `outcome` / `error_code`) | **Not met** (AI-12 partial) | recommended before a wide rollout |

## Open risks

| Risk | Severity | Note |
|---|---|---|
| Real-model accuracy unknown (AI-04) | HIGH (process) | blocks release |
| Figure check is advisory | MEDIUM | it marks unsupported figures but does not stop an answer |
| DB-bound tools on the event loop (AI-10 partial) | MEDIUM | a slow query can delay other users |
| No token budget or summary (AI-11 partial) | MEDIUM | long threads lose earlier tool results |
| No metrics endpoint or tracing (AI-12 partial) | MEDIUM | data is recorded per run, not exported |
| Single process on SQLite; single-instance recovery (AI-21) | INFO | limits scale-out |
| No structural envelope for tool output (AI-22) | INFO | writes remain approval-bound |
| "91 days" vs constant 91.25 | LOW | 23 of 24,000 E10 answers |

## What would change the decision

1. Run E8 live for each supported model with authorized, non-production credentials, and meet the thresholds in
   LIVE_MODEL_EVALUATION.md.
2. CI green on this PR, and an owner's review.
3. Recommended before a wide rollout: export `outcome`, `error_code` and `timing` as metrics with alerts.

## Rollback

All changes are in application code; there is **no schema migration**. Additive fields only (`outcome`,
`error_code`, `timing`, `finish`, `incomplete`, `user_figures`, `figure_provenance`). Reverting the PR restores the
previous behaviour. Two behaviour changes owners should know about:
- skills edited by editors switch off until an admin approves them;
- pins of disabled or unapproved skills now return 422 instead of being accepted.
Retries can be turned off with `DESK_LLM_RETRIES=0`.
