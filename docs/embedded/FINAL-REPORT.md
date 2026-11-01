# Embedded Agent Gateway + AG-UI adapter — final report (MVP)

Branch `feature/embedded-agent-gateway`, built on `origin/main` `eada27077f436685f1df4878b5ec2165d2669c93`.
The implementing commit's SHA is recorded in the pull request and in the delivery note (it does not exist while this
file is being written, so it is not pasted here to avoid a wrong value).

## What was delivered

| Area | Deliverable |
|---|---|
| Design | `docs/embedded/ARCHITECTURE.md` (repository audit with findings, trust model, data model), `AG-UI-PROTOCOL-MAPPING.md` (event names verified against the AG-UI specification on 2026-11-01), `SECURITY-THREAT-MODEL.md` (20 threats, each with scenario, entry point, impact, mitigation, test, residual risk), `IMPLEMENTATION-PLAN.md` |
| Engine | `engine/desk_engine/embedded/` — `model.py` (integrations, sessions, threads), `auth.py` (token resolution, origin allow-list, rate limits, audited refusals), `scope.py` (per-run read-only tool scope), `agents.py` (three read-only agents), `agui.py` (AG-UI adapter), `gateway.py` (run orchestration, re-attach, cancel, resume), `metering.py` (usage + entitlement hook), `components.py` (server-side component registry), `routes.py` (public + admin API). Migration 22. 1 930 lines. |
| Changes to existing code | 24 changed lines in 5 files: `agent/harness.py` (`state_extra`, apply `tool_scope`), `app.py` (construct `engine.embedded`), `routes/__init__.py` (mount), `routes/workspaces.py` (service user stays viewer), `tests/test_backup.py` (schema version 20 → 21, the same one-line bump every migration makes). No existing test was weakened or removed; no golden fixture regenerated. |
| SDK | `sdk/embedded-agent/` — `@ofintech/embedded-agent` (TypeScript, React 19 peer, esbuild): `OfinTechAgentProvider`, `OfinTechFinancialCopilot`, `useOfinTechAgent`, `AgUiClient`, registry with 10 components and v1 schemas, ar/en, RTL. 1 083 lines. |
| Example | `examples/copilotkit-embedded/` — partner backend that mints sessions, SDK panel and CopilotKit `HttpAgent` side by side. |
| Docs | 14 developer guides in `docs/embedded/guides/`, OpenAPI `docs/openapi/embedded-gateway.json`, CHANGELOG entry. |
| Evidence | `docs/audit/evidence/embedded/report.md` + screenshot from the browser verification. |

## Verification record

| Suite | Result | Command |
|---|---|---|
| Full engine suite (existing + new) | **407 passed, 0 failed** after merging `main` `4fc2240` (Owner Control Center); 54 new | `python -m pytest engine/tests` |
| `test_embedded_auth.py` | 17 passed | token lifecycle, origins, rate limits, registration, sealed workspaces, audit |
| `test_embedded_tenant.py` | 6 passed | two synthetic organisations, ignored client fields, per-subject threads, private in Desk |
| `test_embedded_approvals.py` | 8 passed | interrupt outcome, service user cannot decide, unknown/expired ids, payload validation, cancel, Desk editor decides + re-attach, argument change detected |
| `test_embedded_protocol.py` | 11 passed | sequence validity, input validation, component validation, disconnect cancels, cancel + re-attach replay, audit, schema parity with the SDK, only published event names |
| `test_embedded_financial.py` | 9 passed | read-only scope, tampered scope falls closed, golden figures through the gateway equal the engine, editor-only tools not offered, injection cannot reach outbound tools, payment refused, bank identifiers masked, no hidden model fallback |
| `test_embedded_metering.py` | 3 passed | usage rows and summary, daily limit, entitlement hook |
| SDK (`node --test`) | 12 passed | registry validation, unknown version fallback, schema caps, no `innerHTML`/`eval`, single network destination, no runtime dependencies, SSR render of all 10 components in ar and en, RTL, no `javascript:` link |
| Browser (`e2e/embedded_browser.py`, Playwright + real engine process) | 9 passed | RTL panel, streamed answer, tool badge, component rendered, no token in web storage, no console errors, unknown origin refused, session token useless on Desk's API, run audited and metered |
| SDK `tsc --noEmit` | **not run** in the build environment (no `@types/react` available offline); the bundle builds with esbuild and the runtime tests render every component. Run `npm install && npm run typecheck` before publishing. |

## Success criteria from the brief

| Criterion | Status |
|---|---|
| Repository audited before coding, with the commit SHA | done (`ARCHITECTURE.md` §1, `eada270`) |
| Gateway `POST /api/embedded/agents/{agent_id}/run`, integration registration, deny by default, no trust in client tenant/user/role | done; tests `test_embedded_auth`, `test_embedded_tenant` |
| AG-UI adapter with the standard events, state, approvals via the standard interrupt mechanism, versioned extension, no invented event names | done; `test_embedded_protocol::test_event_type_strings_are_the_published_ones` |
| Reuse of the existing approvals engine (claim/digest/expiry) | done; no second decision path exists; `test_embedded_approvals` |
| First release read-only | done; every shipped agent's scope is read-only and egress-free, asserted in `test_embedded_financial` |
| Capabilities Ask Your Finance / read-only reconciliation / Financial Intelligence | done as `ask_finance`, `reconciliation_read`, `financial_intelligence` (three agents) |
| Generative UI via a controlled registry, versioned schemas, ar/en, RTL, no generated code executed | done; 10 components; static scans in the SDK tests |
| React SDK with `<OfinTechAgentProvider endpoint tokenProvider><OfinTechFinancialCopilot/>` and a CopilotKit example | done |
| Private AI only, no external LLM fallback | done; the workspace's configured provider is the only one used; `test_no_external_model_fallback` |
| Threat model with 20 threats | done |
| Regression protection; new suites incl. two synthetic orgs and browser | done; 370 engine tests green |
| Observability, usage metering, entitlement interface (no billing) | done; audit actions, `embedded_usage`, `Entitlements` |
| Developer docs (14) + OpenAPI | done |
| Zip without secrets, PR not merged, report with SHA | zip built from `git archive` of the branch after a secret scan; PR opened; SHA in the delivery note |

## Limitations and honest notes

1. **Not production-ready by this work alone.** No load test, no independent penetration test of the new surface, a
   single-process engine; see the residual risks in `SECURITY-THREAT-MODEL.md` and the open items in
   `docs/audit/OPEN-RISKS.md`. Nothing here claims PDPL, SAMA, ZATCA or any certification.
2. **Approvals never occur in this release** because no embedded agent has a write tool; the interrupt path is proven
   only with a test-only write agent.
3. **No Desk UI page for integrations** — the admin API is complete, the settings screen is not built.
4. **`reconciliation-review` is a scope over existing tools** (invoices, datasets, calculators); Desk has no
   dedicated bank-reconciliation engine, so "read-only reconciliation" means reviewing open, overdue and dataset rows.
5. **Component mapping is structural**: a table whose columns do not match a known shape becomes a
   `FinancialDataTable`; charts appear only when date + revenue/expense or cash columns are present.
6. **The CopilotKit example is illustrative**: it depends on third-party packages whose APIs move between releases
   and is not part of the test suite.
7. **Browser verification** runs against a fake model on localhost origins; the live-integration risk AR-3 applies.
8. **Rate limits are in-process memory**: they reset on restart and are per engine process.
