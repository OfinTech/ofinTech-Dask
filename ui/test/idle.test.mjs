// Session inactivity decisions, with a clock we control (no browser, no engine):
//   node --experimental-strip-types --test "test/*.test.mjs"
import assert from "node:assert/strict";
import { test } from "node:test";

import { fromServer, idleState, isUserActivity, mergeActivity, msLeft, secondsLeft, tickMs, WARN_MS } from "../src/lib/idle.ts";

const MIN = 60_000;
const T0 = 1_700_000_000_000;

test("an active person stays signed in; the warning comes 2 minutes before the end; then the session is over", () => {
  const limit = 30 * MIN;
  assert.equal(idleState(T0 + 10 * MIN, T0, limit), "active");
  assert.equal(idleState(T0 + 27 * MIN + 59_000, T0, limit), "active");
  assert.equal(idleState(T0 + 28 * MIN, T0, limit), "warning");           // 28 minutes idle: warn
  assert.equal(secondsLeft(T0 + 28 * MIN, T0, limit), 120);
  assert.equal(idleState(T0 + 29 * MIN + 59_000, T0, limit), "warning");
  assert.equal(idleState(T0 + 30 * MIN, T0, limit), "expired");           // 30 minutes idle: sign out
  assert.equal(secondsLeft(T0 + 31 * MIN, T0, limit), 0);
  assert.equal(WARN_MS, 2 * MIN);
});

test("a 45-minute policy warns at 43 and ends at 45", () => {
  const limit = 45 * MIN;
  assert.equal(idleState(T0 + 42 * MIN + 59_000, T0, limit), "active");
  assert.equal(idleState(T0 + 43 * MIN, T0, limit), "warning");
  assert.equal(idleState(T0 + 45 * MIN, T0, limit), "expired");
});

test("activity resets the countdown; background traffic has no event type that counts", () => {
  const limit = 30 * MIN;
  const last = T0 + 25 * MIN;                                            // a click at minute 25
  assert.equal(idleState(T0 + 29 * MIN, last, limit), "active");
  assert.equal(msLeft(T0 + 29 * MIN, last, limit), 26 * MIN);
  for (const ev of ["pointerdown", "keydown", "touchstart", "wheel"]) assert.equal(isUserActivity(ev), true, ev);
  for (const ev of ["fetch", "message", "focus", "visibilitychange", "online", "timer", "scroll", "mousemove", "storage"]) {
    assert.equal(isUserActivity(ev), false, ev);
  }
});

test("the engine's view of the remaining time wins over the page's, in the safe direction only", () => {
  const limit = 30 * MIN;
  const now = T0 + 40 * MIN;
  // the laptop slept: the page remembers a click at minute 38 of a stale clock, the engine says 3 minutes are left
  assert.equal(fromServer(now, 180, limit, T0 + 38 * MIN), now - 27 * MIN);
  // the engine knows of more recent activity (another device): the page keeps its own, earlier value — the page can
  // only ever shorten what it believes, never lengthen it
  assert.equal(fromServer(now, 1700, limit, T0 + 5 * MIN), T0 + 5 * MIN);
  // no number (desktop app, no session): unchanged
  assert.equal(fromServer(now, null, limit, T0), T0);
  assert.equal(fromServer(now, undefined, limit, T0), T0);
});

test("several tabs share the newest activity; a value from the future is ignored, not trusted", () => {
  const now = T0 + 10 * MIN;
  assert.equal(mergeActivity(now, T0, T0 + 8 * MIN), T0 + 8 * MIN);        // the other tab was used later
  assert.equal(mergeActivity(now, T0 + 9 * MIN, T0), T0 + 9 * MIN);        // this tab was
  assert.equal(mergeActivity(now, T0, now + 99 * MIN), T0);                // a forged or skewed future value: ignored
  assert.equal(mergeActivity(now, T0, now + 2_000), now);                  // two seconds of skew: tolerated
  assert.equal(mergeActivity(now, now + 99 * MIN, T0), now);               // this tab's own value never exceeds now
});

test("the clock is looked at often enough for a 2-minute warning to be on time", () => {
  assert.ok(tickMs(30 * MIN) <= 15_000);
  assert.ok(tickMs(45 * MIN) <= 22_500 && tickMs(45 * MIN) <= 15_000);
  assert.equal(tickMs(60_000), 1_000);
});
