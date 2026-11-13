# AI agent remediation: summary

**Audit reference:** `reports/ai-agent-audit/`, PR #68 (merged into `main` at `bd1e952`).
**Branch:** `fix/ai-agent-remediation`, based on `main` at `00d93f8` (14 Nov 2026).
**Nothing is merged.**

## Baseline (re-measured, not assumed)

| | |
|---|---|
| Commit | `00d93f83a3b1af2103de1f0bf81cef15c34e9fa4` (`main`, after PR #69) |
| Runtime | Python 3.13.18, SQLite 3.53.1, Node 22.23.4; Linux VM, 2 vCPU Intel Xeon @ 2.10 GHz, 7 GB RAM |
| Engine suite | `cd engine && python -m unittest discover -s tests -t .`: **474 tests, OK** |
| Audit evaluations on `main` | E1 1,359/1,385 · E2 85/86 · E3 7/17 · E4 as audited · E5 0 exposures in 657 calls · E7 0/4 probes pass · E9 1.7 KB/conversation |
| Model configuration | No authorized model provider. See LIVE_MODEL_EVALUATION.md. |

The audit's results were reproduced exactly on `main`, so every finding below was confirmed before it was fixed.

## Findings

| ID | Severity | Status | What changed | Proof |
|---|---|---|---|---|
| AI-01 | HIGH | **Resolved** | `get_enabled_skill` is now the only lookup that feeds the assistant: deny by default, checked on every call. The agent loop, `load_skill` and `read_skill_file` use it. Pins of skills that are not enabled are refused at `POST/PATCH /threads` (422) and audited. Two more paths found during the fix are closed too: `read_skill_file` read disabled skills, and a disabled built-in could be pinned. | 8 brief scenarios + matrix (`test_skill_authorization.py`); E7 P2, P3 |
| AI-02 | HIGH | **Resolved** | An edit by someone who may not enable skills switches the skill off until an admin approves it (audited `skill.awaiting_approval`). A client-sent `enabled` field is ignored. | `test_editor_edit…`, permission matrix; E7 P1 |
| AI-03 | HIGH | **Resolved** | Both adapters require completion evidence. A stream without it fails as `llm_incomplete`; malformed parts fail it as `llm_malformed`. A reply cut by the output limit or a content filter is kept, marked incomplete, and the run ends `answer_incomplete`. An empty reply fails `answer_empty`. The app shows "This answer is incomplete". | 18 tests (`test_response_lifecycle.py`, both adapters); E3 4 scenarios |
| AI-04 | HIGH (process) | **Blocked** (release gate open) | The live evaluation is ready and its scorer re-validated (oracle 20/20, fabricator 0% figure accuracy), but no authorized model endpoint exists in this environment. | LIVE_MODEL_EVALUATION.md |
| AI-05 | MEDIUM | **Resolved** | Provenance-aware, unit-aware figure check (`agent/figures.py`): tool / derived / memory / user / unsupported. Tolerance follows the precision shown, units must agree, and counts with a unit are checked. The user's own figures are shown as the user's. | E10: precision 100%, recall 99.83% (old: 77.9% / 29.7%); `test_figure_provenance.py` |
| AI-06 | MEDIUM | **Resolved** | Errors are classified. Transient ones are retried with exponential backoff and jitter, honouring Retry-After, at most `DESK_LLM_RETRIES` (2) times, and only before any text was shown. Permanent errors and tools are never retried. | lifecycle tests; E3 `model_500_then_ok`, `model_timeout` |
| AI-07 | MEDIUM | **Resolved** | A tool's unexpected exception returns an error to the model, and the turn continues. Details are logged, not sent to the model. | lifecycle test; E3 |
| AI-08 | MEDIUM | **Resolved** | Malformed stream parts are counted and fail the call. | lifecycle test; E3 |
| AI-09 | MEDIUM | **Resolved** | Dataset, connection and collection names leave the system role and go in one user-role "workspace context" message as JSON. | `test_prompt_surfaces.py`; E7 P4 |
| AI-10 | MEDIUM | **Partially resolved** | The ten pure calculators run in a worker thread, so their timeout holds and they cannot stall the loop. Tools that use the database still run on the event loop; the KPI scorecard already computes in a thread. | `test_agent_resources.py`; E3 `tool_blocking_sync_work` |
| AI-11 | MEDIUM | **Partially resolved** | The user's own earlier messages (≤ 20, 3,000 characters) are carried when a conversation outgrows the 60-message window. There is still no token budget or summary of earlier answers and tool results. | E3 `early_fact_retained`; resources test |
| AI-12 | MEDIUM | **Partially resolved** | Each answer records `timing` (ttfb_ms, total_ms, attempts) and `finish`. Each run records `outcome` and `error_code`, returned by the thread API. There is no metrics endpoint or tracing. | lifecycle tests |
| AI-13 | LOW | **Resolved** | Several IRRs are detected (by sign changes, then a scan) and reported as "not unique (multiple IRRs)" with every root. The same applies to XIRR. | E1 non-conventional 30/30 |
| AI-14 | LOW | **Resolved** | The XIRR/IRR bracket widens up to 100,000,000%, with a robust NPV for the root search. | E1 XIRR > 1,000%; 4 more E1 cases found by the corrected ground truth |
| AI-15 | LOW | **Resolved** | Growth is measured against \|previous\|; from zero it is n/a; CAGR says when it is not meaningful. | E1; edge-case tests |
| AI-16 | LOW | **Resolved** | Rates of 1 or more are refused with the likely intended value. Negative tax and loan rates, cost ≤ 0, negative salvage and salvage above cost are refused too. | E1; edge-case tests |
| AI-17 | LOW | **Resolved** | A month outside the ledger returns "No ledger data for YYYY-MM" and no figures. | E2 86/86 |
| AI-18 | LOW | **Resolved** | An identical read call beyond the second in a run is served from that run's own result. Writes never are. | E3 2 executions of 6 calls |
| AI-19 | LOW | **Resolved** | Finished tasks are released. | E9: 0 tasks kept (was 1,500); RSS flat after 750 conversations |
| AI-20 | LOW | **Resolved** | `calc_financial_ratios` no longer mutates its arguments. | E1; edge-case test |
| AI-21 | INFO | Unchanged | Single process on SQLite; run recovery assumes one instance. | — |
| AI-22 | INFO | Unchanged | No structural envelope for tool output. Writes stay approval-bound. | — |
| AI-23 | INFO | Unchanged | Audit rows keep 200 characters of a failed tool's error text. | — |

## Commits

| Commit | Change |
|---|---|
| `5180a8a` | AI-01, AI-02: only admin-approved skills reach the assistant |
| `eb934df` | AI-03, AI-06, AI-07, AI-08: completion evidence, retries, error classes, tool exceptions, UI notice |
| `466056a` | AI-13–17, AI-20: calculator edge cases, no month substitution |
| `51d714a` | AI-05: provenance-aware figure check |
| `a0faeb3` | AI-10 (partial), AI-11 (partial), AI-18, AI-19 |
| `c990427` | AI-09: names out of the system role |
| `2103709`, `09095ce` | Wording ("multiple IRRs"), typing clean-up; no behaviour change |
| (reports commit) | These reports, E10, and five corrections to the audit's own evaluation scripts (below) |

## Changes to the audit's evaluation scripts, and why

None of these changes what a pass means. Each corrected script was also run against the **old** code, and those
"before" figures are the ones compared below (`results/before/*.json`). The scripts' outputs as audited are kept
as `*.as_audited.json`.

1. **E1, sales tax at "15":** the check read `o.data["gross"]`, and a tool that now refuses returns no data. The check accepts a refusal (`data is None`) as well as a small gross.
2. **E1, XIRR ground truth:** the audit's root scan stopped at 1,000%, so for four generated cases with a real XIRR above that it expected "none". The ground truth is now an independent 60-digit bisection up to 1e8%. Case #29 was verified by hand: the XIRR is 6,716.4%, and the XNPV there is −2.5×10⁻¹¹. On the old code the corrected E1 gives 1,355/1,385 (it was 1,359 with the faulty ground truth).
3. **E3, repeated calls:** the scenario counted tool *messages* as executions, but every call must get a result message. It now counts executions of the tool itself (and still reports the messages).
4. **E7:** when the API now refuses a pin (422), the probe pins the skill in the database anyway, to test the second (run-time) layer, and records the API refusal.
5. **E8 oracle:** the scripted "ideal model" stated March's net margin after fetching only October's scorecard. The new figure check flagged it (correctly; the old one accepted it by coincidence). The oracle now fetches both months.

## Test totals

| | Before (`main`) | After (this branch) |
|---|---:|---:|
| Engine suite | 474 OK | **530 OK** (56 new) |
| New tests failing on `main` | — | 52 of 56 (the other 4 pin behaviour that was already correct) |
| Existing agent-security tests (11 modules) | 100 OK | **100 OK** |
| E1 calculators (corrected) | 1,355 / 1,385 | **1,385 / 1,385** |
| E2 ledger | 85 / 86 | **86 / 86** |
| E3 reliability | 7 / 17 | **17 / 17** |
| E5 cross-workspace | 0 exposures / 657 | **0 / 657** |
| E7 system-role probes | 0 / 4 | **4 / 4** |
| E10 figure check (24,000 answers) | precision 77.9%, recall 29.7% | **precision 100%, recall 99.83%** |
| UI tests, translations, browser e2e (smoke, multi-tenant, embedded, session, i18n) | pass | **pass** |
| Live model (E8) | not run | **NOT EXECUTED: BLOCKED** |

See also: SECURITY_VERIFICATION.md, FINANCIAL_ACCURACY_REPORT.md, RELIABILITY_TEST_RESULTS.md,
LIVE_MODEL_EVALUATION.md, PERFORMANCE_COMPARISON.md, PRODUCTION_READINESS.md, FINAL_SCORECARD.md.
