// Pure decisions the main process makes about URLs and IPC senders. Kept free of Electron so that
// desktop/test/guards.test.mjs can run them under plain Node (npm test), and so that each rule is read in one place.

export const PROTOCOL = "ofintech-desk";

/** The app's own origin: http://localhost:<port> for the local engine, or the Team Server's https origin. */
export function isAppUrl(url: string, appOrigin: string): boolean {
  try {
    return appOrigin !== "" && new URL(url).origin === appOrigin;
  } catch {
    return false;
  }
}

/** The two bundled pages that may use the setup bridge (connect / offline), loaded from the app's own files. */
export function isSetupPage(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "file:" && /\/static\/(connect|offline)\.html$/.test(u.pathname);
  } catch {
    return false;
  }
}

/** Frames that may talk to the main process at all. */
export function trustedSender(url: string | undefined | null, appOrigin: string): boolean {
  return isAppUrl(url || "", appOrigin) || isSetupPage(url || "");
}

/** Only http(s) links ever leave the app (to the system browser). */
export function isOpenableExternally(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:";
  } catch {
    return false;
  }
}

/** A Team Server address a person may connect to. */
export function isAcceptableServerUrl(url: unknown): url is string {
  return typeof url === "string" && /^https:\/\/[^\s/]+/.test(url);
}

/** ofintech-desk://open/<route>?ws=…: the route becomes the page's hash and nothing else; anything outside the
 *  allowed characters, or any other host or scheme, is dropped. */
export function routeFromDeepLink(raw: string): string | null {
  try {
    const u = new URL(raw);
    if (u.protocol !== `${PROTOCOL}:` || u.hostname !== "open") return null;
    const path = u.pathname.replace(/^\/+/, "");
    if (!/^[A-Za-z0-9_\-/]{1,200}$/.test(path)) return null;
    const params = new URLSearchParams();
    for (const key of ["ws", "page", "message", "cell"]) {
      const v = u.searchParams.get(key);
      if (v && /^[A-Za-z0-9_\-!. &]{1,120}$/.test(v)) params.set(key, v);
    }
    const q = params.toString();
    return `#/${path}${q ? `?${q}` : ""}`;
  } catch {
    return null;
  }
}

/** ofintech-desk://open/auth?handoff=<code>: the one-time code from the Google sign-in in the system browser. */
export function handoffCode(raw: string): string | null {
  try {
    const u = new URL(raw);
    if (u.protocol !== `${PROTOCOL}:` || u.hostname !== "open" || u.pathname.replace(/^\/+/, "") !== "auth") return null;
    const code = u.searchParams.get("handoff") || "";
    return /^[A-Za-z0-9_-]{20,100}$/.test(code) ? code : null;
  } catch {
    return null;
  }
}
