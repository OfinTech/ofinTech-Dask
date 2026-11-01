// API client. In the desktop app the preload script exposes `window.desk` with the per-launch
// token of the local engine; on the Team Server the session cookie is used instead.

export type DeskBridge = {
  token?: string;
  version?: string;
  openExternal?: (url: string) => void;
  mode?: "local" | "remote";
  onLock?: (cb: () => void) => void;      // desktop: the computer was locked or put to sleep
  onResume?: (cb: () => void) => void;    // desktop: the computer woke up
};

declare global {
  interface Window { desk?: DeskBridge }
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function devToken(): string | undefined {
  try {
    const m = /token=([A-Za-z0-9_-]{32,})/.exec(location.hash);
    if (m) {
      sessionStorage.setItem("desk-token", m[1]);
      history.replaceState(null, "", location.pathname + location.search);
      return m[1];
    }
    return sessionStorage.getItem("desk-token") || undefined;
  } catch {
    return undefined;
  }
}

const token = window.desk?.token || devToken();

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...(extra || {}) };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

/** The engine has ended the session. The app listens for this and goes back to the sign-in screen, so a
 *  tab whose own idle timer never ran (discarded, restored, or stopped by an error) cannot sit there
 *  looking signed in while every request is refused. */
export const SIGNED_OUT_EVENT = "desk-signed-out";

function announceSignedOut(code: string): void {
  try {
    window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: code }));
  } catch { /* no window: nothing is listening anyway */ }
}

async function handle(resp: Response): Promise<any> {
  if (resp.status === 204) return null;
  const type = resp.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await resp.json() : await resp.text();
  if (!resp.ok) {
    if (typeof body === "object" && body) {
      const err = new ApiError(resp.status, body.error || "error", body.message || "Request failed.", body.details);
      if (resp.status === 401) announceSignedOut(err.code);
      throw err;
    }
    if (resp.status === 401) announceSignedOut("unauthorized");
    throw new ApiError(resp.status, "error", String(body).slice(0, 300) || `HTTP ${resp.status}`);
  }
  return body;
}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      method,
      headers: headers(body !== undefined ? { "Content-Type": "application/json" } : {}),
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "offline", "The OfinTech Desk engine is not reachable.");
  }
  return handle(resp);
}

export const get = <T = any>(p: string) => api<T>("GET", p);
export const post = <T = any>(p: string, b?: unknown) => api<T>("POST", p, b ?? {});
export const put = <T = any>(p: string, b?: unknown) => api<T>("PUT", p, b ?? {});
export const patch = <T = any>(p: string, b?: unknown) => api<T>("PATCH", p, b ?? {});
export const del = <T = any>(p: string) => api<T>("DELETE", p);

export async function upload<T = any>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
  const form = new FormData();
  for (const [k, v] of Object.entries(fields)) form.append(k, v);
  form.append("file", file);
  const resp = await fetch(path, { method: "POST", body: form, headers: headers(), credentials: "same-origin" });
  return handle(resp);
}

export async function download(path: string, filename: string): Promise<void> {
  const resp = await fetch(path, { headers: headers(), credentials: "same-origin" });
  if (!resp.ok) await handle(resp);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Server-sent events over fetch (so the Authorization header can be sent). */
export function subscribe(path: string, onEvent: (ev: any) => void): () => void {
  const ctrl = new AbortController();
  let stopped = false;
  (async () => {
    let delay = 500;
    while (!stopped) {
      try {
        const resp = await fetch(path, { headers: headers(), signal: ctrl.signal, credentials: "same-origin" });
        if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
        delay = 500;
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx: number;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const data = chunk.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("\n");
            if (data) {
              try { onEvent(JSON.parse(data)); } catch { /* ignore malformed */ }
            }
          }
        }
      } catch {
        if (stopped) return;
      }
      await new Promise((r) => setTimeout(r, delay));
      delay = Math.min(delay * 2, 8000);
    }
  })();
  return () => { stopped = true; ctrl.abort(); };
}

export function openExternal(url: string): void {
  if (!/^https?:\/\//i.test(url)) return;
  if (window.desk?.openExternal) window.desk.openExternal(url);
  else window.open(url, "_blank", "noopener,noreferrer");
}

export async function uploadMany<T = any>(path: string, files: File[], fields: Record<string, string> = {}): Promise<T> {
  const form = new FormData();
  for (const [k, v] of Object.entries(fields)) form.append(k, v);
  for (const f of files) form.append("files", f);
  const resp = await fetch(path, { method: "POST", body: form, headers: headers(), credentials: "same-origin" });
  return handle(resp);
}
