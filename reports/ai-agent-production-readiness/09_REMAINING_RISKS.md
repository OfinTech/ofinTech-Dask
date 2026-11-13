# 09 · Remaining risks

The "Owner" column names a role, not a person. Who fills each role is the product owner's decision.

| # | Risk | Severity | Owner | Recommended action | Blocks release? |
|---|---|---|---|---|---|
| 1 | **Real-model accuracy unknown** (AI-04): wrong figures, wrong tool choice, injection resistance, refusals, multi-turn behaviour | HIGH (process) | Product owner + AI evaluation | Run E8 live for each supported model with authorised, non-production credentials (03); approve thresholds | **Yes** |
| 2 | **No alerting** on the new metrics | MEDIUM | SRE / operations | Scrape `/api/admin/metrics`; deploy the proposed rules (07) and test each alert | **Yes** (observability gate) |
| 3 | **No agreed SLOs** | MEDIUM | Product owner + SRE | Approve or change the proposed SLOs (06); measure end-to-end with the chosen model | **Yes** (performance gate) |
| 4 | Figure check is advisory: unsupported figures are marked, not blocked | MEDIUM | Engineering + product | Decide a policy for unsupported figures in financial answers (block, require confirmation, or keep marking), using E8 data | No, but decide before wide rollout |
| 5 | The agent loop's own per-step DB reads run on the event loop (RT-03) | LOW | Engineering | Move history and context reads to a thread if E6 / P1 on production-size databases show loop lag (watch `desk_event_loop_lag_seconds`) | No |
| 6 | CPU-bound KPI work contends for the GIL under load (`/health` P95 189 ms at 20 concurrent scorecards) | LOW | Engineering | Cache scorecards per ledger version (already cached warm), or run heavy analysis in a process pool if the lag alert fires | No |
| 7 | History budget counts characters, not tokens; no per-model context window | LOW | Engineering | Add an optional per-provider context-window setting; keep the 4-characters-per-token estimate as the fallback | No |
| 8 | Reconciliation has no currency, ambiguity or duplicate labels | LOW | Product | Label ambiguous pairs and possible duplicates in the close's bank view; reject a statement whose currency differs | No |
| 9 | Single process on SQLite; restart recovery assumes one instance (AI-21) | INFO | Engineering | Document the single-instance deployment; revisit if multi-host is needed | No |
| 10 | No structural envelope for tool output (AI-22) | INFO | Engineering | Keep writes approval-bound; consider marking tool output as untrusted in the request when E8 shows injection issues | No |
| 11 | Metrics reset on restart and are per process | INFO | SRE | Prometheus scraping makes this normal; note it in the operations guide | No |
| 12 | UI typecheck, `pip-audit`, `gitleaks`, `npm audit` not run in this environment | INFO | CI | Must be green on the pull request | Yes, until CI passes |
| 13 | The 23 "91 days" misses in E10 (formula constant 91.25) | LOW | Engineering | Mark constants in tool output so the figure check ignores them | No |

## Limitations of this review

- Every model in every test is scripted. The figures measure the product's controls and deterministic layer, not
  model intelligence.
- Performance was measured on a 2-vCPU VM with an instant model. Production numbers depend on the model provider and
  the hardware.
- R1, E10, F1 and E8's scenarios use synthetic data (the HarborLine demo company and generated statements). No real
  customer data was used.
- Browser end-to-end tests were not re-run for the one UI change (a conditional notice in Search); the UI unit
  tests, translation check and an esbuild compile passed, and CI runs the typecheck.
