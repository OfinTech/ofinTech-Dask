# Final session security policy

## Scope

The session architecture of OfinTech Desk reviewed as a whole — inactivity timeout, absolute lifetime, token and
cookie handling, revocation, step-up authentication for high-risk actions, background credentials, and the
desktop edition — with the decisions taken, the code that implements them (`03bdb3b` and later) and the tests
that prove them. Values are security defaults chosen for a financial application; none is a regulatory
requirement.

## Architecture reviewed

`web.authenticate` / `session_lifetime` / `is_privileged` / `Ctx.require_fresh`, `routes/auth.py` (sign-in,
sign-up, Google callback, logout, reauth, sessions list/revoke/sign-out-everywhere, admin sign-out, password
change/reset), `config.py` (knobs and clamps), the `sessions` table (migration 20 adds `reauth_at`), the page
(`ui/src/lib/idle.ts`, `main.tsx` session timer, warning and step-up dialogs, `api.ts` retry-after-confirm),
the desktop shell (`desktop/src/main.ts` `powerMonitor`, `preload.ts`), MCP keys, ingest keys and webhook
secrets as non-session credentials.

## Final configuration

| Control | Final value | Env | Range on a Team Server |
|---|---|---|---|
| Inactivity timeout | **30 min** | `DESK_SESSION_IDLE_MINUTES` | 30–45 (clamped; never off) |
| Warning before sign-out | **2 min** | fixed (`warn_seconds` = 120) | — |
| Absolute lifetime, interactive session | **12 h** | `DESK_SESSION_HOURS` | 1–24 |
| Absolute lifetime, administrative user (admin/owner of any workspace, or instance admin) | **8 h** | `DESK_ADMIN_SESSION_HOURS` | 1–24, effectively ≤ `DESK_SESSION_HOURS` |
| Step-up freshness window for high-risk actions | **10 min** | `DESK_REAUTH_MINUTES` | 1–60 |
| Desktop lock after inactivity | 30 min (same knob) | — | — |

## A. Inactivity timeout — kept as implemented and verified

Server-side (`authenticate` compares `last_seen` with its own clock and deletes the row), only state-changing
requests count as activity (GET/HEAD/OPTIONS never do), the page warns at 28:00 and signs every tab out at
30:00. Verified by `tests/test_session_inactivity.py` (18, at 30 and 45) and in a real browser
(`OP-12-BROWSER-SESSION-VERIFICATION.md`).

## B. Absolute session lifetime — decision

**Decision: 12 hours for interactive sessions, 8 hours for anyone with administrative rights anywhere, judged at
every request from the current memberships.** The previous 30-day lifetime was a backstop against a stolen token
on a laptop that stays active; for a financial workspace a token that can be used for a month is too long, and a
working day bounds the exposure of a stolen cookie to hours while costing an active person one sign-in a day.

Why judged at every request rather than stored at sign-in: a person promoted to admin during a session gets the
shorter lifetime at once (a promotion is exactly when a stale long session matters), and nobody keeps a 12-hour
session by being promoted after signing in. The stored `expires_at` (12 h) remains as the row's own cap.

Compatibility: `DESK_SESSION_DAYS` from earlier releases is still read (days × 24, clamped to 24 h on a server,
with a warning), so an existing install starts; the desktop edition has no sessions and is untouched; Google
sign-in sessions follow the same rule; background work never used sessions. Documented in `docs/DEPLOYMENT.md`,
`server/.env.example`, `CHANGELOG.md`.

