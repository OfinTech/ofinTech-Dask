# 07 · Security regression (targeted)

The scope is the code changed in this branch plus the required release suites. Raw output is in `results/after/`.

| Area | Evidence | Result |
|---|---|---|
| Agent authorisation (skills, roles, approvals, tool permissions) | `test_skill_authorization`, `test_approvals_and_audit`, `test_guard_audit`, `test_ai_security`, `test_security_boundaries` | pass |
| Tenant isolation | `test_isolation`, `test_tenant_isolation`, `test_embedded_tenant`, E5 (0 exposures / 657 calls), tenant mixing under concurrency (`RedTeamProbes`) | pass |
| Sensitive log redaction | `test_isolation.test_failed_logins_and_upstream_failures_log_no_secrets`, `test_ai_security` (masked bank identifiers) | pass |
| Metrics authentication | `test_observability.ScrapeAccess`; D2 on a real process: 200 with the token, 401 without or wrong | pass |
| No customer data in metrics | `test_no_customer_data_in_metrics` | pass |
| Trace authorisation | `RedTeamProbes.test_trace_of_another_workspace_is_not_found` | pass |
| Database connection isolation | the new reads run in pool threads with their own connections; `ConnectionsOfEndedThreads`; `test_shutdown_race`; tenant mixing under concurrency | pass |
| Response integrity | `test_response_lifecycle`, `EveryModelReplyIsCheckedForCompleteness`, E3 17 / 17 | pass |
| Financial correctness | E1 1,385 / 1,385; E2 86 / 86; R1 211 / 211; F1 168 / 168; E10 precision 100% and recall 99.83%; `test_financial_regression` | pass |
| Tool execution permissions | `test_guard_audit`, `RedTeamProbes`, `test_ai_security.test_4` | pass |

## Suite totals

| Suite | Result |
|---|---|
| Security and regression modules (19) | **213 / 213 OK** (`results/after/security_regression_suites.txt`) |
| Full engine suite | **574 / 574 OK**, twice with `-X faulthandler` and no crash (`results/after/engine_suite.txt`) |
| Static analysis | ruff F and the CI S gate clean |
| Dependency audits (`pip-audit`, `gitleaks`, `npm audit`) | not available here; CI. No dependency added. |

## Security-relevant notes on this branch's changes

- **Prompt context in a worker thread.** The function is read-only apart from auditing refused pins. The audit write uses the process-wide write lock. No events are published from the thread.
- **Tool availability in a thread.** Same checks and the same results. The role check still reads the membership at call time, and is re-checked at each tool call (unchanged).
- **The off switch** restores the previous execution path exactly; it is covered by a test.
- **No test was removed or weakened.** One new test (`test_shutdown_race`) runs in a subprocess so that a crash fails the test rather than the suite.
