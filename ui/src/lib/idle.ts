/**
 * Session inactivity: the pure decisions behind the warning, the sign-out and the desktop lock.
 *
 * The engine is the authority: it keeps `last_seen` per session, counts only requests that change something
 * (never the page's polling, the event stream or a refresh) and ends the session 30 to 45 minutes after the last
 * such request. The page's job is to predict that moment, warn two minutes before it, give the person a way to
 * stay signed in (one authenticated POST, which the engine refuses if the session has already ended) and to sign
 * out cleanly when the moment comes. Several tabs share the last activity through localStorage, and after a sleep,
 * a reconnection or a long time in the background the page asks the engine how long is really left instead of
 * trusting its own clock.
 *
 * Everything here is a function of timestamps, so it is tested without a browser (ui/test/idle.test.mjs).
 */

export const WARN_MS = 120_000;

/** DOM events that mean a person is doing something. Timers, fetches and streams are not events of this kind. */
export const ACTIVITY_EVENTS: readonly string[] = ["pointerdown", "keydown", "touchstart", "wheel"];

export type IdleState = "active" | "warning" | "expired";

export function isUserActivity(eventType: string): boolean {
  return ACTIVITY_EVENTS.includes(eventType);
}

/** Milliseconds until the engine signs this session out, given the last activity it knows of. */
export function msLeft(now: number, lastActivity: number, limitMs: number): number {
  return Math.max(0, lastActivity + limitMs - now);
}

export function idleState(now: number, lastActivity: number, limitMs: number, warnMs: number = WARN_MS): IdleState {
  const left = msLeft(now, lastActivity, limitMs);
  if (left <= 0) return "expired";
  if (left <= warnMs) return "warning";
  return "active";
}

/** Whole seconds shown in the countdown (never negative). */
export function secondsLeft(now: number, lastActivity: number, limitMs: number): number {
  return Math.ceil(msLeft(now, lastActivity, limitMs) / 1000);
}

/** The last activity implied by what the engine reports (`idle_seconds_left`). The engine's number wins over the
 *  page's own memory: a laptop that slept keeps an old clock, a tab in the background may have missed events, and
 *  the page can never make the engine believe the person was active more recently than it knows. */
export function fromServer(now: number, idleSecondsLeft: number | null | undefined, limitMs: number,
                           localLast: number): number {
  if (idleSecondsLeft === null || idleSecondsLeft === undefined) return localLast;
  const implied = now - (limitMs - idleSecondsLeft * 1000);
  return Math.min(localLast, implied);
}

/** The newest activity among this tab and the shared value, never in the future. */
export function mergeActivity(now: number, own: number, shared: number): number {
  return Math.min(now, Math.max(own, shared));
}

/** How often the page should look at the clock: often enough that a 2-minute warning is never late. */
export function tickMs(limitMs: number): number {
  return Math.max(1_000, Math.min(15_000, limitMs / 120));
}
