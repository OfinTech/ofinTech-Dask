import { StrictMode, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

try {
  const theme = localStorage.getItem("desk-theme");
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
} catch { /* storage unavailable */ }
import { ApiError, SIGNED_OUT_EVENT, get, post, setReauthHandler } from "./lib/api";
import { AppCtx, can, type Session, type Workspace } from "./lib/app";
import { BrandMark, ErrorBox, Icon, ToastProvider, Wordmark, errorText, go, useRoute } from "./components/ui";
import { AuthPage, InvitePage, ResetPage } from "./pages/Auth";
import { Onboarding } from "./pages/Onboarding";
import { ChatPage } from "./pages/Chat";
import { DashboardsPage } from "./pages/Dashboards";
import { DataPage } from "./pages/Data";
import { DocumentsPage } from "./pages/Documents";
import { FilesPage } from "./pages/Files";
import { MemoryPage } from "./pages/Memory";
import { CostCentresPage } from "./pages/CostCentres";
import { AnalysisPage } from "./pages/Analysis";
import { ClosePage } from "./pages/Close";
import { SearchPage } from "./pages/Search";
import { WorkflowsPage } from "./pages/Workflows";
import { SkillsPage } from "./pages/Skills";
import { InboxPage } from "./pages/Inbox";
import { ConnectionsPage } from "./pages/Connections";
import { SettingsPage } from "./pages/Settings";
import { OverviewPage } from "./pages/Overview";
import { FinancePage } from "./pages/Finance";
import { LedgerPage } from "./pages/Ledger";
import { OversightPage } from "./pages/Oversight";
import { N, lang, onLang, setLang, tr, type Lang } from "./lib/i18n";
import { ACTIVITY_EVENTS, fromServer, idleState, isUserActivity, mergeActivity, secondsLeft, tickMs, type IdleState } from "./lib/idle";

const NAV: [string, string, string][] = [
  ["overview", N("Overview"), "pulse"], ["finance", N("Statements"), "ledger"], ["search", N("Search"), "search"], ["chat", N("Assistant"), "chat"],
  ["dashboards", N("Dashboards"), "dash"], ["analysis", N("Financial analysis"), "trend"], ["costcentres", N("Cost centres"), "centre"], ["close", N("Month-end close"), "close"],
  ["ledger", N("General ledger"), "book"], ["oversight", N("Oversight"), "shield"], ["data", N("Data"), "data"], ["files", N("Files"), "sheet"], ["documents", N("Documents"), "docs"],
  ["workflows", N("Workflows"), "flow"], ["memory", N("Memory"), "memory"], ["skills", N("Skills"), "skill"], ["inbox", N("Inbox"), "inbox"],
];
const NAV2: [string, string, string][] = [["connections", N("Connections"), "plug"], ["settings", N("Settings"), "gear"]];

/** One application, three views (docs/DESIGN.md). A view only chooses what the rail shows; the server enforces
 *  permissions on every request whatever the view. The default follows the member's role; the person can switch. */
type View = "executive" | "finance" | "accounting";
const VIEWS: [View, string, string][] = [["executive", N("Executive"), N("Executive overview")],
  ["finance", N("Finance"), N("Finance analysis")], ["accounting", N("Accounting"), N("Accounting operations")]];
const VIEW_NAV: Record<View, string[]> = {
  executive: ["overview", "chat", "dashboards", "inbox", "search"],
  finance: ["overview", "finance", "analysis", "costcentres", "dashboards", "chat", "search", "close", "oversight", "data", "files", "documents", "inbox"],
  accounting: NAV.map(([k]) => k),
};
function defaultView(role: Workspace["role"]): View {
  return role === "viewer" ? "executive" : role === "editor" ? "accounting" : "finance";
}
function storedView(wsId: string): View | null {
  try {
    const v = localStorage.getItem(`desk-view:${wsId}`);
    return v === "executive" || v === "finance" || v === "accounting" ? v : null;
  } catch { return null; }
}

function storedWs(): string | null {
  try { return localStorage.getItem("desk-ws"); } catch { return null; }
}

// Every tab of the app writes here, so working in one tab counts for all of them. Without it a tab left
// untouched beside a busy one would reach its own idle limit and sign the person out of both.
const ACTIVITY_KEY = "desk-last-activity";
// Written by the tab that signed out (by choice or for idling) so every other tab follows at once.
const SIGNED_OUT_KEY = "desk-signed-out";

function readSharedActivity(): number {
  try {
    const v = Number(localStorage.getItem(ACTIVITY_KEY));
    // never in the future: a stale clock must not hold the session open for ever
    return Number.isFinite(v) ? Math.min(v, Date.now()) : 0;
  } catch {
    return 0;  // private mode: this tab falls back to watching only itself
  }
}

type SessionTimer = { state: IdleState; secondsLeft: number };
type SessionStatus = { idle_seconds_left: number | null };

/** Keeps the session open while the person is doing something, warns two minutes before the engine would end
 *  it, and signs out when the moment comes (docs/SECURITY.md, "Sessions"). `minutes` is the engine's idle window
 *  (0 when there is none). Only real input counts as activity; the page's own polling, the event stream, the
 *  countdown and background refreshes never do. After a sleep, a reconnection or a long time in the background
 *  the engine is asked how long is really left, and its answer can only shorten what this tab believes. */
function useSessionTimer(minutes: number, signedIn: boolean, onExpired: () => void): [SessionTimer, () => Promise<boolean>] {
  const [timer, setTimer] = useState<SessionTimer>({ state: "active", secondsLeft: Infinity });
  const lastRef = useRef(Date.now());
  const limit = minutes * 60_000;

  const resync = useCallback(async () => {
    if (!limit) return;
    try {
      const s = await get<SessionStatus>("/api/session/status");   // a GET: never extends the session
      lastRef.current = fromServer(Date.now(), s.idle_seconds_left, limit, Math.max(lastRef.current, readSharedActivity()));
    } catch { /* a 401 here has already announced the sign-out; a network error leaves the local estimate */ }
  }, [limit]);

  const stay = useCallback(async (): Promise<boolean> => {
    try {
      // one authenticated POST: the engine refreshes last_seen only if the session is still alive (401 otherwise)
      const s = await post<SessionStatus>("/api/session/ping");
      const t = Date.now();
      lastRef.current = fromServer(t, s.idle_seconds_left, limit, t);
      try { localStorage.setItem(ACTIVITY_KEY, String(t)); } catch { /* private mode */ }
      setTimer({ state: "active", secondsLeft: secondsLeft(t, lastRef.current, limit) });   // the warning closes at once
      return true;
    } catch {
      return false;   // the session had already ended; the 401 brings the sign-in screen
    }
  }, [limit]);

  useEffect(() => {
    if (!limit || !signedIn) return;
    const every = Math.min(60_000, limit / 4);  // a short window needs a faster heartbeat to stay reachable
    let shared = lastRef.current = Date.now();
    let pinged = lastRef.current;
    let warned = false;
    const seen = (e: Event) => {
      if (!isUserActivity(e.type) || warned) return;   // while the warning shows, only "Stay signed in" counts
      const t = Date.now();
      lastRef.current = t;
      if (t - shared > 5_000) {
        shared = t;
        try { localStorage.setItem(ACTIVITY_KEY, String(t)); } catch { /* private mode */ }
      }
    };
    for (const ev of ACTIVITY_EVENTS) window.addEventListener(ev, seen, { passive: true, capture: true });
    const wake = () => { if (document.visibilityState !== "hidden") void resync(); };
    void resync();   // a freshly loaded page knows nothing of the session's age: ask before trusting its own clock
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", wake);
    window.addEventListener("focus", wake);
    const t = setInterval(() => {
      const now = Date.now();
      const last = mergeActivity(now, lastRef.current, readSharedActivity());
      const state = idleState(now, last, limit);
      warned = state === "warning";
      setTimer({ state, secondsLeft: secondsLeft(now, last, limit) });
      if (state === "expired") {
        clearInterval(t);  // sign out once, not again on every tick while the two requests are in flight
        onExpired();
        return;
      }
      // Anything done since the last ping is reported, however sparse. Only the tab the person is
      // actually in has activity of its own, so open tabs do not all ping at once.
      if (state === "active" && last > pinged && now - pinged >= every) {
        pinged = now;
        post("/api/session/ping").catch(() => undefined);
      }
    }, tickMs(limit));
    return () => {
      clearInterval(t);
      for (const ev of ACTIVITY_EVENTS) window.removeEventListener(ev, seen, { capture: true });
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("online", wake);
      window.removeEventListener("focus", wake);
    };
  }, [limit, signedIn, onExpired, resync]);

  return [timer, stay];
}

/** Shown two minutes before the engine ends the session. Nothing on this dialog counts as activity except the
 *  explicit choice to stay, which asks the engine; closing it is not a choice. */
function SessionWarning({ secondsLeft, onStay, onLogout }: { secondsLeft: number; onStay: () => void; onLogout: () => void }): ReactNode {
  // the countdown ticks every second from the last value the timer gave it (the timer itself looks every 15 s)
  const [left, setLeft] = useState(secondsLeft);
  useEffect(() => { setLeft(secondsLeft); }, [secondsLeft]);
  useEffect(() => {
    const t = setInterval(() => setLeft((v) => Math.max(0, v - 1)), 1000);
    return () => clearInterval(t);
  }, []);
  const m = Math.floor(left / 60);
  const s = left % 60;
  return (
    <div className="scrim" role="presentation">
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="session-warning-title">
        <div className="modal-head"><h2 id="session-warning-title">{tr("Still there?")}</h2></div>
        <div className="modal-body">
          <p>{tr("Your session will expire in 2 minutes due to inactivity.")}</p>
          <p className="muted" aria-live="polite">{tr("Time left: {0}", { 0: `${m}:${String(s).padStart(2, "0")}` })}</p>
        </div>
        <div className="modal-foot">
          <button className="btn ghost" onClick={onLogout}>{tr("Log out now")}</button>
          <button className="btn primary" autoFocus onClick={onStay}>{tr("Stay signed in")}</button>
        </div>
      </div>
    </div>
  );
}

/** Step-up: shown when the engine answers 403 reauth_required to a high-risk action (changing roles, connections,
 *  MCP servers, closing a month, deleting data, approving an action that leaves the workspace, …). The password goes
 *  to POST /api/auth/reauth on this session; a Google-only account is sent to sign in again instead. */
function ReauthDialog({ google, onDone }: { google: boolean; onDone: (ok: boolean) => void }): ReactNode {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function confirm() {
    setBusy(true); setError(null);
    try {
      await post("/api/auth/reauth", { password });
      onDone(true);
    } catch (e) {
      const err = e as ApiError;
      setError(err.code === "reauth_google" ? tr("This account signs in with Google. Sign in again to continue.") : errorText(e));
      setBusy(false);
    }
  }
  return (
    <div className="scrim" role="presentation">
      <form className="modal" role="dialog" aria-modal="true" aria-labelledby="reauth-title" onSubmit={(e) => { e.preventDefault(); void confirm(); }}>
        <div className="modal-head"><h2 id="reauth-title">{tr("Confirm it's you")}</h2></div>
        <div className="modal-body stack">
          <p>{tr("This action changes something sensitive. Enter your password to continue.")}</p>
          {google
            ? <p className="muted">{tr("This account signs in with Google. Sign in again to continue.")}</p>
            : <label className="stack"><span className="sr">{tr("Password")}</span>
                <input className="input" type="password" autoComplete="current-password" autoFocus value={password}
                  onChange={(e) => setPassword(e.target.value)} /></label>}
          <ErrorBox error={error} />
        </div>
        <div className="modal-foot">
          <button type="button" className="btn ghost" onClick={() => onDone(false)}>{tr("Cancel")}</button>
          {!google && <button type="submit" className="btn primary" disabled={busy || !password}>{tr("Confirm")}</button>}
        </div>
      </form>
    </div>
  );
}

/** The desktop app has no accounts (the engine runs for this computer's user under a per-launch token), so there
 *  is no session to end. After the same inactivity window the screen is locked instead: every page is unmounted,
 *  nothing financial stays on screen, and a deliberate click brings the app back. This protects against someone
 *  reading the screen of an unattended, unlocked computer; it cannot protect against someone who already holds the
 *  account (docs/SECURITY.md, "Desktop"). */
function LockScreen({ onResume }: { onResume: () => void }): ReactNode {
  return (
    <div className="page lock" role="dialog" aria-modal="true" aria-label={tr("Locked")}>
      <BrandMark />
      <h1>{tr("Locked after inactivity")}</h1>
      <p className="muted">{tr("Nothing happened for a while, so the workspace was hidden.")}</p>
      <button className="btn primary" autoFocus onClick={onResume}>{tr("Resume")}</button>
    </div>
  );
}

function App(): ReactNode {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wsId, setWsId] = useState<string | null>(storedWs());
  const [counts, setCounts] = useState({ unread: 0, pending_approvals: 0 });
  const [menu, setMenu] = useState(false);
  const [idleOut, setIdleOut] = useState(false);
  const [viewChoice, setViewChoice] = useState<Record<string, View>>({});
  const route = useRoute();

  const reloadSession = useCallback(async () => {
    try {
      const s = await get<Session>("/api/session");
      if (s.user) setIdleOut(false);
      const pref = (s as any)?.user?.language as Lang | undefined;
      if (pref && pref !== lang()) setLang(pref);  // the person's saved language follows them to every device
      setSession(s);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => { void reloadSession(); }, [reloadSession]);
  useEffect(() => {  // Ctrl/Cmd+K opens search from anywhere
    const k = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); go("search"); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  // links from generated files carry ?ws=<workspace>
  useEffect(() => {
    const wanted = new URLSearchParams(location.hash.split("?")[1] || "").get("ws");
    if (wanted && wanted !== wsId && session?.workspaces.some((w) => w.id === wanted)) {
      try { localStorage.setItem("desk-ws", wanted); } catch { /* private mode */ }
      setWsId(wanted);
    }
  }, [route.join("/"), session]);

  const signOut = useCallback(async (reason: "idle" | "user") => {
    if (reason === "idle") setIdleOut(true);
    // the page is signed out now, whatever the network does next: nothing protected stays on screen
    setSession((s) => (s ? { ...s, user: null, workspaces: [] } : s));
    try { await post(`/api/auth/logout?reason=${reason}`); } catch { /* offline, or the engine has ended it anyway */ }
    try { localStorage.setItem(SIGNED_OUT_KEY, `${reason}:${Date.now()}`); } catch { /* private mode */ }  // other tabs follow
    try { sessionStorage.removeItem("desk-token"); } catch { /* nothing stored */ }
    await reloadSession();
  }, [reloadSession]);
  const onExpired = useCallback(() => { void signOut("idle"); }, [signOut]);
  const [timer, stay] = useSessionTimer(session?.idle_minutes ?? 0, Boolean(session?.user), onExpired);
  const [locked, setLocked] = useState(false);
  const [reauth, setReauth] = useState<{ resolve: (ok: boolean) => void } | null>(null);
  useEffect(() => {
    if (!session?.user || session.mode === "desktop") { setReauthHandler(null); return; }
    setReauthHandler(() => new Promise<boolean>((resolve) => setReauth({ resolve })));
    return () => setReauthHandler(null);
  }, [session?.user?.id, session?.mode]);
  const onLock = useCallback(() => setLocked(true), []);
  const [lockTimer] = useSessionTimer(locked ? 0 : (session?.lock_minutes ?? 0), Boolean(session?.user), onLock);
  void lockTimer;
  useEffect(() => {   // the desktop shell: lock when the computer locks or sleeps; re-check the session on waking
    window.desk?.onLock?.(() => { if (session?.lock_minutes) setLocked(true); });
    window.desk?.onResume?.(() => { void reloadSession(); });
  }, [session?.lock_minutes, reloadSession]);

  // the engine refused a request: show the sign-in screen instead of a page that cannot load anything
  const haveUser = Boolean(session?.user);
  useEffect(() => {
    if (!haveUser) return;  // on the sign-in screen a 401 is a wrong password, not a lost session
    const refused = (e: Event) => {
      if ((e as CustomEvent).detail === "session_idle") setIdleOut(true);
      void reloadSession();
    };
    // another tab signed out: this one does too, with the same explanation
    const other = (e: StorageEvent) => {
      if (e.key !== SIGNED_OUT_KEY || !e.newValue) return;
      if (e.newValue.startsWith("idle")) setIdleOut(true);
      void reloadSession();
    };
    window.addEventListener(SIGNED_OUT_EVENT, refused);
    window.addEventListener("storage", other);
    return () => { window.removeEventListener(SIGNED_OUT_EVENT, refused); window.removeEventListener("storage", other); };
  }, [haveUser, reloadSession]);

  const ws: Workspace | undefined = session?.workspaces.find((w) => w.id === wsId) || session?.workspaces[0];

  const refreshCounts = useCallback(() => {
    if (!ws) return;
    get(`/api/w/${ws.id}/notifications`).then((n) => setCounts({ unread: n.unread, pending_approvals: n.pending_approvals }))
      .catch(() => undefined);
  }, [ws?.id]);
  useEffect(() => {
    refreshCounts();
    const t = setInterval(refreshCounts, 20000);
    return () => clearInterval(t);
  }, [refreshCounts]);

  if (error && !session) {
    return <div className="page"><h1>{tr("OfinTech Desk")}</h1><ErrorBox error={error} />
      <p className="muted">{tr("If you are using the desktop app, quit and reopen it.")}</p></div>;
  }
  if (!session) return <div className="page muted">{tr("Starting…")}</div>;
  if (route[0] === "invite" && route[1]) return <InvitePage token={route[1]} session={session} onDone={reloadSession} />;
  if (route[0] === "reset" && route[1]) return <ResetPage token={route[1]} onDone={reloadSession} />;
  if (!session.user) return <AuthPage session={session} onDone={reloadSession} signedOutForIdling={idleOut} />;
  if (locked) return <LockScreen onResume={() => setLocked(false)} />;
  if (!ws) return <Onboarding session={session} onDone={async (id) => { selectWs(id); await reloadSession(); }} />;

  function selectWs(id: string) {
    try { localStorage.setItem("desk-ws", id); } catch { /* private mode */ }
    setWsId(id);
  }

  const view: View = viewChoice[ws.id] || storedView(ws.id) || defaultView(ws.role);
  function selectView(v: View) {
    try { localStorage.setItem(`desk-view:${ws!.id}`, v); } catch { /* private mode */ }
    setViewChoice((c) => ({ ...c, [ws!.id]: v }));
  }
  const navKeys = new Set(VIEW_NAV[view].filter((k) => k !== "oversight" || can(ws, "admin")));   // oversight reads the audit log
  const section = route[0] || "overview";
  let page: ReactNode;
  switch (section) {
    case "overview": page = <OverviewPage view={route[1]} arg={route[2]} />; break;
    case "finance": page = <FinancePage tab={route[1]} sub={route[2]} />; break;
    case "ledger": page = <LedgerPage tab={route[1]} />; break;
    case "oversight": page = <OversightPage />; break;
    case "dashboards": page = <DashboardsPage id={route[1]} />; break;
    case "data": page = <DataPage id={route[1]} />; break;
    case "documents": page = <DocumentsPage id={route[1]} />; break;
    case "files": page = <FilesPage id={route[1]} />; break;
    case "memory": page = <MemoryPage />; break;
    case "analysis": page = <AnalysisPage tab={route[1]} id={route[2]} />; break;
    case "costcentres": page = <CostCentresPage id={route[1]} />; break;
    case "close": page = <ClosePage id={route[1]} />; break;
    case "search": page = <SearchPage key={location.hash} />; break;
    case "workflows": page = <WorkflowsPage id={route[1]} />; break;
    case "skills": page = <SkillsPage name={route[1]} />; break;
    case "inbox": page = <InboxPage />; break;
    case "connections": page = <ConnectionsPage />; break;
    case "settings": page = <SettingsPage tab={route[1]} />; break;
    case "new-workspace": page = <Onboarding session={session} embedded onDone={async (id) => { selectWs(id);
      await reloadSession(); go("overview"); }} />; break;
    case "chat": page = <ChatPage threadId={route[1]} />; break;
    default: page = <OverviewPage />;
  }

  return (
    <AppCtx.Provider value={{ session, ws, reloadSession, counts, refreshCounts }}>
      {timer.state === "warning" && <SessionWarning secondsLeft={timer.secondsLeft}
        onStay={() => { void stay(); }} onLogout={() => { void signOut("user"); }} />}
      {reauth && <ReauthDialog google={!session.user?.has_password && Boolean(session.features.google_login)}
        onDone={(ok) => { reauth.resolve(ok); setReauth(null); }} />}
      <div className="shell">
        <header className="mobilebar">
          <button className="btn ghost small" aria-label={tr("Menu")} aria-expanded={menu} onClick={() => setMenu(!menu)}>☰</button>
          <BrandMark /><strong>{ws.name}</strong>
        </header>
        <aside className={`rail ${menu ? "open" : ""}`} aria-label={tr("Main")} onClick={(e) => {
          if ((e.target as HTMLElement).closest("a")) setMenu(false);
        }}>
          <div className="brand"><BrandMark /><div className="brand-name"><Wordmark height={14} /><span className="brand-desk">Desk</span>
            <small>{session.mode === "desktop" ? tr("On this computer") : tr("Team Server")}</small></div></div>
          <div className="ws-switch">
            <label className="sr" htmlFor="ws-select">{tr("Workspace")}</label>
            <select id="ws-select" value={ws.id} onChange={(e) => {
              if (e.target.value === "__new") go("new-workspace");
              else { selectWs(e.target.value); go("overview"); }
            }}>
              {session.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              <option value="__new">{tr("+ New workspace…")}</option>
            </select>
          </div>
          <div className="view-switch" role="tablist" aria-label={tr("View")}>
            {VIEWS.map(([v, label, title]) => (
              <button key={v} role="tab" aria-selected={view === v} title={tr(title)} onClick={() => selectView(v)}>{tr(label)}</button>
            ))}
          </div>
          <nav className="nav">
            {NAV.filter(([key]) => navKeys.has(key)).map(([key, label, icon]) => (
              <a key={key} href={`#/${key}`} aria-current={section === key ? "page" : undefined}>
                <Icon name={icon} />{tr(label)}
                {key === "inbox" && counts.pending_approvals + counts.unread > 0 && (
                  <span className="count" aria-label={`${counts.pending_approvals} approvals, ${counts.unread} unread`}>
                    {counts.pending_approvals + counts.unread}</span>)}
              </a>
            ))}
            <div className="nav-sep" />
            {NAV2.map(([key, label, icon]) => (
              <a key={key} href={`#/${key}`} aria-current={section === key ? "page" : undefined}><Icon name={icon} />{tr(label)}</a>
            ))}
          </nav>
          <div className="rail-foot">{session.user.name}, {ws.role}<br />{tr("Version")}{" "}{session.version}</div>
        </aside>
        <main className="main" id="main">{page}</main>
      </div>
    </AppCtx.Provider>
  );
}

/** Re-mounts the app when the language changes, so every string is looked up again. */
function Root(): ReactNode {
  const [l, setL] = useState(lang());
  useEffect(() => onLang(setL), []);
  return <App key={l} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><ToastProvider><Root /></ToastProvider></StrictMode>,
);
