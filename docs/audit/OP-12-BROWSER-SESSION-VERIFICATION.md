# OP-12 — browser and desktop session verification

## Scope

The session inactivity policy as a person experiences it: a real browser (Chromium through Playwright) driving
the built interface served by real engine processes — a Team Server at 30 minutes, a Team Server at 45 minutes,
and the desktop edition — with isolated synthetic data (an account, a workspace, a one-row ledger with a
recognisable figure). Time is controlled on both sides so no scenario waits for real minutes: the page's clock
(`page.clock`) is moved forward, and the session's `last_seen` in the engine's database is moved back by the same
amount, which is exactly what the engine compares against its own clock.

OP-12 was opened because three page behaviours (cross-tab sign-out, resync after sleep/reconnection, the desktop
lock) were implemented but only unit-tested. This run drives all of them, and the rest of the policy, end to end.

## How it runs

`e2e/session_browser.py`. It is a step of the CI `test` job on every push (`build.yml`, after the smoke test), and
the `session-evidence` workflow runs it on demand and commits the JSON summary and screenshots to
`docs/audit/evidence/session/` so this report points at files in the repository, not at an expiring artifact.
The evidence below is the committed run on `e02220c` (workflow run 41294821631, commit `ff77743`).

Clock control: `page.clock.install()` once per page; `advance(ms)` = `UPDATE sessions SET last_seen = last_seen - ms/1000`
+ `page.clock.fast_forward(ms)`; the countdown uses `page.clock.run_for`. Two tabs share one installed clock.

## Evidence

Commit `e02220c` · Chromium (Playwright for Python 1.63, headless) · 26 scenarios: **22 passed, 0 failed, 4 not verified**.

