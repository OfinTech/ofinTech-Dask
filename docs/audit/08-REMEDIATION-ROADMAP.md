# 08 — Remediation roadmap and implementation report

Living document: updated with every change the audit makes. Priorities: P0 critical, P1 high, P2 medium, P3 low
(definitions in the engagement brief). A finding moves to *done* only when its regression test passes in the full
suite and the change is on `main`.

## Status at a glance (1 November 2026)

| Round | Findings | Commits | Tests added | State |
|---|---|---|---|---|
| R1 | OB-01 (P1), OB-02 (P2) | 769e263 | 4 | done |
| R2 | OB-03 (P1), OB-15 (P3) | d409034 | 6 | done |
| R3 | OB-07 (P2), OB-11 (P2) | 1435e17 | 3 | done |
| R4 | OB-04 (P2), OB-05 (P1), OB-10 (part), OB-13, OB-14 (P3) | 87e0196 | 7 (+1 rewritten) | done |
| R5 | OB-08 (P2, two of three parts) | 78644c1 | 2 | done; one owner decision open |
| R6 | OB-17 (P2), OB-18 (part), audit documents | this commit | — (CI-side) | done; CI run pending |
| R7 | OB-06 parser limits, Caddy body limit, OB-19 masker false positive | d5b6ba0, this commit | 3 | done |
| R8 | OB-09 skills gate, AI security suite + doc 06, dependency bumps, `/mcp` rate limit | e705701, 8e1b39c, fa391df, 76496d2 | 14 | done; delimiting → AR-2/OP-1 |
| R9 | DB-02/03, performance measurements | c9138a0, 76496d2 | — | done; `/metrics`, retention, load run → OP-2/OP-3 |
| G1 | Decisions: OB-08 effects, OB-10 fingerprint re-approval | 3fa9da4 | 3 (+1 rewritten) | done |
| G2 | Encrypted backups with key file, restore and rehearsal (DB-01) | 6110d9a | 5 | done |
| G3 | Lockfiles via CI workflow, `npm ci` everywhere; desktop IPC rules + tests (OB-12) | e30b1b1, 68770e0, 2c7773c | 7 (desktop) | done |
| G4 | Adversarial + isolation suites; WH-01, WF-01, NEW-01/02, invisible-prefix fix | 34fac0e, 055e67b, 75e14d5, f2ebc46 | 19 | done |
| G5 | Final reports: assurance, readiness, threat model, open risks | this commit | — | done |
| R10 | Threat model (03), DB review (04), API review (05), AI testing (06), target architecture (07), readiness (10) | this commit | — | done; readiness is a living document |

Engine suite at the final gate: **269 tests, all passing** (182 before the audit; 86 added, 2 rewritten for the intended OB-05/OB-08 changes); desktop guard tests 7/7. CI on `main`: every job green incl. Trivy; the `security` job green on f2ebc46.

## Implementation record

Each entry: reason, components, compatibility, verification.

### R1 — 769e263 · Accept only Zoho's own hosts in the OAuth callback, and compare origins whole
- **Why:** a prefix check let an outside host receive the authorisation code and client secret (OB-01); the CSRF
  origin guard accepted look-alike origins (OB-02).
- **Components:** `connectors/flows.py` (new `zoho_accounts_server`, `zoho_api_domain`, `ZOHO_API_DOMAINS`),
  `web.py` (`request_origin`, `check_origin`), `routes/public.py` (`_allowed_origin`).
- **Compatibility:** none affected; genuine Zoho callbacks and same-origin requests behave as before (existing
  tests `ZohoFlow`, `TeamServer`, `SiteAuth` unchanged and passing).
- **Verification:** `OB01ZohoCallback`, `OB02OriginCheck` fail on the previous code and pass now; full suite OK.

### R2 — d409034 · Keep imported text as text in the close pack, journal and CSV exports
- **Why:** formula injection through ledger text (OB-03); unhandled parser errors (OB-15).
- **Components:** new `files/cells.py`; `close/pack.py`, `files/xlsx.py`, `routes/datasets.py`, `files/expr.py`,
  `analysis/formula.py`.
- **Compatibility:** files look the same; the only visible change is that a cell that *was* a formula by accident
  is now the text the ledger held. Engine totals stay formulas.
