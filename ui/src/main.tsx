import { StrictMode, useCallback, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

try {
  const theme = localStorage.getItem("desk-theme");
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
} catch { /* storage unavailable */ }
import { get } from "./lib/api";
import { AppCtx, type Session, type Workspace } from "./lib/app";
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
import { SearchPage } from "./pages/Search";
import { WorkflowsPage } from "./pages/Workflows";
import { SkillsPage } from "./pages/Skills";
import { InboxPage } from "./pages/Inbox";
import { ConnectionsPage } from "./pages/Connections";
import { SettingsPage } from "./pages/Settings";
import { N, lang, onLang, setLang, tr, type Lang } from "./lib/i18n";

const NAV: [string, string, string][] = [
  ["search", N("Search"), "search"], ["chat", N("Assistant"), "chat"], ["dashboards", N("Dashboards"), "dash"], ["analysis", N("Financial analysis"), "trend"], ["costcentres", N("Cost centres"), "centre"], ["data", N("Data"), "data"],
  ["files", N("Files"), "sheet"], ["documents", N("Documents"), "docs"],
  ["workflows", N("Workflows"), "flow"], ["memory", N("Memory"), "memory"], ["skills", N("Skills"), "skill"], ["inbox", N("Inbox"), "inbox"],
];
const NAV2: [string, string, string][] = [["connections", N("Connections"), "plug"], ["settings", N("Settings"), "gear"]];

function storedWs(): string | null {
  try { return localStorage.getItem("desk-ws"); } catch { return null; }
}

function App(): ReactNode {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wsId, setWsId] = useState<string | null>(storedWs());
  const [counts, setCounts] = useState({ unread: 0, pending_approvals: 0 });
  const [menu, setMenu] = useState(false);
  const route = useRoute();

  const reloadSession = useCallback(async () => {
    try {
      const s = await get<Session>("/api/session");
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
  if (!session.user) return <AuthPage session={session} onDone={reloadSession} />;
  if (!ws) return <Onboarding session={session} onDone={async (id) => { selectWs(id); await reloadSession(); }} />;

  function selectWs(id: string) {
    try { localStorage.setItem("desk-ws", id); } catch { /* private mode */ }
    setWsId(id);
  }

  const section = route[0] || "chat";
  let page: ReactNode;
  switch (section) {
    case "dashboards": page = <DashboardsPage id={route[1]} />; break;
    case "data": page = <DataPage id={route[1]} />; break;
    case "documents": page = <DocumentsPage id={route[1]} />; break;
    case "files": page = <FilesPage id={route[1]} />; break;
    case "memory": page = <MemoryPage />; break;
    case "analysis": page = <AnalysisPage tab={route[1]} id={route[2]} />; break;
    case "costcentres": page = <CostCentresPage id={route[1]} />; break;
    case "search": page = <SearchPage key={location.hash} />; break;
    case "workflows": page = <WorkflowsPage id={route[1]} />; break;
    case "skills": page = <SkillsPage name={route[1]} />; break;
    case "inbox": page = <InboxPage />; break;
    case "connections": page = <ConnectionsPage />; break;
    case "settings": page = <SettingsPage tab={route[1]} />; break;
    case "new-workspace": page = <Onboarding session={session} embedded onDone={async (id) => { selectWs(id);
      await reloadSession(); go("chat"); }} />; break;
    default: page = <ChatPage threadId={route[1]} />;
  }

  return (
    <AppCtx.Provider value={{ session, ws, reloadSession, counts, refreshCounts }}>
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
              else { selectWs(e.target.value); go("chat"); }
            }}>
              {session.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              <option value="__new">{tr("+ New workspace…")}</option>
            </select>
          </div>
          <nav className="nav">
            {NAV.map(([key, label, icon]) => (
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
