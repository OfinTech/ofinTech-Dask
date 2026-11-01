import { useState, type ReactNode } from "react";
import { del, get, post, put } from "../lib/api";
import { useApp } from "../lib/app";
import { Confirm, ErrorBox, Field, Loading, PageHead, useAction, useLoad } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { tr } from "../lib/i18n";
import { Kpi, Pager, Records, Section, Tag, Toggle, When, bytes } from "./shared";

export function SecurityPage(): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data, error, reload } = useLoad(() => get("/api/owner/security"), []);
  const { data: sessions, reload: reloadSessions } = useLoad(() => get("/api/owner/sessions?limit=50"), []);
  const [f, setF] = useState<any>({});
  const [adminEmail, setAdminEmail] = useState("");
  const [adminRole, setAdminRole] = useState("admin");
  const [revoke, setRevoke] = useState<any | null>(null);
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const configure = perms.has("security.configure");
  const p = data.policy;
  const v = (k: string) => f[k] ?? p[`security.${k}`];
  const save = () => void run(() => put("/api/owner/security", f), tr("Security policy saved")).then((r: any) => {
    if (r?.pending_approval) run(async () => undefined, tr("The change awaits a second owner's approval."));
    setF({}); reload();
  });
  return (
    <div className="page">
      <PageHead title={tr("Security Center")} sub={tr("Authentication events, sessions, privileged actions and the policies that govern them. Nothing here loosens a control past the engine's own limits.")} />
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <Kpi label={tr("Failed sign-ins (24 h)")} value={data.failed_logins_24h} tone={data.failed_logins_24h > 20 ? "watch" : undefined} />
        <Kpi label={tr("Refusals (7 days)")} value={data.denied_7d} />
        <Kpi label={tr("Active sessions")} value={data.active_sessions} />
        <Kpi label={tr("Sessions revoked (7 days)")} value={data.revoked_7d} />
        <Kpi label={tr("Privileged actions (7 days)")} value={data.privileged_actions_7d} />
        <Kpi label={tr("Permission changes (7 days)")} value={data.permission_changes_7d} />
        <Kpi label={tr("API security events (7 days)")} value={data.api_events_7d} />
        <Kpi label={tr("Configuration changes (7 days)")} value={data.config_changes_7d} />
        <Kpi label={tr("Accounts with MFA")} value={`${data.mfa_users} / ${data.users_total}`} format="text" />
      </div>
      {!data.owner_list_configured && <div className="notice warn" style={{ marginBottom: 16 }}>{tr("DESK_PLATFORM_OWNERS is not set: the first account on this server acts as platform owner. Set it in the server environment to pin ownership to named accounts.")}</div>}
      {!!data.pending_changes.length && <Section title={tr("Changes awaiting a second owner")}>
        <Records rows={data.pending_changes} cols={[{ key: "kind", label: tr("Change") }, { key: "requested_by_email", label: tr("Requested by") },
          { key: "payload", label: tr("Details"), render: (c: any) => <code className="small">{JSON.stringify(c.payload).slice(0, 160)}</code> }, { key: "created_at", label: tr("When"), render: (c: any) => <When ts={c.created_at} /> },
          { key: "x", label: "", render: (c: any) => session.user?.platform_role === "owner" ? <div className="row">
            <button className="btn small primary" disabled={busy} onClick={() => void run(() => post(`/api/owner/changes/${c.id}/decide`, { approve: true }), tr("Approved and applied")).then(reload)}>{tr("Approve")}</button>
            <button className="btn small" disabled={busy} onClick={() => void run(() => post(`/api/owner/changes/${c.id}/decide`, { approve: false }), tr("Rejected")).then(reload)}>{tr("Reject")}</button></div> : null }]} />
      </Section>}
      <Section title={tr("Policies")} actions={configure ? <button className="btn primary" disabled={busy || !Object.keys(f).length} onClick={save}>{tr("Save")}</button> : null}>
        <div className="grid3">
          <Field label={tr("MFA enforcement")}><select className="select" disabled={!configure} value={v("mfa_policy")} onChange={(e) => setF({ ...f, mfa_policy: e.target.value })}>
            <option value="none">{tr("Optional")}</option><option value="admins">{tr("Required for administrators")}</option><option value="all">{tr("Required for everyone")}</option></select></Field>
          <Field label={tr("Account lockout: failed attempts")}><input className="input" type="number" min={3} max={50} disabled={!configure} value={v("lockout_threshold")} onChange={(e) => setF({ ...f, lockout_threshold: Number(e.target.value) })} /></Field>
          <Field label={tr("Lockout window (minutes)")}><input className="input" type="number" min={1} max={1440} disabled={!configure} value={v("lockout_minutes")} onChange={(e) => setF({ ...f, lockout_minutes: Number(e.target.value) })} /></Field>
          <Field label={tr("Alert after … failed sign-ins per hour")}><input className="input" type="number" min={3} disabled={!configure} value={v("alert_failed_logins")} onChange={(e) => setF({ ...f, alert_failed_logins: Number(e.target.value) })} /></Field>
          <Field label={tr("Inactivity sign-out (minutes, 30–45)")} hint={tr("Currently {0}", { 0: data.session.idle_minutes })}><input className="input" type="number" min={30} max={45} disabled={!configure} value={f.session_idle_minutes ?? p["security.session_idle_minutes"] ?? data.session.idle_minutes} onChange={(e) => setF({ ...f, session_idle_minutes: Number(e.target.value) })} /></Field>
          <Field label={tr("Session lifetime (hours, 1–24)")} hint={tr("Currently {0}", { 0: data.session.session_hours })}><input className="input" type="number" min={1} max={24} disabled={!configure} value={f.session_hours ?? p["security.session_hours"] ?? data.session.session_hours} onChange={(e) => setF({ ...f, session_hours: Number(e.target.value) })} /></Field>
          <Field label={tr("Administrator session lifetime (hours)")} hint={tr("Currently {0}", { 0: data.session.admin_session_hours })}><input className="input" type="number" min={1} max={24} disabled={!configure} value={f.admin_session_hours ?? p["security.admin_session_hours"] ?? data.session.admin_session_hours} onChange={(e) => setF({ ...f, admin_session_hours: Number(e.target.value) })} /></Field>
          <Field label={tr("Audit retention (days)")} hint={tr("Entries are immutable; retention is applied by the operator's archival job.")}><input className="input" type="number" min={90} max={3650} disabled={!configure} value={v("audit_retention_days")} onChange={(e) => setF({ ...f, audit_retention_days: Number(e.target.value) })} /></Field>
          <Field label={tr("API key maximum age (days, 0 = none)")}><input className="input" type="number" min={0} disabled={!configure} value={v("api_key_max_age_days")} onChange={(e) => setF({ ...f, api_key_max_age_days: Number(e.target.value) })} /></Field>
        </div>
        <div className="row wrap">
          <Toggle label={tr("Dual approval for critical changes (billing keys, plans, security policy, delegation)")} disabled={!configure} checked={f.dual_approval ?? p["security.dual_approval"]} onChange={(x) => setF({ ...f, dual_approval: x })} />
          <Toggle label={tr("Google as an approved sign-in provider")} disabled={!configure} checked={(f.oauth_providers ?? p["security.oauth_providers"] ?? []).includes("google")} onChange={(x) => setF({ ...f, oauth_providers: x ? ["google"] : [] })} />
        </div>
        <p className="small muted">{tr("Step-up re-authentication ({0} min) applies to every change made here.", { 0: data.session.reauth_minutes })}</p>
      </Section>
      <div className="grid2">
        <Section title={tr("Platform administrators")}>
          <Records rows={data.admins.map((a: any) => ({ id: a.user_id, ...a }))} cols={[{ key: "email", label: tr("Account"), render: (a: any) => <>{a.name}<br /><span className="small muted">{a.email}</span></> },
            { key: "role", label: tr("Role"), render: (a: any) => <Tag text={a.role} tone={a.role === "owner" ? "blue" : ""} /> }, { key: "granted_by", label: tr("Granted by"), render: (a: any) => a.granted_by || "—" },
            { key: "x", label: "", render: (a: any) => perms.has("admins.manage") && a.user_id !== session.user?.id ? <button className="btn ghost small" onClick={() => setRevoke(a)}>{tr("Remove")}</button> : null }]} />
          {perms.has("admins.manage") && <div className="row wrap"><input className="input" style={{ maxWidth: 260 }} type="email" placeholder={tr("existing account's e-mail")} value={adminEmail} onChange={(e) => setAdminEmail(e.target.value)} />
            <select className="select" style={{ width: 150 }} value={adminRole} onChange={(e) => setAdminRole(e.target.value)}><option value="admin">{tr("administrator")}</option><option value="owner">{tr("owner")}</option></select>
            <button className="btn" disabled={busy || !adminEmail} onClick={() => void run(() => post("/api/owner/admins", { email: adminEmail, role: adminRole }), tr("Role granted")).then((r: any) => { if (r?.pending_approval) run(async () => undefined, tr("The change awaits a second owner's approval.")); setAdminEmail(""); reload(); })}>{tr("Delegate")}</button></div>}
          <p className="small muted">{tr("Platform roles are granted here, by DESK_PLATFORM_OWNERS or by `python -m desk_engine owner grant`. Never from the public sign-up form.")}</p>
        </Section>
        <Section title={tr("Support access grants")}>
          <Records rows={data.support_grants} cols={[{ key: "admin_email", label: tr("Administrator") }, { key: "workspace_name", label: tr("Workspace") }, { key: "reason", label: tr("Reason") },
            { key: "expires_at", label: tr("Expires"), render: (g: any) => fmtDateTime(g.expires_at) },
            { key: "x", label: "", render: (g: any) => <button className="btn ghost small" onClick={() => void run(() => del(`/api/owner/support-access/${g.id}`), tr("Revoked")).then(reload)}>{tr("Revoke")}</button> }]} empty={tr("No active support access.")} />
        </Section>
        <Section title={tr("Recent high-severity events (refusals)")}>
          <Records rows={data.recent_alerts.map((e: any, i: number) => ({ id: i, ...e }))} cols={[{ key: "action", label: tr("Event") }, { key: "user_id", label: tr("Account"), render: (e: any) => e.user_id || "—" },
            { key: "detail", label: tr("Detail"), render: (e: any) => <span className="small">{e.detail ? String(e.detail).slice(0, 80) : "—"}</span> }, { key: "created_at", label: tr("When"), render: (e: any) => <When ts={e.created_at} /> }]} />
        </Section>
        <Section title={tr("Recent privileged actions")}>
          <Records rows={data.recent_privileged.map((e: any, i: number) => ({ id: i, ...e }))} cols={[{ key: "action", label: tr("Action") }, { key: "user_id", label: tr("By") }, { key: "target", label: tr("Target") },
            { key: "created_at", label: tr("When"), render: (e: any) => <When ts={e.created_at} /> }]} />
        </Section>
      </div>
      <Section title={tr("Active sessions")} actions={<button className="btn small" onClick={reloadSessions}>{tr("Refresh")}</button>}>
        {!sessions ? <Loading /> : <Records rows={sessions.items} cols={[{ key: "email", label: tr("Account") }, { key: "user_agent", label: tr("Device") },
          { key: "created_at", label: tr("Signed in"), render: (s: any) => <When ts={s.created_at} /> }, { key: "last_seen", label: tr("Last activity"), render: (s: any) => <When ts={s.last_seen} /> },
          { key: "mfa_at", label: tr("MFA"), render: (s: any) => s.mfa_at ? tr("yes") : tr("no") },
          { key: "x", label: "", render: (s: any) => perms.has("users.manage") ? <button className="btn ghost small" onClick={() => void run(() => post(`/api/owner/users/${s.user_id}/sign-out`), tr("Sessions revoked")).then(reloadSessions)}>{tr("Sign out user")}</button> : null }]} />}
      </Section>
      {revoke && <Confirm text={tr("Remove the platform role of {0}?", { 0: revoke.email })} yes={tr("Remove")} onClose={() => setRevoke(null)}
        onYes={() => void run(() => del(`/api/owner/admins/${revoke.user_id}`), tr("Role removed")).then(reload)} />}
    </div>
  );
}