| Test id | Environment | Expected | Actual | Result | Screenshot |
|---|---|---|---|---|---|
| S0-config | Team Server | idle 30 min, warning 120 s, lifetime 12 h, step-up 10 min | idle_minutes=30 warn_seconds=120 session_hours=12 reauth_minutes=10 | **PASS** | — |
| S1-visible | Team Server, Chromium | ledger figure visible while signed in | 98,765.43 rendered | **PASS** | [s1-before.png](evidence/session/s1-before.png) |
| S1-logout | Team Server, Chromium | at 30 min: login screen with the idle notice; figure gone; protected API 401 | login form shown=True notice=True figure_gone=True api=401 | **PASS** | [s1-after.png](evidence/session/s1-after.png) |
| S1-audit | Team Server | sign-out recorded with the reason | [('session.expired', '{"reason":"idle","idle_minutes":30}')] | **PASS** | — |
| S2-en | Team Server, Chromium, en-GB | warning at 28:00 with the English text and a countdown that falls | shown=True countdown 'Time left: 1:58' → 'Time left: 1:48' dir=ltr | **PASS** | [s2-en.png](evidence/session/s2-en.png) |
| S2-keyboard | Team Server, Chromium | Stay signed in has focus; Enter sends the ping; the dialog closes; the engine's last_seen moves | focused='Stay signed in' last_seen moved=True dialog closed=True | **PASS** | [s2-keyboard.png](evidence/session/s2-keyboard.png) |
| S2-ar | Team Server, Chromium, Arabic | the Arabic warning in a right-to-left layout | arabic=True dir=rtl=True | **PASS** | [s2-ar.png](evidence/session/s2-ar.png) |
| S2-tab | Team Server, Chromium | both buttons reachable with the keyboard | after Shift+Tab focus on 'btn ghost' | **PASS** | — |
| S3-stay | Team Server, Chromium | Stay signed in → POST /api/session/ping → engine refreshes last_seen; status shows ~30 min left | last_seen moved=True idle_seconds_left=1799 dialog=False | **PASS** | [s3-stay.png](evidence/session/s3-stay.png) |
| S3-revive | Team Server, Chromium | a session already over on the engine is not revived by Stay signed in: 401 and the login screen | background read saw 401; ping=401 login form=True | **PASS** | [s3-revive.png](evidence/session/s3-revive.png) |
| S3-revoked | Team Server, Chromium | a revoked session cannot be renewed | ping=401 | **PASS** | — |
| S4-background | Team Server, Chromium | polling, status checks, the chat page and its stream, reads: last_seen unchanged; ~2 min left | last_seen unchanged=True idle_seconds_left=119 | **PASS** | [s4-background.png](evidence/session/s4-background.png) |
| S4-logout | Team Server, Chromium | …and the sign-out still comes at 30:00 | login screen | **PASS** | [s4-logout.png](evidence/session/s4-logout.png) |
| S4-websocket | n/a | WebSocket heartbeats do not extend the session | the product has no WebSocket; its event stream is a GET (covered above) | **NOT VERIFIED** | — |
| S4-jobs | Team Server | scheduled jobs / background sync do not extend sessions | engine jobs use no session (by construction; test_session_inactivity, test_session_policy) | **PASS** | — |
| S5-logout-propagates | Team Server, Chromium, 2 tabs | sign-out in one tab signs the other out and clears its figures | tab2 login form shown, figure gone=True | **PASS** | [s5-tab2.png](evidence/session/s5-tab2.png) |
| S5-expiry-sync | Team Server, Chromium, 2 tabs | both tabs sign out at 30 min; the second cannot revive (401); no figure in either | tab1 login=True tab2 login=True ping=401 figure in tab2=False | **PASS** | [s5-expiry.png](evidence/session/s5-expiry.png) |
| S6-offline-local | Team Server, Chromium (offline) | past the limit while offline the page signs itself out without waiting for the network | login form, figure gone | **PASS** | [s6-offline.png](evidence/session/s6-offline.png) |
| S6-offline | Team Server, Chromium | offline past the limit: no cached access; on reconnection the engine is asked and the login screen follows; API 401 | while offline: login form, figure gone; after reconnect: login form; api=401; figure gone=True | **PASS** | [s6-online.png](evidence/session/s6-online.png) |
| S6-wake | Team Server, Chromium | after waking, the page re-checks with the engine and shows the login screen; nothing cached is shown | login form; figure gone=True | **PASS** | [s6-wake.png](evidence/session/s6-wake.png) |
| S1-45min | Team Server (45 min), Chromium | idle_minutes=45; no warning at 42; warning at 43; sign-out at 45 | idle_minutes=45 no_warning_at_42=True warning_at_43=True signed_out_at_45=True | **PASS** | [s1-45.png](evidence/session/s1-45.png) |
| S7-lock | Desktop UI (engine in desktop mode, Chromium; not Electron) | lock_minutes=30; after 30 min the lock screen replaces every page and the figure is gone | lock_minutes=30 idle_minutes=0 lock shown, figure hidden=True | **PASS** | [s7-lock.png](evidence/session/s7-lock.png) |
| S7-resume | Desktop UI, Chromium | Resume (keyboard) brings the workspace back | figure visible again | **PASS** | [s7-resume.png](evidence/session/s7-resume.png) |
| S7-electron-minimize | Electron | minimize and restore keep the policy | Electron is not launched in this run (no display/installed shell here); the page logic is the same as S7-lock | **NOT VERIFIED** | — |
| S7-electron-sleep | Electron | OS sleep/resume locks at once and re-checks on resume | powerMonitor → desk:lock/desk:resume wiring reviewed in code; not driven here | **NOT VERIFIED** | — |
| S7-electron-reauth | Electron | re-authentication after expiry | the desktop edition has no accounts: a screen lock, not a credential re-check (documented in SECURITY.md) | **NOT VERIFIED** | — |

Log (engine, browser, console errors, results):

```
09:50:40 engine (server, 30 min) at http://127.0.0.1:54903; Chromium 153.0.8010.12 (Playwright, headless)
09:50:41 S0-config: PASS — idle_minutes=30 warn_seconds=120 session_hours=12 reauth_minutes=10
09:50:41 S1-visible: PASS — 98,765.43 rendered
09:50:41 S1-logout: PASS — login form shown=True notice=True figure_gone=True api=401
09:50:41 S1-audit: PASS — [('session.expired', '{"reason":"idle","idle_minutes":30}')]
09:50:42 S2-en: PASS — shown=True countdown 'Time left: 1:58' → 'Time left: 1:48' dir=ltr
09:50:42 S2-keyboard: PASS — focused='Stay signed in' last_seen moved=True dialog closed=True
09:50:42 S2-ar: PASS — arabic=True dir=rtl=True
09:50:42 S2-tab: PASS — after Shift+Tab focus on 'btn ghost'
09:50:43 S3-stay: PASS — last_seen moved=True idle_seconds_left=1799 dialog=False
09:50:43 S3-revive: PASS — background read saw 401; ping=401 login form=True
09:50:44 S3-revoked: PASS — ping=401
09:50:44 S4-background: PASS — last_seen unchanged=True idle_seconds_left=119
09:50:44 S4-logout: PASS — login screen
09:50:44 S4-websocket: NOT VERIFIED — the product has no WebSocket; its event stream is a GET (covered above)
09:50:44 S4-jobs: PASS — engine jobs use no session (by construction; test_session_inactivity, test_session_policy)
09:50:45 S5-logout-propagates: PASS — tab2 login form shown, figure gone=True
09:50:46 S5-expiry-sync: PASS — tab1 login=True tab2 login=True ping=401 figure in tab2=False
09:50:46 S6-offline-local: PASS — login form, figure gone
09:50:46 S6-offline: PASS — while offline: login form, figure gone; after reconnect: login form; api=401; figure gone=True
09:50:47 S6-wake: PASS — login form; figure gone=True
09:50:49 S1-45min: PASS — idle_minutes=45 no_warning_at_42=True warning_at_43=True signed_out_at_45=True
09:50:50 S7-lock: PASS — lock_minutes=30 idle_minutes=0 lock shown, figure hidden=True
09:50:50 S7-resume: PASS — figure visible again
09:50:50 S7-electron-minimize: NOT VERIFIED — Electron is not launched in this run (no display/installed shell here); the page logic is the same as S7-lock
09:50:50 S7-electron-sleep: NOT VERIFIED — powerMonitor → desk:lock/desk:resume wiring reviewed in code; not driven here
09:50:50 S7-electron-reauth: NOT VERIFIED — the desktop edition has no accounts: a screen lock, not a credential re-check (documented in SECURITY.md)
```

