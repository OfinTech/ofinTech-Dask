// The Owner Control Center: a protected administrative area at #/owner/… for platform owners and delegated
// administrators. It reuses the app's rail, panels, tables and charts, and shows nothing that the API does not
// return — the API, not the page, decides what a platform role may see or change.
import { type ReactNode } from "react";
import { get } from "../lib/api";
import { useApp } from "../lib/app";
import { BrandMark, ErrorBox, Icon, Loading, Wordmark, go, useLoad } from "../components/ui";
import { N, tr } from "../lib/i18n";
import { OverviewPage } from "./Overview";
import { UsersPage } from "./Users";
import { WorkspacesPage } from "./Workspaces";
import { PlansPage, SubscriptionsPage } from "./Plans";
import { BillingPage, CouponsPage } from "./Billing";
import { AnalyticsPage, LeadsPage } from "./Growth";
import { EmailPage, NotificationsPage } from "./Comms";
import { AuditPage, IntegrationsPage, SecurityPage, SettingsPage, StoragePage } from "./Security";

export const SECTIONS: [string, string, string, string][] = [  // key, label, icon, permission
  ["overview", N("Overview"), "dash", "dashboard.read"],
  ["users", N("Users"), "team", "users.read"],
  ["workspaces", N("Workspaces"), "centre", "workspaces.read"],
  ["subscriptions", N("Subscriptions"), "ledger", "subscriptions.read"],
  ["plans", N("Plans & Features"), "sheet", "plans.read"],
  ["billing", N("Billing"), "trend", "billing.read"],
  ["coupons", N("Coupons"), "skill", "coupons.read"],
  ["leads", N("Sales Leads"), "inbox", "sales.read"],
  ["analytics", N("Analytics"), "pulse", "analytics.read"],
  ["notifications", N("Notifications"), "send", "notifications.read"],
  ["email", N("Email Settings"), "docs", "email.read"],
  ["security", N("Security Center"), "shield", "security.read"],
  ["integrations", N("Integrations"), "plug", "integrations.read"],
  ["storage", N("Storage"), "data", "storage.read"],
  ["audit", N("Audit Logs"), "book", "audit.read"],
  ["settings", N("Platform Settings"), "gear", "settings.read"],
];

export function OwnerApp({ route }: { route: string[] }): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const section = route[1] || "overview";
  const arg = route[2];
  const { data: me, error } = useLoad(() => get("/api/owner/overview?period=today"), [session.user?.id]);
  if (!session.user?.platform_role) {
    return <div className="page"><h1>{tr("Owner Control Center")}</h1><p className="muted">{tr("This area is for platform administrators.")}</p>
      <a className="btn" href="#/overview">{tr("Back to the app")}</a></div>;
  }
  let page: ReactNode;
  switch (section) {
    case "users": page = <UsersPage id={arg} />; break;
    case "workspaces": page = <WorkspacesPage id={arg} />; break;
    case "subscriptions": page = <SubscriptionsPage />; break;
    case "plans": page = <PlansPage />; break;
    case "billing": page = <BillingPage />; break;
    case "coupons": page = <CouponsPage />; break;
    case "leads": page = <LeadsPage id={arg} />; break;
    case "analytics": page = <AnalyticsPage />; break;
    case "notifications": page = <NotificationsPage />; break;
    case "email": page = <EmailPage />; break;
    case "security": page = <SecurityPage />; break;
    case "integrations": page = <IntegrationsPage />; break;
    case "storage": page = <StoragePage />; break;
    case "audit": page = <AuditPage />; break;
    case "settings": page = <SettingsPage />; break;
    default: page = error ? <div className="page"><ErrorBox error={error} /></div> : me ? <OverviewPage /> : <div className="page"><Loading /></div>;
  }
  const alerts = me?.alerts?.length || 0;
  return (
    <div className="shell owner-shell">
      <header className="mobilebar"><BrandMark /><strong>{tr("Owner Control Center")}</strong></header>
      <aside className="rail" aria-label={tr("Owner Control Center")}>
        <div className="brand"><BrandMark /><div className="brand-name"><Wordmark height={14} /><span className="brand-desk">{tr("Owner")}</span>
          <small>{session.user.platform_role === "owner" ? tr("Platform owner") : tr("Platform administrator")}</small></div></div>
        <nav className="nav">
          {SECTIONS.filter(([, , , perm]) => perms.has(perm)).map(([key, label, icon]) => (
            <a key={key} href={`#/owner/${key}`} aria-current={section === key ? "page" : undefined}><Icon name={icon} />{tr(label)}
              {key === "overview" && alerts > 0 && <span className="count" aria-label={tr("{0} alerts", { 0: alerts })}>{alerts}</span>}</a>
          ))}
          <div className="nav-sep" />
          <a href="#/overview"><Icon name="chat" />{tr("Back to the app")}</a>
        </nav>
        <div className="rail-foot">{session.user.name}<br />{tr("Version")}{" "}{session.version}{session.hosted ? ` · ${tr("hosted")}` : ` · ${tr("self-hosted")}`}</div>
      </aside>
      <main className="main" id="main">{page}</main>
    </div>
  );
}

export function goOwner(path: string): void {
  go(`owner/${path}`);
}