export function IntegrationsPage(): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data, error, reload } = useLoad(() => get("/api/owner/integrations"), []);
  const [g, setG] = useState<any>({});
  const [a, setA] = useState<any>({});
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const configure = perms.has("integrations.configure");
  const google = data.google;
  return (
    <div className="page">
      <PageHead title={tr("Integrations")} sub={tr("Google sign-in, Stripe, e-mail, analytics and storage. Secrets are write-only.")} />
      <Section title={tr("Google authentication")} actions={configure ? <button className="btn primary" disabled={busy || !Object.keys(g).length} onClick={() => void run(() => put("/api/owner/integrations/google", g), tr("Google settings saved")).then(() => { setG({}); reload(); })}>{tr("Save")}</button> : null}>
        <p className="small">{google.available ? <Tag text={tr("available to users")} tone="green" /> : <Tag text={tr("not available")} tone="amber" />} {tr("Redirect URI to register in Google Cloud")}: <code>{google.redirect_uri}</code>
          {google.credentials?.source === "environment" && <> · {tr("credentials come from the environment")}</>}</p>
        <div className="grid2">
          <Field label={tr("OAuth client ID")}><input className="input" disabled={!configure} value={g.client_id ?? google.credentials?.client_id ?? ""} onChange={(e) => setG({ ...g, client_id: e.target.value })} /></Field>
          <Field label={tr("OAuth client secret (write-only)")}><input className="input" type="password" disabled={!configure} value={g.client_secret ?? ""} onChange={(e) => setG({ ...g, client_secret: e.target.value })} placeholder={google.credentials?.configured ? tr("saved — enter to replace") : ""} /></Field>
          <Field label={tr("Registration with Google")}><select className="select" disabled={!configure} value={g.registration ?? google["google.registration"]} onChange={(e) => setG({ ...g, registration: e.target.value })}>
            <option value="follow">{tr("Follow the platform's registration policy")}</option><option value="open">{tr("Open")}</option><option value="invite_only">{tr("Invitation only")}</option><option value="disabled">{tr("No new accounts via Google")}</option></select></Field>
          <Field label={tr("Link an existing account by verified e-mail")}><select className="select" disabled={!configure} value={g.link_existing ?? google["google.link_existing"]} onChange={(e) => setG({ ...g, link_existing: e.target.value })}>
            <option value="verified_email">{tr("Yes, when Google reports the address verified")}</option><option value="never">{tr("Never: sign in with the password first")}</option></select></Field>
          <Field label={tr("Allowed e-mail domains (comma-separated, blank = any)")}><input className="input" disabled={!configure} value={(g.allowed_domains ?? google["google.allowed_domains"] ?? []).join(", ")} onChange={(e) => setG({ ...g, allowed_domains: e.target.value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean) })} /></Field>
        </div>
        <Toggle label={tr("Google sign-in enabled")} disabled={!configure} checked={g.enabled ?? google["google.enabled"]} onChange={(x) => setG({ ...g, enabled: x })} />
        <p className="small muted">{tr("Authorization Code flow with PKCE, state and verified-e-mail checks; accounts are never merged on an unverified claim.")}</p>
      </Section>
      <div className="grid2">
        <Section title={tr("Stripe")}><p className="small">{data.stripe.ready ? <Tag text={tr("ready")} tone="green" /> : <Tag text={tr("not configured")} tone="amber" />} {tr("{0} mode", { 0: data.stripe.mode })} · <a href="#/owner/billing">{tr("Billing settings")}</a></p></Section>
        <Section title={tr("E-mail")}><p className="small">{data.email.configured ? <Tag text={tr("configured")} tone="green" /> : <Tag text={tr("not configured")} tone="amber" />} {data.email.host} · <a href="#/owner/email">{tr("Email settings")}</a></p></Section>
        <Section title={tr("Analytics")} actions={configure ? <button className="btn small" disabled={busy || !Object.keys(a).length} onClick={() => void run(() => put("/api/owner/integrations/analytics", a), tr("Analytics settings saved")).then(() => { setA({}); reload(); })}>{tr("Save")}</button> : null}>
          <Toggle label={tr("Collect website and product analytics")} disabled={!configure} checked={a.enabled ?? data.analytics["analytics.enabled"]} onChange={(x) => setA({ ...a, enabled: x })} />
          <Toggle label={tr("Require visitor consent before collecting")} disabled={!configure} checked={a.require_consent ?? data.analytics["analytics.require_consent"]} onChange={(x) => setA({ ...a, require_consent: x })} />
          <Toggle label={tr("Record country (from the proxy's header)")} disabled={!configure} checked={a.collect_country ?? data.analytics["analytics.collect_country"]} onChange={(x) => setA({ ...a, collect_country: x })} />
          <Field label={tr("Retention (days)")}><input className="input" type="number" min={30} max={1825} disabled={!configure} value={a.retention_days ?? data.analytics["analytics.retention_days"]} onChange={(e) => setA({ ...a, retention_days: Number(e.target.value) })} /></Field>
        </Section>
        <Section title={tr("Storage provider")}><p className="small">{data.storage.kind === "s3" ? `S3 · ${data.storage.bucket} · ${data.storage.endpoint || "AWS"}` : tr("Local disk")} · {data.storage.encrypted ? tr("encrypted at rest") : tr("not encrypted")} · <a href="#/owner/storage">{tr("Storage")}</a></p>
          <p className="small muted">{tr("The provider is set in the server environment (DESK_STORAGE, DESK_S3_*).")}</p></Section>
      </div>
    </div>
  );
}

