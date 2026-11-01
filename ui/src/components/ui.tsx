import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../lib/api";
import { LANGUAGES, N, lang, setLang, tr, type Lang } from "../lib/i18n";

// ---- icons (1.6px strokes, 24px grid) ------------------------------------------------------------
const paths: Record<string, string> = {
  chat: "M4 5h16v11H8l-4 4z",
  dash: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  data: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  flow: "M6 3v6m0 0a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm0 6h6a6 6 0 0 1 6 6v6M18 15l-3 3m3-3 3 3",
  skill: "M4 4h11l5 5v11H4zM15 4v5h5M8 13h8M8 17h5",
  inbox: "M3 13h5l2 3h4l2-3h5M5 5h14l2 8v6H3v-6z",
  plug: "M9 2v5m6-5v5M6 7h12v4a6 6 0 0 1-12 0zm6 10v5",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.6-2-3.4-2.4 1a7.4 7.4 0 0 0-2.2-1.3L14.4 3h-4l-.3 2.4a7.4 7.4 0 0 0-2.2 1.3l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.6l-2 1.6 2 3.4 2.4-1a7.4 7.4 0 0 0 2.2 1.3l.3 2.4h4l.3-2.4a7.4 7.4 0 0 0 2.2-1.3l2.4 1 2-3.4-2-1.6c.1-.4.1-.9.1-1.3z",
  plus: "M12 5v14M5 12h14",
  send: "M4 12 20 4l-6 16-3-7z",
  stop: "M7 7h10v10H7z",
  x: "M6 6l12 12M18 6 6 18",
  team: "M16 20v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm13 9v-1a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  upload: "M12 16V4m0 0-5 5m5-5 5 5M4 20h16",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  trash: "M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3",
  link: "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1m-2 6a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  play: "M7 4l13 8-13 8z",
  edit: "M4 20h4L19 9l-4-4L4 16zm10-14 4 4",
  docs: "M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6M4 7v14h11",
  sheet: "M4 4h16v16H4zM4 9h16M4 14h16M10 4v16",
  memory: "M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V4.5A2.5 2.5 0 0 0 9 4zm6 0a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1",
  clip: "M21 11.5 12.4 20a5.5 5.5 0 0 1-7.8-7.8l8.9-8.9a3.7 3.7 0 0 1 5.2 5.2l-8.9 8.9a1.8 1.8 0 0 1-2.6-2.6L15.4 6.6",
  trend: "M3 17l6-6 4 4 8-8M15 7h6v6",
  centre: "M12 3a9 9 0 1 0 9 9h-9zM15 3.2A9 9 0 0 1 20.8 9H15z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm5-2 5 5",
  close: "M4 5h16v15H4zM4 9h16M8 3v4M16 3v4M9 14.5l2 2 4-4",
  pulse: "M2 12h4l3-7 4 14 3-7h6",
};

export function Icon({ name, size = 18 }: { name: string; size?: number }): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || ""} /></svg>
  );
}

