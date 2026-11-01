# Embedded Agent Gateway — implementation plan and verification record

Baseline: `origin/main` `eada27077f436685f1df4878b5ec2165d2669c93`. Branch: `feature/embedded-agent-gateway`.
The plan below is the order the work is done in; the verification column is filled in as each phase lands and is
summarised again in the final report.

## Phase 1 — audit and design (done)

* Repository audit recorded in `ARCHITECTURE.md` §1 with the commit SHA.
* AG-UI event names verified against the published specification (`AG-UI-PROTOCOL-MAPPING.md`).
* Threat model with 20 threats, each with a named test (`SECURITY-THREAT-MODEL.md`).

## Phase 2 — gateway core (engine) — done

| Step | Files | Notes |
|---|---|---|
| Migration 21 | `db.py` | Four tables (`ARCHITECTURE.md` §2.4). Additive only. |
| Tool scope | `embedded/scope.py`, `agent/harness.py` | `state["tool_scope"]` is applied right after `tools.for_context()`; absent → unchanged behaviour for the Desk UI, workflows, channels and MCP. |
| Integration registration | `embedded/model.py`, `embedded/routes.py` (admin part) | Admin + fresh session; creates the viewer service user; key shown once; rotate/suspend/delete; sealed workspaces refused. |
| Sessions | `embedded/model.py`, `embedded/routes.py` | `POST /api/embedded/sessions` with the integration key; 15-minute tokens. |
| Auth, origin, rate limits | `embedded/auth.py` | Mirrors `mcp_server.RateLimited`; all refusals audited. |
| Metering + entitlements | `embedded/metering.py` | `embedded_usage` rows; `Entitlements` protocol with `LimitEntitlements`. |

## Phase 3 — AG-UI adapter — done

`embedded/agui.py` + `embedded/gateway.py`: `RunAgentInput` parsing, SSE encoding, Desk-event translation, interrupt
outcome, `resume` handling, re-attach and cancel endpoints, bounded buffers (engine queue limits), keep-alives.

## Phase 4 — SDK and generative UI — done

`sdk/embedded-agent/` (`@ofintech/embedded-agent`, TypeScript, React 19 peer dependency, esbuild like `ui/`):
`OfinTechAgentProvider`, `OfinTechFinancialCopilot`, `useOfinTechAgent`, `AgUiClient` (fetch + SSE parser),
component registry with ten components, ar/en strings, RTL via `dir`. `examples/copilotkit-embedded/` shows the same
endpoint through CopilotKit's `HttpAgent`. Server-side registry in `embedded/components.py` shares the JSON schemas
(`sdk/embedded-agent/schemas/v1/*.json`, copied into the engine package at build time — single source of truth).

## Phase 5 — wiring — done

`routes/__init__.py` mounts the embedded routers; `app.py` constructs `engine.embedded`; OpenAPI fragment
`docs/openapi/embedded.yaml`.

## Phase 6 — tests — done

New suites (no existing test changed, no golden fixture regenerated):

| Suite | Covers |
|---|---|
| `test_embedded_auth.py` | token lifecycle, origin, rate limits, admin registration, sealed workspaces, audit of refusals |
| `test_embedded_tenant.py` | two synthetic organisations, ignored client fields, thread isolation per subject |
| `test_embedded_approvals.py` | interrupt mapping, resume refusal for the service user, unknown/expired ids, argument change |
| `test_embedded_protocol.py` | AG-UI event sequence validity, component payload validation, disconnect cancels, re-attach replay |
| `test_embedded_financial.py` | read-only scope, payment refusal, bank-identifier masking, golden figures through the gateway equal the Desk UI's |
| `test_embedded_metering.py` | usage rows, daily limits, entitlement hook |
| `sdk/embedded-agent/test/*.test.mjs` | registry validation, no dangerous HTML, no external calls |
| `e2e/embedded_browser.py` | Playwright: a static partner page loads the built SDK against a real engine, asks a question, receives a `FinancialKpiCard` (run on demand; documented) |

Regression: the full `engine/tests` suite is run after the change and its result recorded in the final report.

## Phase 7 — documentation, PR, package — done

`docs/embedded/guides/` (14 guides), OpenAPI, CHANGELOG entry, PR against `main` (not merged), zip
`OfinTech-Embedded-Agent-Gateway-AGUI.zip` built from `git archive` of the branch (no `.env`, no secrets — the
packaging step greps for key patterns before zipping).

## Verification record

Engine: 370 passed, 0 failed (`python -m pytest engine/tests`; 54 new tests). SDK: 12 passed (`node --test`). Browser:
9 of 9 checks passed (`e2e/embedded_browser.py`; evidence in `docs/audit/evidence/embedded/`). `tsc --noEmit` for the
SDK was not run in the build environment (no `@types/react` offline). Details and limitations: `FINAL-REPORT.md`.
