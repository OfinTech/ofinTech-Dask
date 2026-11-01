# Final AI approval security (OP-9)

## Scope

Approvals are the only path by which an AI agent or a workflow obtains a person's permission for a sensitive
action (anything with `effect="write"`, any MCP tool not approved to run unattended, a workflow's approval step).
This report covers their expiry, their binding to user, tenant, tool and arguments, single use, revocation,
concurrency and auditability, as implemented and tested on `3d055c1`.

## Architecture reviewed

- `agent/harness.py`: a write tool call becomes an approval request; the run waits; after the decision the run
  resumes and executes the stored call. Executed under the requesting user's id, never the approver's.
- `workflows/engine.py`: tool steps needing approval and explicit approval steps; `on_approval` resumes the run.
- `routes/approvals.py`: `POST /api/w/{ws}/approvals/{aid}/decide` (any member reaches the route; the service
  requires editor+). `routes/threads.py:stop` expires a thread's pending approvals.
- The `approvals` table (migration 19 adds `expires_at`, `args_digest`, `executed_at`, `executed_by`).

## Changes implemented (`1f1a8de`)

`desk_engine/approvals.py`, one service used by the harness, the workflow engine and the route:

| Requirement | Implementation |
|---|---|
| Expiry by sensitivity | `expires_at = created_at + ttl_for(tool)`: **4 h** for anything that leaves the workspace or runs code elsewhere (sends, messages, workflow runs, MCP tools, imports from Gmail/Drive); **24 h** for `edit_file` (the person's own file) and workflow approval steps. `expire_stale()` marks overdue rows; `decide` and `claim` refuse them regardless. |
| Binding | the row holds `workspace_id`, `requested_by` (the run's user — the model never supplies it), `source`/`source_id` (run), `tool`, the JSON arguments **and** `args_digest = SHA-256(canonical JSON of {{ws, requester, tool, args}})`, `created_at`, `expires_at`, `status`. |
| Unpredictable id | `ap_` + 80 random bits from `uuid4` (os.urandom); never derived from anything the model sees. |
| Single use | `claim()` is one atomic `UPDATE … SET status='executing' WHERE id=? AND status='approved' AND workspace_id=? AND tool=? AND args_digest=? AND expires_at>=now`; `rowcount==1` wins, every other caller is told `replayed`. `finish()` moves it to `executed`; no path returns a row to `approved`. |
| Lost permission | at decision and at claim the requester must still be an active member with the tool's `min_role` (`_requester_may`); otherwise the approval is expired and the action refused. |
| Changed target/parameters | the digest is recomputed from what is about to execute and compared; any difference (tool, requester, workspace, any argument byte) → `changed`. |
| Concurrency | the atomic claim; proven with eight threads (one `None`, seven `replayed`). |
| Revocation | stopping the thread (`status='expired'`); removing a member or demoting them to viewer (`expire_for_member`, pending **and** approved rows); disabling the account (membership join fails → `permission`). Approvals are bound to the **user**, not to a browser session: logging out one device does not cancel a request that another device of the same person may still see — documented limitation, see below. |
| Audit | `approval.requested`, `approval.approved/rejected`, `approval.expired`, `approval.replayed`, `approval.changed`, `approval.permission`, `approval.missing`, `approval.denied` (viewer tried to decide), `tool.run` with `approval_id`, all with `decision` and `result` (see `FINAL-AUDIT-LOGGING-VERIFICATION.md`). |
| Model-supplied ids ignored | nothing in the execution path reads an approval id from tool arguments; the run resumes from its own stored `pending` list, and `claim` re-derives everything from the row and the context. |

The approval card in the UI shows the stored summary and arguments; the decision call carries only `approve` and
a note.

## Test scenarios and actual results (`tests/test_approvals_and_audit.py`, 10 tests, all passing locally and in CI)

| Mandatory test | Test | Result |
|---|---|---|
| Valid approval succeeds exactly once | `ApprovalLifecycle.test_valid_approval_succeeds_exactly_once_and_is_consumed` — executed once, status `executed`, second claim `replayed`, one workflow run, ledger fingerprint unchanged, trail requested→approved→run | PASS |
| Expired approval rejected | `test_expired_approval_is_rejected_at_decision_and_at_claim` — 409 `approval_expired` at decision; `expired` at claim when it lapses after the decision; nothing ran | PASS |
| Replayed approval rejected | `test_replayed_decision_is_rejected` — second and third decisions 409 `approval_decided`; two `approval.replayed` events | PASS |
| Approval for another user rejected | `test_approval_for_another_user_is_rejected_at_claim` — executing as a different requester → `changed` | PASS |
| Approval for another tenant rejected | `test_approval_for_another_tenant_is_rejected` — 404 through the API under another workspace; `missing` at claim | PASS |
| Different action rejected | `test_approval_for_a_different_action_or_modified_parameters_is_rejected` — other tool → `changed` | PASS |
| Modified parameters rejected | same test — one trailing space, one extra key → `changed`; the original then succeeds once | PASS |
| Revoked approval rejected | `test_revoked_approval_is_rejected` — thread stopped → `expired`; requester removed → `expired`, decision 409 | PASS |
| Concurrent replay cannot both succeed | `test_concurrent_claims_cannot_both_succeed` — 8 threads, exactly one wins | PASS |
| Approval cannot bypass RBAC | `test_approval_cannot_bypass_rbac` — viewer's decision 403 (`approval.denied`); requester demoted after approval → claim `permission`, row expired; a model naming an approval id in its arguments still waits for a new approval | PASS |

Also exercised: `test_rbac.test_background_job_stops_when_its_creator_loses_the_editor_role` (workflow approval
step expired on demotion; decision 409), `test_ai_financial_assurance` (approvals in every injection case),
`test_isolation.ApprovalRace` (two decisions racing: one wins), and the live run (`MULTI-TENANT-INTEGRATION-RESULTS.md`
step "Vic's write waits for an editor; Vic cannot approve it himself").

## Evidence references

Commit `1f1a8de`; CI run 41284426822 (engine 311 tests OK) and the run on the final commit in
`FINAL-RELEASE-GATE.md`; `docs/SECURITY.md` "Agent actions"; `CHANGELOG.md`.

## Remaining risks

- Approvals are user-bound, not session-bound: a person with two devices who logs out of one keeps the pending
  request visible on the other. Acceptable because the request is still that person's, still expires, and still
  needs an editor; recorded as a documented limitation rather than a finding.
- The TTLs (4 h / 24 h) are fixed in code; a per-workspace policy is a later improvement.
- `expire_stale` runs lazily (on decision/claim), not on a timer; a pending row past its expiry stays `pending`
  in listings until touched, but can never be approved or executed. The UI shows `expired: true` from `out()`.

## Final commit

`3d055c1` (service introduced in `1f1a8de`, unchanged since).