## Findings during the run (fixed before this evidence)

1. **A shared activity timestamp from the future kept the page from signing itself out.** Found when the second
   tab's clock ran ahead: the page clamped a future `localStorage` value to "now" and treated it as fresh activity,
   so a forged or skewed entry could have suppressed the *page's* own sign-out indefinitely (the engine's expiry
   was never affected — the next request was still refused). Such a value is now ignored (`mergeActivity`,
   `readSharedActivity`, 5 s of skew tolerated); unit-tested in `ui/test/idle.test.mjs`.
2. **A freshly loaded page trusted its own clock.** After a reload the page assumed activity "now"; it now asks
   `GET /api/session/status` on load and takes the engine's remaining time (the warning then comes at the right
   moment after a reload too).
3. **Offline past the limit, the page waited for the network before signing out.** The page now drops its session
   state at once and tells the engine when it can; verified by S6-offline-local.
4. The warning now counts down every second (it previously updated with the 15-second timer tick).

## Scenario coverage against the brief

| Required | Covered by |
|---|---|
| 1 Automatic inactivity logout: default 30; 30–45 configurable; backend-enforced; APIs 401; redirect to login; data gone | S0-config, S1-visible/logout/audit, S1-45min |
| 2 Warning two minutes before; countdown; Arabic and English; RTL/LTR; keyboard | S2-en, S2-ar, S2-keyboard, S2-tab |
| 3 Stay signed in: extends a valid session server-side; expired not revived; revoked not renewed | S3-stay, S3-revive, S3-revoked |
| 4 Background activity: dashboard refresh, polling, AI streaming, WebSocket, sync, scheduled jobs | S4-background (poll, status, stream, reads), S4-logout, S4-jobs; WebSocket NOT APPLICABLE (none in the product) |
| 5 Multiple tabs: logout propagates; expiry synchronised; no revival; data concealed | S5-logout-propagates, S5-expiry-sync |
| 6 Network: offline does not bypass; reconnection validates; sleep/wake; no cached access | S6-offline-local, S6-offline, S6-wake |
| 7 Electron: minimize/restore, OS sleep/resume, lock, reauth, visibility after expiry | S7-lock, S7-resume (the desktop UI in Chromium); S7-electron-* **NOT VERIFIED** (no Electron automation in CI; see OP-14) |

## OP-12 status

**CLOSED — VERIFIED** for every browser scenario (22 passed, 0 failed, reproducible in CI on every push). The
three Electron-shell scenarios that this run cannot drive are recorded as **NOT VERIFIED** and carried as
**OP-14** in `OPEN-RISKS.md`; the desktop *page* behaviour they rely on (lock and resume) is verified here.

## Remaining risks

- Electron's own process (minimize/restore, `powerMonitor` sleep/resume IPC) is not automated; the wiring is
  reviewed in `desktop/src/main.ts` and `preload.ts` (OP-14).
- The desktop lock is a screen lock, not a re-authentication (documented in `docs/SECURITY.md`).
- Headless Chromium only; Firefox and WebKit are not run (the page uses no browser-specific API).

## Final commit

Evidence on `e02220c`; see `FINAL-RELEASE-GATE.md` for the release commit and the CI run that re-executes these
tests on it.