Tested: `test_session_policy.test_lifetimes_twelve_hours_and_eight_for_administrators` (owner out at 8 h 1 s while
active throughout; editor in until 12 h; promotion at hour 7 ends the editor's session at hour 8);
`test_accounts.SessionCap`, `SessionSettings` (defaults, clamps, legacy knob).

## C. Session revocation — verified paths

| Trigger | Behaviour | Test |
|---|---|---|
| Logout | that session's row deleted; cookie cleared; other devices untouched; audited `session.logout` | `test_session_policy.test_every_revocation_path` (1), `test_session_inactivity` |
| Idle / lifetime expiry | row deleted on the next request; `session.expired` with the reason | `test_session_inactivity`, `test_session_policy` |
| Account disabled | every request 401 on the next call (`authenticate` joins `disabled = 0`) | `test_rbac.test_revocation…`, `test_session_policy` (5) |
| Removed from a workspace | session lives; that workspace 404 on the next request; pending approvals expired | `test_rbac`, `test_session_policy` (6), live run step 7 |
| Permissions revoked (demotion) | next write 403; approvals of that person expired; workflow runs stop; admin lifetime may now apply | `test_rbac`, `test_approvals_and_audit`, `test_session_policy` |
| Administrator terminates a session | `POST /api/admin/users/{id}/sign-out` by the instance administrator, after a fresh password; all of that account's sessions deleted; `session.revoked` by `instance_admin` | `test_session_policy` (3) |
| Person ends their own other sessions | `GET /api/auth/sessions` (opaque ids, current flagged), `DELETE /api/auth/sessions/{id}`, `POST /api/auth/sign-out-everywhere` | `test_session_policy` (2) |
| Password changed or reset | every session of the person deleted; a fresh one issued | `test_session_policy` (4); `routes/auth.change_password`, `reset` |
| Credentials of integrations change | connections, OAuth apps, MCP servers and providers are high-risk routes (step-up); they do not end sessions, which is right: they are workspace credentials, not the person's | `test_session_policy` (step-up) |

## D. Token and cookie security — review

| Item | Finding |
|---|---|
| Secure / HttpOnly | `set_session_cookie`: `httponly=True`, `secure` when the public URL is https (a Team Server refuses an http public URL unless `DESK_ALLOW_HTTP=true`, a test-only switch), `samesite="strict"`, `path="/"` |
| CSRF | whole-origin check on every cookie-authenticated non-GET (OB-02), incl. ping, logout, reauth |
| Fixation | sign-in and sign-up always create a new 256-bit random token and set a new cookie; an attacker-planted cookie is never adopted |
| Identifier rotation | at sign-in, sign-up, password change/reset (new session); not on step-up (the session stays, `reauth_at` moves) — acceptable: the token never changes hands |
| Token expiry | idle (30–45 min), lifetime (12 h / 8 h), both server-side; cookie `max-age` = lifetime |
| Refresh tokens | none exist; the session is the only credential and is extended only by activity within its lifetime |
| Server-side invalidation | rows deleted on every revocation path above; nothing is "logically" expired yet usable |
| Multi-device | sessions are per device; listed and revocable; the lifetime and idle rules apply per session |
| Browser storage | cookie HttpOnly; `localStorage` holds timestamps and a sign-out marker only; the desktop dev token lives in `sessionStorage` on the desktop dev path only; no token in `localStorage`, ever |
| Bearer path | the same token may be sent as `Authorization: Bearer` (desktop and API clients); AR-9 (token in login JSON) stays open for the owner |

No insecure storage was introduced.

## E. Financial action protection — step-up

**Implementation:** `POST /api/auth/reauth` (password; rate-limited like a sign-in; `user.reauth` /
`user.reauth.failed` audited) stamps `sessions.reauth_at`; sign-in sets it too. Routes declared `fresh=True`
(or calling `ctx.require_fresh()`) answer **403 `reauth_required`** unless `reauth_at` is within
`DESK_REAUTH_MINUTES`. The page shows a password dialog in place and resends the request once on success; a
Google-only account is told to sign in again (which also stamps `reauth_at`). Step-up is checked **after** the
role check and **before** the handler; it never replaces RBAC, resource scoping or action-level approval.

**High-risk operations covered**

| Category of the brief | Routes |
|---|---|
| Bank / accounting integration credentials | connect (`authorize`, `odoo`, `custom`, `telegram`, `whatsapp`), disconnect, ingest-key rotation, OAuth app settings |
| User permissions | member role change, member removal (of others), workspace deletion |
| Exporting highly sensitive data | an MCP access key (a long-lived credential that exports data to another tool) |
| Modifying financial records | closing/reopening or deleting a month-end close; deleting a dataset |
| Approving sensitive AI agent actions | approving any approval whose tool leaves the workspace (sends, messages, workflow runs, MCP tools, imports) — `edit_file` and workflow approval steps are internal and do not ask |
| Security settings | egress policy, review settings, MCP servers (add/change/delete), model providers (add/delete), administrator reset links and session termination |

Not covered on purpose: ordinary reads, chat, dataset imports, memory, dashboards, file generation (the approval
and role controls apply), the desktop edition (no password; the OS account stands for the person — documented).

Tested: `test_session_policy.test_high_risk_actions_need_a_recent_password` (11 routes refused at minute 11 with
`reauth_required`, records unchanged; ordinary writes pass; wrong password refused and audited; right password
opens a 10-minute window; desktop never asks), `test_approving_an_outbound_action_needs_a_recent_password`.

## F. Compatibility — impact

| Area | Impact |
|---|---|
| Existing web authentication | unchanged protocol; a person signs in once a day instead of once a month; high-risk actions ask for the password at most every 10 minutes |
| Electron desktop | no sessions; unaffected by lifetimes and step-up; gains the inactivity lock |
| AI agent execution | runs are independent of the requester's session; approvals now additionally need a fresh approver for outbound tools |
| Background jobs | never used sessions; MCP keys, ingest keys and webhook secrets open only their own endpoints (`test_background_credentials_are_not_sessions`) |
| Open Banking / accounting integrations | connecting or disconnecting asks for the password; syncing and reading do not |
| Reconciliation and close workflows | preparing, matching and deciding items unchanged; closing a month asks for the password |
| Existing user data | migration 20 adds one nullable column; sessions created before the upgrade have no `reauth_at` and simply ask for the password once |
| Existing tests | 326 engine tests pass; the live two-tenant run and the browser run pass |

## Remaining risks

- Step-up does not rotate the session id (acceptable; noted above).
- Google-only accounts re-authenticate by signing in again rather than in place (documented, slightly heavier).
- AR-9 (token in the login JSON) is for the owner to accept or remove.

## Final commit

See `FINAL-RELEASE-GATE.md` for the release commit and its CI run; the policy code is in `03bdb3b` and `b1374ed`.