export function StoragePage(): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data, error, reload } = useLoad(() => get("/api/owner/storage"), []);
  const [f, setF] = useState<any>({});
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const configure = perms.has("storage.configure");
  const s = data.settings;
  return (
    <div className="page">
      <PageHead title={tr("Storage")} sub={tr("Hosted file storage per workspace: usage, quotas and the over-quota policy. Files are encrypted before they are stored.")} />
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <Kpi label={tr("Total stored")} value={data.totals.total} format="bytes" />
        <Kpi label={tr("Documents")} value={data.totals.document_count} />
        <Kpi label={tr("Editable files")} value={data.totals.file_count} />
        <Kpi label={tr("Provider")} value={`${data.provider.kind}${data.provider.encrypted ? ` · ${tr("encrypted")}` : ""}`} format="text" />
      </div>
      <Section title={tr("Policy")} actions={configure ? <button className="btn primary" disabled={busy || !Object.keys(f).length} onClick={() => void run(() => put("/api/owner/storage", f), tr("Storage policy saved")).then(() => { setF({}); reload(); })}>{tr("Save")}</button> : null}>
        <div className="grid3">
          <Field label={tr("When a workspace exceeds its quota")}><select className="select" disabled={!configure} value={f.over_quota_policy ?? s["storage.over_quota_policy"]} onChange={(e) => setF({ ...f, over_quota_policy: e.target.value })}>
            <option value="read_only">{tr("Refuse new uploads at once")}</option><option value="grace">{tr("Grace period, then read-only")}</option></select></Field>
          <Field label={tr("Grace period (days)")}><input className="input" type="number" min={1} max={90} disabled={!configure} value={f.grace_days ?? s["storage.grace_days"]} onChange={(e) => setF({ ...f, grace_days: Number(e.target.value) })} /></Field>
          <Field label={tr("Retention after deletion (days)")}><input className="input" type="number" min={0} max={365} disabled={!configure} value={f.retention_days_after_delete ?? s["storage.retention_days_after_delete"]} onChange={(e) => setF({ ...f, retention_days_after_delete: Number(e.target.value) })} /></Field>
          <Field label={tr("Maximum file size (MB, blank = engine default {0})", { 0: data.provider.max_upload_mb })}><input className="input" type="number" min={1} max={200} disabled={!configure} value={f.max_file_mb ?? s["storage.max_file_mb"] ?? ""} onChange={(e) => setF({ ...f, max_file_mb: Number(e.target.value) })} /></Field>
        </div>
      </Section>
      <Section title={tr("Usage by workspace")}>
        <Records rows={data.workspaces.map((w: any) => ({ id: w.workspace_id, ...w }))} onRow={(w: any) => { location.hash = `#/owner/workspaces/${w.workspace_id}`; }} cols={[{ key: "name", label: tr("Workspace") },
          { key: "documents", label: tr("Documents"), num: true, render: (w: any) => bytes(w.documents) }, { key: "files", label: tr("Files"), num: true, render: (w: any) => bytes(w.files) },
          { key: "total", label: tr("Total"), num: true, render: (w: any) => bytes(w.total) }, { key: "quota", label: tr("Quota"), num: true, render: (w: any) => w.quota ? bytes(w.quota) : tr("unlimited") },
          { key: "percent", label: tr("Used"), render: (w: any) => w.percent === null ? "—" : <><div className="split" style={{ width: 120, display: "inline-flex" }}><div className="split-a" style={{ width: `${Math.min(100, w.percent)}%` }} /></div> {w.percent}%{w.over && <> <Tag text={tr("over quota")} tone="red" /></>}</> }]} />
      </Section>
    </div>
  );
}