- **Verification:** `OB03FormulaInjection` (4), `OB15FormulaEngineRobustness` (2); `test_close`, `test_files`,
  `test_data`, `test_financial_analysis` unchanged.

### R3 — 1435e17 · Hold body limits whatever Content-Length says, and re-check open event streams
- **Components:** `web.py` (`read_body`, `BodyTooLarge`, `MAX_JSON_BODY`), `routes/public.py` (all hooks),
  `routes/threads.py` (`KEEP_ALIVE_SECONDS`, `still_allowed`).
- **Compatibility:** the same limits apply, now always; a stream now closes within one keep-alive (15 s) of a
  sign-out or removal, and the UI reconnects and gets the 401/404 it expects.
- **Verification:** `OB07BodyLimits` (2), `OB11StreamReauthorisation` (direct ASGI test, since the test client
  buffers streams).

### R4 — 87e0196 · Judge outbound hosts at every request, and stop reading unencrypted files silently
- **Components:** `egress.py` (`PrivateNetworkGuard`, `_CGNAT`), `security.py`, `app.py` (installs the guard in
  server mode; `_storage_hosts`), `storage.py` (`allow_plaintext`, `encrypt_in_place`), `config.py`,
  `__main__.py` (`storage-encrypt`), `routes/mcp.py` (`public_url`), `channels/service.py`, `mail.py`,
  `docs/DEPLOYMENT.md`, `docs/SECURITY.md`.
- **Compatibility — read before deploying:**
  1. Team Servers that reach an **internal** model, PipesHub or embedding service by a private address must list
     that host in `DESK_EGRESS_ALLOW` (or run sealed). Public endpoints and the S3 endpoint need nothing.
  2. Installations that stored files **before** `DESK_STORAGE_ENCRYPTION` was on must run
     `python -m desk_engine storage-encrypt` once (or set `DESK_STORAGE_ALLOW_PLAINTEXT=true` meanwhile).
  3. `DESK_SMTP_SECURITY` values other than `starttls`/`ssl`/`none` now stop the engine at start with a clear
     message, as do `none` + a login.
- **Verification:** `OB04ConnectionTimeNetworkPolicy` (3), `OB10MCPAuditURL`, `OB13TelegramTokenNeverInErrors`,
  `OB14MailNeverLogsInInClear`, `test_storage` (rewritten case); full suite OK.

### R5 — 78644c1 · Check tool arguments against their schema, and give every tool call a time limit
- **Components:** `agent/tools.py` (`validate_args`, `DEFAULT_TOOL_TIMEOUT`, `Tool.timeout`), `agent/connector_tools.py`.
- **Compatibility:** a model that sends a wrong type now gets an error it can correct instead of a handler
  exception; no test script needed changes, which suggests current providers already send correct types.
- **Verification:** `OB08AgentIsolation` (2); full suite OK.

### R6 — this commit · CI security gates, container hardening, audit documents
- **Components:** `.github/workflows/build.yml` (`permissions`, `security` job, Trivy step, dependencies),
  `.github/workflows/desktop-cloud.yml` (`permissions`), `server/docker-compose.yml` (`cap_drop`,
  `no-new-privileges`, tmpfs), `docs/audit/02,08,09,10`, `CHANGELOG.md`.
- **Compatibility:** none for the product. The first CI run may surface advisories in pinned dependencies or the
  base image; those are findings to fix or to accept below, not reasons to loosen the gate.
- **Verification:** workflow and compose files parse; ruff's S rules pass locally with the listed exceptions; the
  Docker build and the scanners run in CI (no registry access in the audit environment).

### R7 — d5b6ba0, this commit · Parser resource limits, masker false positive
- **Why:** zip bombs and oversized images reached the parsers (OB-06); the IBAN masker ate engine ids that
  happened to look like IBANs (OB-19, seen as a flaky CI failure in `test_chat_attachments`).
- **Components:** new `files/limits.py`; `knowledge/extract.py` (zip check, OCR budget), `knowledge/ocr.py`,
  `files/editable.py`, `data/importer.py`, `costcentres/budget.py`, `egress.py` (masker), `server/Caddyfile`.
