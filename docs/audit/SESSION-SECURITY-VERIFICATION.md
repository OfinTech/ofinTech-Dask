# Session security verification (automatic logout after inactivity)

## Scope

Server-enforced inactivity sign-out for the Team Server (30 minutes by default, 30–45 configurable), the warning
two minutes before, a user-friendly and bilingual way to stay signed in or leave, clean logout across tabs,
correct behaviour after sleep and disconnection, the desktop equivalent, and the session properties the brief
lists (fixation, reuse, cookies, CSRF, storage, absolute lifetime, falsified activity). Verified on `3d055c1`.

## Architecture reviewed

`web.authenticate` (session lookup, idle and lifetime checks, `last_seen` write only for activity),
`web.is_activity` (method-based: GET/HEAD/OPTIONS never count), `routes/auth.py` (login creates a fresh random
token and cookie; logout deletes the row; `/api/session`, `/api/session/ping`), `config.py` (the two knobs),
`ui/src/main.tsx` (the page's timer), `desktop/src/main.ts` + `preload.ts` (shell ↔ page).

## Changes implemented (`9e47e90`, `3d055c1`)

**Engine**
- `DESK_SESSION_IDLE_MINUTES` default **30**; on a Team Server values below 30 or above 45 (and 0) are pulled into
  30–45 with a logged warning — the check cannot be switched off on a server; the desktop keeps what it is given.
- `DESK_SESSION_DAYS` default **30**: a separate absolute lifetime; `0` removes it.
- `GET /api/session/status`: remaining idle and lifetime seconds **without** extending the session (a GET).
- `POST /api/session/ping` returns the same state after refreshing a **live** session; a session already over gets
  401 first — nothing revives it. Rate-limited.
- `/api/session` now carries `idle_minutes`, `warn_seconds` (120) and `lock_minutes` (desktop).
- Audit: `session.expired` (`idle` / `lifetime`), `session.logout` (`user` / `idle`), `user.login.failed`.

**Web page (`ui/src/lib/idle.ts`, `ui/src/main.tsx`)**
- A pure timer module: `idleState`, `secondsLeft`, `fromServer`, `mergeActivity`, `isUserActivity`, `tickMs`.
- Activity = `pointerdown`, `keydown`, `touchstart`, `wheel` only. Fetches, the 20-second notification poll, the
  event stream, `focus`, `visibilitychange`, timers and `storage` events are not activity. A ping is sent at most
  once a minute and only when there was activity since the last one.
- **Warning** at 2 minutes left (`alertdialog`, countdown): *"Your session will expire in 2 minutes due to
  inactivity."* / *"ستنتهي جلستك خلال دقيقتين بسبب عدم النشاط."* (also Spanish and French), with **Stay signed in**
  (one authenticated `POST /api/session/ping`; a 401 means it was already over and the sign-in screen follows) and
  **Log out now**. While the warning shows, ordinary clicks do not reset the timer; only the explicit choice does.
- **Automatic logout** at 0: `POST /api/auth/logout?reason=idle` (server row deleted, audited), `desk-signed-out`
  written to `localStorage` so **every other tab** signs out too, the dev token cleared, the sign-in screen with
  *"You were signed out because nothing happened for a while."* (translated).
- **Sleep / wake / reconnect / background**: on `visibilitychange`, `focus` and `online` the page asks
  `/api/session/status`; the engine's number can only **shorten** what the page believes (`fromServer`), never
  lengthen it; a 401 there shows the sign-in screen. Tabs share the newest activity through `localStorage`, and a
  value from the future is ignored.
- The manual *Sign out* in Settings also writes the shared key, so other tabs follow.

**Desktop (`desktop/src/main.ts`, `preload.ts`, `main.tsx`)**
- The desktop app has no accounts: the engine runs for this computer's user under a per-launch token, so there is
  no session to expire. The page therefore **locks** after the same window (`lock_minutes` = the engine's idle
  setting, 30 by default): every page is unmounted, nothing financial stays on screen, and a deliberate click on
  *Resume* brings it back. The shell (`powerMonitor`) sends `desk:lock` on lock-screen, suspend and shutdown so
  the page locks at once, and `desk:resume` on wake so the page re-reads the session. Documented difference: there
  is no credential to re-check on this computer, so the lock protects an unattended screen from a passer-by and
  not against someone who already holds the OS account (AR-5).

## Security requirements — status

| Requirement | Status | Where |
|---|---|---|
| Server-enforced timeout | yes | `authenticate`; the page's timer is a prediction only |
| Fixation | login always creates a new random 256-bit token and sets a new cookie; the old one is not reused | `create_session`, `test_session_inactivity.test_inactivity_signs_out…` (new token after re-login) |
| Reuse of expired sessions | the row is deleted; cookie, bearer and ping all 401 | same test |
| Cookie attributes | HttpOnly, Secure (https), SameSite=Strict, path `/` | `set_session_cookie` |
| CSRF | whole-origin check on every cookie-authenticated write incl. ping/logout | `check_origin`, OB-02 tests |
| No secrets in insecure storage | the cookie is HttpOnly; `localStorage` holds timestamps only; the dev token is `sessionStorage` on the desktop dev path only | `main.tsx`, `api.ts` |
| Multiple tabs | shared activity clock; shared sign-out key | `idle.test.mjs` (`mergeActivity`), `main.tsx` |
| Sleep/wake, suspended desktop | resync on visibility/focus/online; desktop lock on suspend, re-check on resume | `main.tsx`, `main.ts` |
| Temporary disconnection | the local countdown continues; on `online` the engine is asked; a failed ping is not a revival | `useSessionTimer` |
| Revalidate when active again | yes | `wake()` → `/api/session/status` |
| Server state authoritative | `fromServer` only shortens | `idle.test.mjs` |
| Unsaved work | drafts stay in component state until the sign-out; nothing sensitive is retained after it (the page reloads the session and renders the sign-in screen) | `signOut` |
| Background jobs do not extend sessions | they use no session (workflow runs, indexing, review run inside the engine) | by construction; `test_background_traffic…` for the page's GETs |
| Absolute lifetime | 30 days default | `test_absolute_lifetime…` |
| Client cannot falsify activity | nothing the page sends carries a timestamp; `last_seen` comes from the engine's clock; a dead session's ping is 401 | `test_client_cannot_extend_the_session_by_lying` |

## Mandatory automated tests — results

Engine (`tests/test_session_inactivity.py`, 18 tests: each case at 30 and at 45 minutes, with the engine's clock
replaced by a controllable one) and page (`ui/test/idle.test.mjs`, 6 tests, `npm test` in the UI step of CI):

| Test | Where | Result |
|---|---|---|
| Active user remains authenticated | engine `test_an_active_person_stays_signed_in` (3 windows, action every 10 min) | PASS |
| 30-minute inactivity causes logout | engine `test_inactivity_signs_out…` (29:55 in, 30:01 out, row deleted) | PASS |
| 45-minute configuration works | engine `FortyFiveMinutes` (same 9 cases at 45); `test_accounts.test_values_are_read_within_the_allowed_range` (45 kept, 15→30, 120→45, 0→30) | PASS |
| Warning 2 minutes before expiration | page `idleState` → `warning` at 28:00 (30) and 43:00 (45), `secondsLeft` 120 | PASS |
| Background polling does not extend | engine `test_background_traffic_does_not_extend…` (notifications, status, thread, session GETs at 28 min; sign-out at 30) | PASS |
| WebSocket heartbeats do not extend | **NOT APPLICABLE** — the product has no WebSocket; the SSE stream is covered below | — |
| AI streaming does not extend | engine `test_ai_streaming_does_not_extend…` (open stream, clock moved past the window, 401) | PASS |
| Expired session cannot call protected APIs | engine (datasets 401) | PASS |
| Expired session cannot execute AI tools | engine (`POST …/threads` 401, `GET …/tools` 401) | PASS |
| Expired session cannot access financial data | engine (`/query` 401, dataset 401, no values in the body) | PASS |
| Cannot be restored with an old token | engine (bearer reuse → 401; new login → new token) | PASS |
| Multi-tab logout synchronisation | page: shared sign-out key + `storage` listener (code); `mergeActivity` tested; the cross-tab event itself is browser behaviour — **NOT VERIFIED by an automated browser test** | partial |
| Desktop suspend/resume | shell → page IPC (code); `fromServer` after wake tested; the Electron event path — **NOT VERIFIED automatically** | partial |
| Network disconnect/reconnect | `online` → resync (code); `fromServer` tested; the browser event — **NOT VERIFIED automatically** | partial |
| Concurrent session behaviour | engine `test_concurrent_sessions_and_logout_are_independent` | PASS |
| Session revocation in audit logs | engine (`session.expired` idle/lifetime, `session.logout` user/idle) | PASS |
| Stay signed in verifies a live session only | engine `test_stay_signed_in_refreshes_only_a_live_session` | PASS |
| Forged activity ignored | engine `test_client_cannot_extend_the_session_by_lying` | PASS |

## Evidence references

Commits `9e47e90`, `3d055c1`; CI run 41284426822 (`test` job: engine 311 OK, UI typecheck + i18n + `npm test`
6/6; the only failure was the desktop typing fixed in `3d055c1`) and the final run in `FINAL-RELEASE-GATE.md`;
`docs/SECURITY.md` "Sessions" and "Desktop"; `docs/DEPLOYMENT.md`; `server/.env.example`; `CHANGELOG.md`.

## Remaining risks

- Three page behaviours (cross-tab `storage` event, Electron suspend/resume IPC, browser `online` event) are
  implemented and reviewed but not driven by an automated browser; the `e2e/ui_smoke.py` Playwright harness is
  the natural place to add them (OP-12).
- The desktop lock is a screen lock, not a re-authentication (documented).
- The idle window is enforced per request: a session idle for 31 minutes is removed when it next calls, not by a
  sweeper; until then the row exists but can do nothing.

## Final commit

`3d055c1`.
