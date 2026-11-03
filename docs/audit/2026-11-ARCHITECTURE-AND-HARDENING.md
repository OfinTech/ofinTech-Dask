# Architecture and hardening pass — November 2026

Baseline `fcd001f`. Branch `audit/clean-architecture-security-hardening`. This repository is public, so nothing
below describes a working exploit path; findings are named with enough detail to review the fix and no more.

This is not a first audit. `docs/audit/` already holds a threat model, a security audit, a database and an API
review, an AI-security suite and a release gate, and `OPEN-RISKS.md` records what the owner has not yet accepted.
The question for this pass was therefore not "is there an audit" but **"does what the audit claims still hold, now
that the code has moved on, and is it held by something other than everyone remembering?"**

Since the last pass the engine gained the embedded agent gateway, lost PipesHub, gained token pricing, and the
public site was replaced and moved. Those are the parts no prior document covers.

## Headline

**No exploitable vulnerability was found.** Workspace scoping, payment refusal and sealed-workspace egress all
behave as documented wherever they were checked. What this pass changes is the standing of those claims: three
security properties were held by discipline and proved by hand, and are now held by tests that read the system
rather than a list someone maintains.

## What was verified, and how

| Property | Method | Result |
|---|---|---|
| Workspace scoping across the HTTP surface | Planted a canary row per resource family in workspace A; called **every** id-taking route as workspace B | 88 of 117 routes refuse outright; 0 returned 200; 0 leaked the canary. The remaining 29 stop at request validation and are reported, not assumed |
| Object-level authorisation in handlers | Read every `db.update` without a `workspace_id` guard and every `DELETE ... WHERE id = ?` | Each is preceded by a read that scopes on the workspace. Correct, but by discipline: `db.update`'s guard is optional |
| Payment refusal | `is_payment_tool` at registration and at the call | Enforced on both paths |
| Sealed-workspace egress | `EGRESS_TOOLS` against the live registry | The list is complete today: all nine Google tools and the collaboration tool are on it |
| Refusal of a protected route | `web._audit_denied` | Every 403 is already recorded |

## Findings

### F-1 — A guard that refuses leaves no record (medium)

`agent/tools.py` refuses a payment tool, an outbound tool in a sealed workspace, and a tool above the caller's role.
All three returned a sentence to the model and wrote nothing. The control was never bypassed; it was silent. A
workspace in which something tried repeatedly to move money, or to push a ledger to an outside service, was
indistinguishable from one in which nothing had happened — so the product could not show that its two hardest
promises had been kept, and nobody could notice them being tested.

This is an evidence and detection gap rather than an access-control failure, which is why it is medium and not
high. In a product whose audit trail is part of what it sells, that is still the wrong place to be silent.

**Fixed.** Each refusal now writes `tool.refused` with the reason (`payment`, `sealed`, `role`), the tool's name and
the source of the call. The entry deliberately omits the arguments: those are written by the model out of the
workspace's own data and can carry account numbers and counterparties into a log that is read more widely than the
ledger. Recording cannot change the outcome — if the write fails the refusal still stands, and there is a test for
that. Regression tests: `engine/tests/test_guard_audit.py` (6).

### F-2 — The sealed-workspace tool list had no completeness guard (low now, high later)

`tool_sends_out` matches a tool by name against `EGRESS_TOOLS`. The list is complete today. Nothing held it that
way: a new tool that reaches an outside service is sealed only if whoever writes it remembers to add the name, and
one that is missed is offered inside a sealed workspace and sends.

**Fixed.** Every registered tool must now be accounted for — on the list, an MCP tool, or in a category declared as
staying inside. A new category fails the test by name, so the decision is made once and deliberately instead of
being missed quietly. It fails closed. Test: `test_security_boundaries.test_every_registered_tool_is_classified_as_inside_or_outside`.

### F-3 — The isolation proof named eight routes out of 117 (medium)

Scoping in this engine is a discipline, not a structure: `db.update` takes an optional `workspace_id`, deletes are
issued by bare id, and each handler is correct because it first reads the row with the workspace in the `WHERE`
clause. Reading the code, that holds everywhere. The weakness was never a handler — it was that the test proving
isolation enumerated eight routes by hand while 117 take a resource id, so a route written next month fell outside
the proof, and the failure it would introduce is the one that matters most in a multi-tenant financial product.

**Fixed.** `engine/tests/test_tenant_isolation.py` asks the router which routes exist. A path parameter whose table
is not declared fails the test by name, so a route to a new resource family forces its author to say what it reads
and the proof then covers it. A wrong declaration is safe: the canary does not match, the route is reported as not
exercised, and nothing passes that should not.

## Architecture

The layering is sound and worth leaving alone. `web.Router` holds authentication, workspace loading, role checks,
step-up freshness and body parsing in that order, so membership and role are decided before a request body is even
read. Domain services (`close/`, `files/`, `costcentres/`, `knowledge/`) are separate from their routes and take an
engine rather than a request. `egress.py` is a single place for the two boundaries that matter. The agent's tool
layer re-checks at the call what the route checked at the edge, which is why a workflow, an MCP client and a chat
all get the same answer.

Two observations, neither changed here because both are working as intended and changing them would be larger than
this pass:

- **Scoping is optional at the data layer.** `db.update(table, id, values)` scopes only when the caller passes
  `workspace_id=`. Making it required for the 42 workspace-scoped tables would turn a discipline into a structure.
  F-3's test is the cheaper half of that; the API change is a separate, reviewable piece of work.
- **`/health` answers `ok` without touching the database.** An uptime check on it proves the process is listening
  and nothing more. This sits inside `OPEN-RISKS` OP-2 (`/metrics` and alerting) rather than being a new finding.

## What could not be run here

Reported rather than skipped, because an audit that implies a scan it never ran is worse than one that admits it.

| Tool | Status |
|---|---|
| `ruff` (security rules, the set CI applies) | **Ran.** Clean on `desk_engine` |
| Engine test suite | **Ran.** See the pull request for the count |
| gitleaks, Trivy | Could not run: no Docker daemon in this environment. Both run in CI on every push |
| pip-audit, Bandit, Semgrep, OSV-Scanner | Could not run: no route to the package index from here. pip-audit and the ruff security set run in CI |
| `tsc --noEmit` | Could not run: `@types/react` is not installable offline. Runs in CI |

No scanner output is reproduced in this document, because none was produced here.

## Still open

Unchanged by this pass and still recorded in `OPEN-RISKS.md`: the owner has signed none of the accepted risks
(`FINAL-OWNER-RISK-ACCEPTANCE.md` is still pending), live integrations are validated against mocks only, there is
no external monitoring or metrics endpoint, and no independent penetration test has been performed. OP-13 — the
audit log living in the application database — is worth re-reading now that refusals are recorded in it, because
the log just became more interesting to an attacker who can reach the file.