const WORDMARK = "M360.31 3.25 L360.31 76.56 L427.00 76.56 L427.00 61.62 L426.62 61.00 L377.75 61.00 L376.19 60.38 L376.41 49.12 L376.41 47.75 L400.62 47.75 L400.62 32.88 L400.12 31.62 L376.75 31.62 L376.12 30.62 L376.12 19.75 L377.25 18.88 L426.62 18.88 L427.31 18.38 L427.31 3.25 Z M195.38 3.19 L195.38 76.81 L210.25 76.81 L211.12 76.25 L211.12 9.25 L212.00 8.12 L213.62 10.00 L240.00 76.12 L241.12 76.69 L271.62 76.69 L271.62 3.31 L255.94 3.31 L255.94 70.00 L255.25 71.62 L254.00 71.00 L227.12 3.75 L226.12 3.19 Z M154.29 3.38 L87.38 3.38 L87.38 76.56 L103.12 76.56 L103.12 48.88 L103.75 47.88 L127.12 47.88 L127.56 47.00 L127.56 31.75 L104.12 31.75 L103.38 30.62 L103.38 19.50 L104.75 18.88 L154.30 18.88 L154.29 16.12 Z M526.50 3.50 L526.50 76.50 L542.25 76.50 L542.25 48.62 L543.38 47.75 L586.25 47.75 L587.06 48.50 L587.06 76.50 L602.56 76.50 L602.56 3.00 L588.00 3.00 L587.00 3.50 L587.00 30.75 L586.25 31.69 L543.25 31.69 L542.25 30.62 L542.25 3.50 L541.12 2.88 L527.38 2.88 Z M440.38 3.50 L440.38 63.12 L441.50 67.88 L445.50 73.25 L449.38 75.75 L453.00 76.69 L514.38 76.69 L514.38 62.38 L513.75 61.00 L457.88 61.00 L456.75 60.25 L456.75 19.00 L513.75 19.00 L514.25 18.00 L514.25 3.00 L441.75 3.00 Z M282.75 3.25 L282.94 17.75 L282.94 19.00 L307.88 19.00 L307.88 76.56 L323.75 76.56 L323.75 19.75 L324.88 18.75 L349.31 18.75 L349.31 3.25 Z M2.06 3.50 L2.06 62.38 L3.00 67.38 L6.75 72.75 L11.00 75.75 L15.38 76.94 L74.62 76.94 L76.12 76.38 L76.12 17.00 L75.38 12.62 L71.25 6.75 L66.88 4.00 L63.38 2.94 L3.25 2.94 Z M18.44 19.00 L59.00 19.00 L60.00 19.62 L60.00 60.25 L59.38 60.94 L19.38 60.94 L18.44 60.38 Z M165.75 2.00 L164.88 2.75 L164.88 76.12 L165.62 76.88 L181.00 76.88 L181.75 76.25 L181.75 2.88 L181.00 2.00 Z";
const MONO_TRANSFORM = "translate(97.66 94.21) scale(4.0508)";
const MONO = "M2.06 3.50 L2.06 62.38 L3.00 67.38 L6.75 72.75 L11.00 75.75 L15.38 76.94 L74.62 76.94 L76.12 76.38 L76.12 17.00 L75.38 12.62 L71.25 6.75 L66.88 4.00 L63.38 2.94 L3.25 2.94 Z M18.44 19.00 L59.00 19.00 L60.00 19.62 L60.00 60.25 L59.38 60.94 L19.38 60.94 L18.44 60.38 Z";

/** The OFINTECH monogram (the "O" of the wordmark) on a rounded square. */
export function BrandMark(): ReactNode {
  return (
    <svg className="brand-mark" viewBox="0 0 512 512" aria-hidden="true">
      <rect width="512" height="512" rx="112" fill="var(--brand-tile)" />
      <path fill="var(--brand-tile-ink)" fillRule="evenodd" transform={MONO_TRANSFORM} d={MONO} />
    </svg>
  );
}

/** The OFINTECH wordmark in the current text colour. */
export function Wordmark({ height = 16 }: { height?: number }): ReactNode {
  return (
    <svg className="wordmark" viewBox="0 0 605.00 79.12" height={height} role="img" aria-label={tr("OFINTECH")}>
      <path fill="currentColor" fillRule="evenodd" d={WORDMARK} />
    </svg>
  );
}

// ---- router (hash based: works from file:// in the desktop app and over http) ---------------------
export function useRoute(): string[] {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const path = hash.replace(/^#\/?/, "").split("?")[0];
  return path ? path.split("/").map(decodeURIComponent) : [];
}

export function query(): URLSearchParams {
  return new URLSearchParams(location.hash.split("?")[1] || "");
}

export function go(path: string): void {
  location.hash = "#/" + path.replace(/^\//, "");
}

// ---- toasts ---------------------------------------------------------------------------------------
type Toast = { id: number; text: string; kind: "info" | "error" };
const ToastCtx = createContext<(text: string, kind?: "info" | "error") => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }): ReactNode {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "info" | "error" = "info") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, kind }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === "error" ? 8000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (Array.isArray(e.details) && e.details.length) {
      const extra = (e.details as { field: string; message: string }[]).filter((d) => d && !e.message.includes(d.message));
      return extra.length ? e.message + " " + extra.map((d) => `${d.field}: ${d.message}`).join("; ") : e.message;
    }
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