export function AuditPage(): ReactNode {
  const [action, setAction] = useState("");
  const [decision, setDecision] = useState("");
  const [user, setUser] = useState("");
  const [ws, setWs] = useState("");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ action, decision, user_id: user, workspace_id: ws, limit: "100", offset: String(offset) });
  const { data, error } = useLoad(() => get(`/api/owner/audit?${params}`), [params.toString()]);
  return (
    <div className="page">
      <PageHead title={tr("Audit Logs")} sub={tr("Every security-relevant event on the platform, immutable: who, what, where, the decision and the outcome. Secrets and financial figures are never written to it.")} />
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <input className="input" style={{ maxWidth: 220 }} list="audit-actions" placeholder={tr("Action (prefix)")} value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }} />
        <datalist id="audit-actions">{(data?.actions || []).map((a: string) => <option key={a} value={a} />)}</datalist>
        <select className="select" style={{ width: 150 }} value={decision} onChange={(e) => setDecision(e.target.value)} aria-label={tr("Decision")}><option value="">{tr("Any decision")}</option><option value="allowed">{tr("allowed")}</option><option value="denied">{tr("denied")}</option></select>
        <input className="input" style={{ maxWidth: 200 }} placeholder={tr("User id")} value={user} onChange={(e) => setUser(e.target.value)} />
        <input className="input" style={{ maxWidth: 200 }} placeholder={tr("Workspace id")} value={ws} onChange={(e) => setWs(e.target.value)} />
      </div>
      <ErrorBox error={error} />
      {!data ? <Loading /> : <>
        <Records rows={data.items} cols={[{ key: "created_at", label: tr("When"), render: (e: any) => fmtDateTime(e.created_at) }, { key: "action", label: tr("Action") },
          { key: "email", label: tr("By"), render: (e: any) => e.email || e.user_id || "—" }, { key: "workspace_id", label: tr("Workspace"), render: (e: any) => e.workspace_id || "—" },
          { key: "target", label: tr("Target"), render: (e: any) => <span className="small">{e.target || "—"}</span> },
          { key: "decision", label: tr("Decision"), render: (e: any) => e.decision ? <Tag text={e.decision} tone={e.decision === "denied" ? "red" : "green"} /> : "—" },
          { key: "result", label: tr("Result"), render: (e: any) => e.result || "—" },
          { key: "detail", label: tr("Detail"), render: (e: any) => <code className="small">{e.detail ? JSON.stringify(e.detail).slice(0, 120) : "—"}</code> },
          { key: "event_id", label: tr("Event id"), render: (e: any) => <span className="small muted">{e.event_id || e.id}</span> }]} empty={tr("No entries match.")} />
        <Pager total={data.total} limit={100} offset={offset} onChange={setOffset} />
      </>}
    </div>
  );
}

