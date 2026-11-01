# Multi-tenant integration results

## Scope

Two independent organisations using one OfinTech Desk Team Server at the same time, driven over real HTTP against
a real engine process (`python -m desk_engine`, server mode, its own data directory, its own port), not an
in-process test client. Organisation A "Test Company Alpha" (owner Alice, viewer Vic, and for one scenario editor
Ed); organisation B "Test Company Beta" (owner Bob, viewer Vera). Each has its own synthetic ledger (two rows,
distinct amounts), its own document with a marker string, its own workspace memory, its own conversations and its
own sessions. No production credentials and no real financial data are used.

**Simulated vs live coverage.** The only simulated component is the language model: a loopback OpenAI-compatible
server that answers the engine's real HTTP calls with scripted tool calls. Banking, e-mail, accounting systems and
PipesHub are not part of this run (they remain mock-only, AR-3). Everything else — authentication, sessions,
cookies, CSRF origin checks, routing, authorization, the agent loop, tool execution, approvals, the audit log,
background workflow runs and the SQLite database — is the real application.

## Architecture reviewed

`web.authenticate` / `load_workspace` (membership join, per-route role), `agent/harness.py` (runs under the
requesting user's id), `agent/tools.py` (`min_role`, scoped lookups), `approvals.py`, `workflows/engine.py`
(tenant carried on the run row; creator re-checked), `app.audit` (tenant and user on every entry), `routes/auth.py`
(sessions per device).

## How the run works

`e2e/multi_tenant.py` (also a step of the CI `test` job; its evidence file is uploaded as the `e2e-evidence`
artifact and committed at `docs/audit/evidence/multi-tenant-evidence.json`). For each step it records the HTTP
status, the authorization decision, the audit event it produced (read back through the admin audit API of the
tenant it was recorded against), and a SHA-256 fingerprint of the authoritative tables (`datasets`, `close_runs`,
`close_items`, `dashboards`, `files`, `file_versions`, `documents`, `memories`, `workflows`, `connections`,
`members`) plus the ledger rows of the workspace, read from the database file before and after.

## Scenarios and actual results

Commit `9e47e90`; engine: python -m desk_engine (server mode, separate process); base URL http://127.0.0.1:55695; model: simulated (loopback OpenAI-compatible server); live model coverage: none; checks: 41/41 passed.

| # | Scenario | Step | HTTP / outcome | Authorization decision | Audit event | DB before → after | Result |
|---|---|---|---|---|---|---|---|
| 1 | 1 simultaneous authentication | Alice signs up (first account) | `200` | allowed | — | — | **PASS** |
| 2 | 1 simultaneous authentication | four people sign in concurrently | `{'Bob': 200, 'Alice': 200, 'Vic': 200, 'Vera': 200}` | allowed | — | — | **PASS** |
| 3 | 1 simultaneous authentication | Alice sees only their own workspace | `200` | allowed | — | — | **PASS** |
| 4 | 1 simultaneous authentication | Bob sees only their own workspace | `200` | allowed | — | — | **PASS** |
| 5 | 1 simultaneous authentication | Vic sees only their own workspace | `200` | allowed | — | — | **PASS** |
| 6 | 1 simultaneous authentication | Vera sees only their own workspace | `200` | allowed | — | — | **PASS** |
| 7 | 2 own financial data | Alice reads their ledger | `200` | allowed | — | — | **PASS** |
| 8 | 2 own financial data | Alice lists dashboards and datasets of their own organisation only | `200/200` | allowed | — | — | **PASS** |
| 9 | 2 own financial data | Bob reads their ledger | `200` | allowed | — | — | **PASS** |
| 10 | 2 own financial data | Bob lists dashboards and datasets of their own organisation only | `200/200` | allowed | — | — | **PASS** |
| 11 | 2 own financial data | Vic reads their ledger | `200` | allowed | — | — | **PASS** |
| 12 | 2 own financial data | Vic lists dashboards and datasets of their own organisation only | `200/200` | allowed | — | — | **PASS** |
| 13 | 2 own financial data | Vera reads their ledger | `200` | allowed | — | — | **PASS** |
| 14 | 2 own financial data | Vera lists dashboards and datasets of their own organisation only | `200/200` | allowed | — | — | **PASS** |
| 15 | 3/4 cross-tenant read | Alice → Beta ledger by id | `404` | denied (404) | `ev_87e2f9a29f6e886f3288cbdb` | — | **PASS** |
| 16 | 3/4 cross-tenant read | Alice → Beta ledger export | `404` | denied (404) | `ev_23118a44a023c981ba5c85a0` | — | **PASS** |
| 17 | 3/4 cross-tenant read | Alice → Beta ledger under Alpha's path | `404` | denied (404) | — | — | **PASS** |
| 18 | 3/4 cross-tenant read | Alice → Beta document | `404` | denied (404) | `ev_a2146235d5605edf1fa059c0` | — | **PASS** |
| 19 | 3/4 cross-tenant read | Alice → Beta document file | `404` | denied (404) | `ev_293c8c39d78c51b5236f1580` | — | **PASS** |
| 20 | 3/4 cross-tenant read | Alice → Beta document under Alpha's path | `404` | denied (404) | — | — | **PASS** |
| 21 | 3/4 cross-tenant read | Alice → Beta memories | `404` | denied (404) | `ev_93872a0de44a4ea4af2eb39e` | — | **PASS** |
| 22 | 3/4 cross-tenant read | Alice → Beta audit log | `404` | denied (404) | `ev_70e605566d88738f72d731b8` | — | **PASS** |
| 23 | 3/4 cross-tenant read | Alice → Beta overview | `404` | denied (404) | `ev_c6cbfb3095566ad120643906` | — | **PASS** |
| 24 | 3/4 cross-tenant read | Alice queries Beta's ledger | `404` | denied (404) | — | — | **PASS** |
| 25 | 3/4 cross-tenant write | Alice deletes Beta's ledger | `404` | denied (404) | — | `be389c04f73d64de` → `be389c04f73d64de` | **PASS** |
| 26 | 5 AI cross-tenant | Alice's agent asks for Beta's ledger, document, text and memory | `done` | denied per tool | — | `be389c04f73d64de` → `be389c04f73d64de` | **PASS** |
| 27 | 6 viewer restrictions | Vic (viewer) tries to prepare a close | `403` | denied (403) | `ev_7c28039a2b8509831570ff26` | — | **PASS** |
| 28 | 6 viewer restrictions | Vic (viewer) tries to delete the ledger | `403` | denied (403) | `ev_95f8e62cb04be51e2e715548` | — | **PASS** |
| 29 | 6 viewer restrictions | Vic (viewer) tries to create a dashboard | `403` | denied (403) | `ev_bdae794eb6b6a6d0e72026d6` | — | **PASS** |
| 30 | 6 viewer restrictions | Vic (viewer) tries to import data | `403` | denied (403) | `ev_1149d5a816cf0fd2adec6be3` | — | **PASS** |
| 31 | 6 viewer restrictions | Vic's agent: editor tools refused, reading allowed | `done` | denied / allowed | `ev_aa384663c0141dafa4c5b392` | `aa6d4b68988e5a9b` → `aa6d4b68988e5a9b` | **PASS** |
| 32 | 6 viewer restrictions | Vic's write waits for an editor; Vic cannot approve it himself | `403` | denied (403) | `ev_a11446deaeab6ac2c0f5e172` | — | **PASS** |
| 33 | 7 revocation mid-session | editor demoted to viewer: next write refused, reads continue | `422/403/200` | denied after demotion | `ev_68552ad0915cb54f4b88918f` | — | **PASS** |
| 34 | 7 revocation mid-session | member removed: workspace gone for that session | `404` | denied (404) | — | — | **PASS** |
| 35 | 8 concurrent AI tools | four agents at once, each sees only its own ledger | `4 runs` | allowed, scoped | — | — | **PASS** |
| 36 | 9 background tenant context | Alpha's workflow run belongs to Alpha and notifies Alpha's owner only | `done` | scoped | `ev_9cd5a1fc86e5ed9ca3c9ef04` | — | **PASS** |
| 37 | 10 audit attribution | every entry carries its tenant, user, event and correlation id; a viewer cannot read the log | `39+19 entries` | correct | — | — | **PASS** |
| 38 | 11 no cross-corruption | Beta's authoritative tables and ledger rows unchanged by everything Alpha did and by Beta's own reads | `fingerprint` | unchanged | — | `be389c04f73d64de` → `be389c04f73d64de` | **PASS** |
| 39 | 11 no cross-corruption | Beta's ledger rows read back intact | `200` | allowed | — | — | **PASS** |
| 40 | 12 logout isolation | Alice logs out: Alice 401, Bob and Vic still signed in | `401/200/200` | per session | session.logout rows have no workspace | — | **PASS** |
| 41 | 12 logout isolation | Vic idle for 31 minutes: signed out; Vera continues | `401/200` | per session | — | — | **PASS** |

## Observations

- Cross-tenant attempts answer 404 (the workspace does not exist for that person) with no Beta value in any body,
  and are recorded against the targeted tenant (`access.denied`, `not_a_member`) with the attacker's user id — the
  tenant's own admin can see who tried.
- The viewer's agent is refused the editor tools and still reads; his write waits for an editor and he cannot
  approve it himself (403, `approval.denied`).
- Demotion took effect on the next request of the editor's live session; removal made the workspace vanish for it.
- Four agents ran at once; each answer carried only its own organisation's sum (Alpha 1,434.56; Beta 1,287.65).
- Alpha's workflow run stayed in Alpha (`workflow_runs.workspace_id`), notified Alpha's owner only, and was a 404
  from Beta.
- Beta's fingerprint was identical at the end of the run to the one taken before Alpha's attempts began.
- One session's logout and another's idle sign-out (session aged in the database, since a live process's clock
  cannot be moved) left every other session untouched.

## Remaining risks

- The model is simulated; a real model's behaviour under injection is bounded by the same server-side controls but
  not exercised live here (AR-2, AR-3).
- One server process, one machine: no load-balanced or multi-replica deployment was tested (AR-1).
- Session idling was induced by editing `last_seen`, which is exactly what the engine compares, but it is not a
  31-minute wait.

## Final commit

Code: `3d055c1` (the run above was produced on `9e47e90`, whose engine code equals `3d055c1` apart from one
desktop typing fix; CI re-runs the script on every push — see `FINAL-RELEASE-GATE.md` for the run on the final
commit).