/** Wraps an async action: busy flag + error toast. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    setBusy(true);
    try {
      const r = await fn();
      if (ok) toast(ok);
      return r;
    } catch (e) {
      toast(errorText(e), "error");
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [toast]);
  return { busy, run };
}

/** Loads data on mount and when deps change. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | undefined; error: string | null;
  reload: () => void; loading: boolean } {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    setLoading(true);
    fn().then((d) => { if (alive.current) { setData(d); setError(null); } })
      .catch((e) => { if (alive.current) setError(errorText(e)); })
      .finally(() => { if (alive.current) setLoading(false); });
    return () => { alive.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

// ---- small components -----------------------------------------------------------------------------
export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode;
  footer?: ReactNode; wide?: boolean }): ReactNode {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head"><h2>{title}</h2>
          <button className="btn ghost small" onClick={onClose} aria-label={tr("Close")}><Icon name="x" /></button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }): ReactNode {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }): ReactNode {
  return <div className="empty"><h3>{title}</h3>{children && <p>{children}</p>}{action}</div>;
}

export function Loading(): ReactNode {
  return <p className="muted" role="status">{tr("Loading…")}</p>;
}

export function ErrorBox({ error }: { error: string | null }): ReactNode {
  return error ? <div className="notice error" role="alert">{error}</div> : null;
}

export function PageHead({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }): ReactNode {
  return <div className="page-head"><div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>{actions && <div className="row">{actions}</div>}</div>;
}

/** Status words shown by StatusTag (listed so they are translated). */
export const STATUS_WORDS = [N("done"), N("indexed"), N("via PipesHub"), N("processing"), N("connected"), N("approved"), N("ok"),
  N("running"), N("waiting"), N("waiting approval"), N("pending"), N("stopped"), N("failed"), N("error"), N("rejected"),
  N("expired"), N("remote"), N("needs ocr"), N("disconnected"), N("none"), N("ready")];

export function StatusTag({ status }: { status: string }): ReactNode {
  const cls: Record<string, string> = {
    done: "green", indexed: "green", "via PipesHub": "blue", processing: "blue", connected: "green", approved: "green", ok: "green", running: "blue", waiting: "blue",
    waiting_approval: "amber", pending: "amber", stopped: "", failed: "red", error: "red", rejected: "red", expired: "",
  };
  return <span className={`tag ${cls[status] ?? ""}`}>{tr(status.replace(/_/g, " "))}</span>;
}

export function Confirm({ text, onYes, onClose, yes = "Delete" }: { text: string; onYes: () => void; onClose: () => void;
  yes?: string }): ReactNode {
  return (
    <Modal title={tr("Are you sure?")} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>{tr("Cancel")}</button>
      <button className="btn danger" onClick={() => { onYes(); onClose(); }}>{tr(yes)}</button></>}>
      <p>{text}</p>
    </Modal>
  );
}

/** Choose the interface language; signed-in people keep the choice on their account. */
export function LanguagePicker({ compact = false }: { compact?: boolean }): ReactNode {
  const current = lang();
  async function pick(l: Lang) {
    try { await api("PATCH", "/api/me", { language: l }); } catch { /* signed out: this browser only */ }
    setLang(l);
  }
  return compact ? (
    <select className="select lang-select" aria-label="Language / اللغة" value={current} onChange={(e) => void pick(e.target.value as Lang)}>
      {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
    </select>
  ) : (
    <div className="seg" role="radiogroup" aria-label="Language / اللغة">
      {LANGUAGES.map((l) => <button key={l.code} lang={l.code} aria-pressed={current === l.code} onClick={() => void pick(l.code)}>{l.name}</button>)}
    </div>
  );
}