export function SettingsPage(): ReactNode {
  const { session, reloadSession } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data, error, reload } = useLoad(() => get("/api/owner/settings"), []);
  const [f, setF] = useState<any>({});
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const manage = perms.has("settings.manage");
  const g = data.general;
  const val = (k: string) => (k in f ? f[k] : g[k]);
  const flags = f["features.flags"] ?? data.features["features.flags"] ?? {};
  const exp = f["features.experimental"] ?? data.features["features.experimental"] ?? {};
  const save = () => void run(() => put("/api/owner/settings", { values: f }), tr("Settings saved")).then(() => { setF({}); reload(); void reloadSession(); });
  return (
    <div className="page">
      <PageHead title={tr("Platform Settings")} sub={tr("Branding, languages, registration, maintenance mode and feature flags. Flags switch optional product features; they can never bypass authentication, tenant isolation or any security control.")}
        actions={manage ? <button className="btn primary" disabled={busy || !Object.keys(f).length} onClick={save}>{tr("Save")}</button> : undefined} />
      <Section title={tr("General")}>
        <div className="grid2">
          <Field label={tr("Platform name")}><input className="input" disabled={!manage} value={val("platform.name")} onChange={(e) => setF({ ...f, "platform.name": e.target.value })} /></Field>
          <Field label={tr("Logo URL (https or a path on this server)")}><input className="input" disabled={!manage} value={val("platform.logo_url")} onChange={(e) => setF({ ...f, "platform.logo_url": e.target.value })} /></Field>
          <Field label={tr("Support e-mail")}><input className="input" disabled={!manage} value={val("platform.support_email")} onChange={(e) => setF({ ...f, "platform.support_email": e.target.value })} /></Field>
          <Field label={tr("Default time zone")}><input className="input" disabled={!manage} value={val("platform.default_timezone")} onChange={(e) => setF({ ...f, "platform.default_timezone": e.target.value })} /></Field>
          <Field label={tr("Default language")}><select className="select" disabled={!manage} value={val("platform.default_language")} onChange={(e) => setF({ ...f, "platform.default_language": e.target.value })}>
            {["en", "ar", "es", "fr"].map((l) => <option key={l} value={l}>{l}</option>)}</select></Field>
          <Field label={tr("Supported languages")}><div className="row wrap">{["en", "ar", "es", "fr"].map((l) => <Toggle key={l} label={l} disabled={!manage} checked={(val("platform.supported_languages") || []).includes(l)}
            onChange={(x) => { const cur: string[] = val("platform.supported_languages") || []; setF({ ...f, "platform.supported_languages": x ? [...cur, l] : cur.filter((y) => y !== l) }); }} />)}</div></Field>
        </div>
        <div className="row wrap">
          <Toggle label={tr("Maintenance mode (everyone but platform administrators is refused)")} disabled={!manage} checked={Boolean(val("platform.maintenance_mode"))} onChange={(x) => setF({ ...f, "platform.maintenance_mode": x })} />
          <Toggle label={tr("Public registration enabled")} disabled={!manage} checked={val("registration.enabled") ?? data.environment.open_signup_env} onChange={(x) => setF({ ...f, "registration.enabled": x })} />
          <Toggle label={tr("Require a verified e-mail before full use")} disabled={!manage} checked={Boolean(val("registration.require_verified_email"))} onChange={(x) => setF({ ...f, "registration.require_verified_email": x })} />
        </div>
        <div className="grid2">
          <Field label={tr("Maintenance message")}><input className="input" disabled={!manage} value={val("platform.maintenance_message")} onChange={(e) => setF({ ...f, "platform.maintenance_message": e.target.value })} /></Field>
          <Field label={tr("Registration limited to e-mail domains (comma-separated)")}><input className="input" disabled={!manage} value={(val("registration.allowed_domains") || []).join(", ")} onChange={(e) => setF({ ...f, "registration.allowed_domains": e.target.value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean) })} /></Field>
        </div>
      </Section>
      <Section title={tr("Feature flags")}>
        <div className="grid3">{data.feature_keys.map((k: string) => <Toggle key={k} label={tr(k.replace(/_/g, " "))} disabled={!manage} checked={flags[k] !== false} onChange={(x) => setF({ ...f, "features.flags": { ...flags, [k]: x } })} />)}</div>
        <h4>{tr("Experimental")}</h4>
        <div className="grid3">{["new_overview", "forecast_v2", "assistant_memory_v2"].map((k) => <Toggle key={k} label={tr(k.replace(/_/g, " "))} disabled={!manage} checked={Boolean(exp[k])} onChange={(x) => setF({ ...f, "features.experimental": { ...exp, [k]: x } })} />)}</div>
        <p className="small muted">{tr("Switching a flag off refuses the feature on the server for everyone, whatever the plan.")}</p>
      </Section>
      <Section title={tr("Environment (read-only)")}>
        <dl className="small">{Object.entries(data.environment).map(([k, v]) => <div key={k}><dt style={{ display: "inline", fontWeight: 600 }}>{k}</dt>: <dd style={{ display: "inline" }}>{String(v)}</dd></div>)}</dl>
      </Section>
    </div>
  );
}