- **Compatibility:** genuine files are unaffected (limits are far above any real workbook or document); a scanned
  PDF that takes more than 15 minutes of OCR is indexed partially instead of failing.
- **Verification:** `OB06ParserLimits` (2), `OB19MaskingKeepsEngineIds`; full suite 206 OK; `test_data`'s
  garbage-file contract kept (non-zip data is left to each parser's own error).

### R8 (part 1) — e705701, 8e1b39c, fa391df · Skills gate, dependency updates, AI security suite
- **Components:** `skills/loader.py`, `routes/skills.py`, `docs/SKILLS.md`; `engine/requirements.txt`,
  `pyproject.toml` (Pillow 13.0.0, pypdf 6.21.4 from Dependabot's security group); `tests/test_ai_security.py`,
  `docs/audit/06-AI-SECURITY-TESTING.md`; `.gitleaks.toml`; Trivy step.
- **Compatibility:** an editor's new skill now starts off (admins turn it on); a custom skill cannot take a
  built-in's name (the UI already names copies `<name>-custom`).
- **Verification:** `OB09SkillsNeedAnAdmin`; 13 AI security tests; full suite 220 OK; CI security job green.

### R9 (part 1) — 76496d2, this commit · MCP rate limit, WAL checkpoint, IPC senders, heavy work off the loop
- **Components:** `mcp_server.py` (`_AuthWrapper` limiter), `db.py` (`checkpoint`), `app.py` (scheduler),
  `desktop/src/main.ts` (`trustedSender`, `isSetupPage`), `routes/datasets.py`, `agent/data_tools.py`.
- **Compatibility:** none visible; MCP clients above 120 requests a minute get 429.
- **Verification:** `OB18MCPEndpointRateLimit`; full suite 221 OK; desktop typecheck in CI; benchmark in document 10
  (200k rows: 2.7 s commit, queries ≤ 110 ms, `/health` < 1 ms afterwards).

### V1–V3 — 5354641, 34fac0e, this commit · Verification: baseline, oracles, adversarial tests, assurance report
- **Components:** `tests/golden_financial.py`, `tests/fixtures/financial_golden.json` (generated by the pre-audit
  engine), `tests/test_financial_regression.py`, `tests/test_adversarial.py`, `files/cells.py` (invisible-prefix
  hardening), `webhooks._clean` (credential fragments, URL queries), `docs/audit/SECURITY-ASSURANCE-REPORT.md`.
- **Evidence:** pre-audit suite 181/182 against the current code (the 1 is OB-05's intended change); golden
  financial output byte-identical; 12 oracle tests; pre-audit data directory opened by the current engine; full
  suite 247 OK ×4.
- **Found and closed by the adversarial pass:** WH-01 (credential keys not on the list, URL query tokens) and the
  zero-width/BOM prefix before "=" in exports.

## Accepted security exceptions

| Exception | Owner | Justification | Expires |
|---|---|---|---|
| ruff `S101` (assert) in the engine | repository owner | asserts are type-narrowing after `authenticate`/`load_workspace`, never input validation; Python runs with asserts on | 2027-05-01 (review) |
| ruff `S608` (string-built SQL) at 36 sites | repository owner | every site interpolates engine identifiers (`ds_<id>`, `c0..cN`, table names from fixed maps) or `?` placeholders; reviewed in document 02 | 2027-05-01 (re-review on new sites) |
| ruff `S110` (try/except/pass) ×5, `S603` (subprocess) ×2, `S314` (stdlib XML on S3 responses) ×2 | repository owner | best-effort cleanup paths; Tesseract runs without a shell and with a timeout; S3 XML comes from the operator's own store | 2027-05-01 |
| Multipart uploads rely on Starlette's disk spool and a post-read size check | audit | spool goes to disk, not memory; Caddy now caps bodies at 260 MB at the edge | 2027-05-01 (review) |

## Decisions needed from the owner

None open. The four decisions were resolved at the final gate by the review acting as architect and are recorded,
with their reasoning, in `SECURITY-ASSURANCE-REPORT.md` (OB-05, OB-08, OB-10, DB-01) and `OPEN-RISKS.md`; the
owner may overturn any of them by reopening the item there.
