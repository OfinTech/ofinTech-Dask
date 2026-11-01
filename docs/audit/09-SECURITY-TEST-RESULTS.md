# 09 — Security test results

Updated with every round. All runs are against throw-away engine instances with mocked LLM, accounting, Google,
Telegram and S3 endpoints; nothing touched a live system.

## Run of 1 November 2026 (after R9, part 1)

Environment: Linux, Python 3.13.18, SQLite 3.x, Tesseract 5 (eng), no LibreOffice. Package registries were not
reachable, so the installed versions are the preinstalled ones (starlette 1.7.0, pydantic 2.13.5, httpx2 2.13.1,
cryptography 50.0.1, pypdf 6.17.0; the rest as pinned). CI re-runs the same suite with the pinned versions.

| Suite | Result |
|---|---|
| `python -m unittest discover -s tests -t .` | **221 tests, OK, ~21 s** |
| `tests/test_audit_regressions.py` (new) | 26 tests, OK; each class was also run against the pre-fix code and fails there |
| `tests/test_ai_security.py` (new, doc 06) | 13 tests, OK |
| `tests/test_security_boundaries.py` (existing) | 26 tests, OK (payments, sealed workspace/server, masking, socket- and DNS-level blocking) |
| `tests/test_team_server.py`, `test_site_auth.py`, `test_api_desktop.py` | OK (CSRF, isolation, SSRF at save time, local token and Host guard) |
| `ruff check --select S --ignore S101,S110,S314,S603,S608 desk_engine` | 0 findings |
| `node ui/scripts/i18n.mjs` | ar/es/fr 1,477/1,477, 0 missing |
| Mermaid diagrams in `docs/audit/01` | 4/4 render with `mmdc` |

Not run here (no registry or Docker access): UI/desktop `tsc --noEmit`, Playwright smoke test, Docker image
build, Trivy. CI results on `main` (run for 443eca5): `security` green (gitleaks over the whole history after
allow-listing six documented fixtures, ruff S rules, pip-audit on the pinned requirements, npm audit), `test`
green (engine, UI typecheck and build, browser smoke test, desktop typecheck), `team-server-image` green (Docker
build + Trivy, CRITICAL/HIGH, fixed only).

## Controlled probes (reproductions, all local)

| Finding | Probe | Before | After |
|---|---|---|---|
| OB-01 | callback with `accounts-server=https://accounts.zoho.attacker.test` and a valid state | token request sent to the attacker host | 400, no request |
| OB-02 | `POST /api/workspaces` with cookie and `Origin: https://desk.example.com.attacker.test` | 200 | 403 `bad_origin` |
| OB-03 | close journal with account name `=HYPERLINK(...)` | formula cell / raw CSV | text cell / `'`-prefixed CSV |
| OB-04 | request to `http://169.254.169.254/`, `http://100.64.0.1/`, `http://[::ffff:10.0.0.8]/` through the server transport | sent | `EgressBlocked` before any connection |
| OB-05 | plaintext object under `documents/` | served as genuine | `storage_plaintext` error until migrated |
| OB-07 | chunked POST to `/api/ingest/...` over the limit | read in full, then 413 | 413 after the limit bytes |
| OB-08 | tool call with `{"n": "21"}` for an integer parameter; handler sleeping 5 s with a 0.2 s limit | handler got a string; waited | schema error; stopped with an error |
| OB-06 | 16 MB-inflating, 60 KB workbook; EPS bytes; PNG header declaring 100 MP | parsed / decoded | `document_too_large` / refused before decoding |
| OB-11 | member removed while an event stream is open, then an event published | event delivered | stream closed, nothing delivered |

## What the test suite now covers that it did not

- A look-alike OAuth host, a look-alike origin, formula prefixes in every exporter, a lying `Content-Length`, a
  removed member on an open stream, a private address at connection time (12 notations), a plaintext object,
  wrong-typed tool arguments, a hanging tool, a Telegram transport failure, SMTP modes, hostile formulas.

## Gaps still to cover (see roadmap R7–R8)

- A larger malicious document corpus (3,000-page PDFs, pathological single pages) under a time budget; zip bombs and EPS-as-PNG are covered.
- A reviewer-manipulation case (cell text addressing the file reviewer) for the AI suite.
- Performance: concurrent chat turns, a 400k-line ledger import, SQLite contention on the event loop.
