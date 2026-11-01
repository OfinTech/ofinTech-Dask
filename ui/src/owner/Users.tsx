import { useState, type ReactNode } from "react";
import { get, post, put } from "../lib/api";
import { useApp } from "../lib/app";
import { Confirm, ErrorBox, Field, Loading, Modal, PageHead, useAction, useLoad } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { tr } from "../lib/i18n";
import { goOwner } from "./OwnerApp";
import { Pager, Records, Section, Tag, Toggle, When, bytes, toneFor } from "./shared";

const LIMIT = 50;

export function UsersPage({ id }: { id?: string }): ReactNode {
  if (id) return <UserDetail id={id} />;
  return <UserDirectory />;
}

function UserDirectory(): ReactNode {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [plan, setPlan] = useState("");
  const [mfa, setMfa] = useState("");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ q, status, plan, mfa, limit: String(LIMIT), offset: String(offset) });
  const { data, error } = useLoad(() => get(`/api/owner/users?${params}`), [params.toString()]);
  const { data: plans } = useLoad(() => get("/api/owner/plans"), []);
  return (
    <div className="page">
      <PageHead title={tr("Users")} sub={tr("Every account on the platform. Passwords, tokens and workspace contents are never shown here.")}
        actions={<a className="btn small" href="/api/owner/export/users.csv" download>{tr("Export CSV")}</a>} />
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <input className="input" style={{ maxWidth: 280 }} placeholder={tr("Search name, e-mail or id")} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
        <select className="select" style={{ width: 160 }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label={tr("Account status")}>
          <option value="">{tr("Any status")}</option><option value="active">{tr("Active")}</option><option value="suspended">{tr("Suspended")}</option></select>
        <select className="select" style={{ width: 160 }} value={plan} onChange={(e) => setPlan(e.target.value)} aria-label={tr("Plan")}>
          <option value="">{tr("Any plan")}</option>{(plans?.items || []).map((p: any) => <option key={p.code} value={p.code}>{p.name}</option>)}</select>
        <select className="select" style={{ width: 140 }} value={mfa} onChange={(e) => setMfa(e.target.value)} aria-label={tr("MFA")}>
          <option value="">{tr("MFA: any")}</option><option value="on">{tr("MFA on")}</option><option value="off">{tr("MFA off")}</option></select>
      </div>
      <ErrorBox error={error} />
      {!data ? <Loading /> : <>
        <Records rows={data.items} onRow={(u: any) => goOwner(`users/${u.id}`)} cols={[
          { key: "name", label: tr("Name"), render: (u: any) => <><strong>{u.name}</strong><br /><span className="small muted">{u.email}</span></> },
          { key: "created_at", label: tr("Registered"), render: (u: any) => <><When ts={u.created_at} /><br /><span className="small muted">{u.signup_method}</span></> },
          { key: "email_verified", label: tr("Verified"), render: (u: any) => u.email_verified ? <Tag text={tr("yes")} tone="green" /> : <Tag text={tr("no")} tone="amber" /> },
          { key: "plan", label: tr("Plan"), render: (u: any) => <>{u.plan || "—"}{u.subscription_status ? <> <Tag text={u.subscription_status} tone={toneFor(u.subscription_status)} /></> : null}</> },
          { key: "workspaces", label: tr("Workspaces"), num: true, render: (u: any) => `${u.owned_workspaces} / ${u.workspaces}` },
          { key: "storage_bytes", label: tr("Storage"), num: true, render: (u: any) => bytes(u.storage_bytes) },
          { key: "last_login_at", label: tr("Last sign-in"), render: (u: any) => <When ts={u.last_login_at} /> },
          { key: "last_activity_at", label: tr("Last activity"), render: (u: any) => <When ts={u.last_activity_at} /> },
          { key: "disabled", label: tr("Status"), render: (u: any) => u.disabled ? <Tag text={tr("suspended")} tone="red" /> : <Tag text={tr("active")} tone="green" /> },
          { key: "mfa_enabled", label: tr("MFA"), render: (u: any) => u.mfa_enabled ? <Tag text={tr("on")} tone="green" /> : <Tag text={tr("off")} /> },
        ]} empty={tr("No accounts match.")} />
        <Pager total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
      </>}
    </div>
  );
}

function UserDetail({ id }: { id: string }): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data: u, error, reload } = useLoad(() => get(`/api/owner/users/${id}`), [id]);
  const { data: plans } = useLoad(() => get("/api/owner/plans"), []);
  const { busy, run } = useAction();
  const [suspend, setSuspend] = useState(false);
  const [reason, setReason] = useState("");
  const [assign, setAssign] = useState(false);
  const [planId, setPlanId] = useState("");
  const [months, setMonths] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [overrides, setOverrides] = useState<string | null>(null);
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!u) return <div className="page"><Loading /></div>;
  const manage = perms.has("users.manage");
  const subs = perms.has("subscriptions.manage");
  const act = (fn: () => Promise<unknown>, ok?: string) => void run(fn, ok).then(reload);
  return (
    <div className="page">
      <PageHead title={u.name} sub={<>{u.email} · <span className="small">{tr("id")} {u.id}</span></>} actions={<a className="btn small" href="#/owner/users">{tr("All users")}</a>} />
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <div className="kpi-card"><span className="kpi-name">{tr("Status")}</span><span className="kpi-value">{u.disabled ? tr("Suspended") : tr("Active")}</span>
          {u.suspended_reason && <span className="small muted">{u.suspended_reason}</span>}</div>
        <div className="kpi-card"><span className="kpi-name">{tr("Plan")}</span><span className="kpi-value">{u.plan || "—"}</span><span className="small muted">{u.subscription_status || ""}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Workspaces owned / member")}</span><span className="kpi-value">{u.owned_workspaces} / {u.workspaces}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Storage")}</span><span className="kpi-value">{bytes(u.storage_bytes)}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Security")}</span><span className="kpi-value">{u.mfa_enabled ? tr("MFA on") : tr("MFA off")}</span>
          <span className="small muted">{u.email_verified ? tr("e-mail verified") : tr("e-mail not verified")} · {tr("{0} sessions", { 0: u.active_sessions })}</span></div>
      </div>
      {manage && <Section title={tr("Actions")}>
        <div className="row wrap">
          {u.disabled ? <button className="btn" disabled={busy} onClick={() => act(() => post(`/api/owner/users/${id}/reactivate`), tr("Account reactivated"))}>{tr("Reactivate account")}</button>
            : <button className="btn danger" disabled={busy || u.id === session.user?.id} onClick={() => setSuspend(true)}>{tr("Suspend account")}</button>}
          <button className="btn" disabled={busy} onClick={() => act(() => post(`/api/owner/users/${id}/sign-out`), tr("Sessions revoked"))}>{tr("Revoke all sessions")}</button>
          <button className="btn" disabled={busy} onClick={() => setConfirmReset(true)}>{tr("Require password reset")}</button>
          {u.mfa_enabled && <button className="btn" disabled={busy} onClick={() => act(() => post(`/api/owner/users/${id}/reset-mfa`), tr("Authenticator reset"))}>{tr("Reset MFA")}</button>}
          {subs && <button className="btn" onClick={() => setAssign(true)}>{tr("Assign plan")}</button>}
          {subs && <button className="btn" onClick={() => setOverrides(JSON.stringify(u.overrides ? JSON.parse(u.overrides.overrides) : {}, null, 2))}>{tr("Entitlement overrides")}</button>}
        </div>
        <div className="row wrap" style={{ marginTop: 8 }}>
          <Toggle label={tr("Require MFA enrolment")} checked={Boolean(u.restrictions.mfa_required)} onChange={(v) => act(() => put(`/api/owner/users/${id}/restrictions`, { mfa_required: v }))} />
          <Toggle label={tr("Read-only account")} checked={Boolean(u.restrictions.read_only)} onChange={(v) => act(() => put(`/api/owner/users/${id}/restrictions`, { read_only: v }))} />
          <Toggle label={tr("No new workspaces")} checked={Boolean(u.restrictions.no_new_workspaces)} onChange={(v) => act(() => put(`/api/owner/users/${id}/restrictions`, { no_new_workspaces: v }))} />
        </div>
      </Section>}
      <div className="grid2">
        <Section title={tr("Workspaces")}>
          <Records rows={u.memberships} onRow={(m: any) => goOwner(`workspaces/${m.workspace_id}`)} cols={[{ key: "name", label: tr("Workspace") }, { key: "role", label: tr("Role") },
            { key: "created_at", label: tr("Since"), render: (m: any) => <When ts={m.created_at} /> }]} />
        </Section>
        <Section title={tr("Entitlements")}>
          <ul className="small">
            <li>{tr("Plan")}: <strong>{u.entitlements.plan_code || tr("none")}</strong>{u.entitlements.overrides && <> · <Tag text={tr("overrides")} tone="amber" /></>}</li>
            <li>{tr("Workspaces")}: {u.entitlements.max_workspaces || tr("unlimited")}</li>
            <li>{tr("Storage quota")}: {u.entitlements.storage_quota_mb ? `${u.entitlements.storage_quota_mb} MB` : tr("unlimited")}</li>
            <li>{tr("Members per workspace")}: {u.entitlements.max_members || tr("unlimited")}</li>
            <li>{tr("Features")}: {Object.entries(u.entitlements.features).filter(([k, v]) => v === true && k !== "ai_monthly_tokens").map(([k]) => k).join(", ") || "—"}</li>
          </ul>
        </Section>
        <Section title={tr("Subscription history")}>
          <Records rows={u.subscriptions} cols={[{ key: "plan_code", label: tr("Plan") }, { key: "status", label: tr("Status"), render: (s: any) => <Tag text={s.status} tone={toneFor(s.status)} /> },
            { key: "source", label: tr("Source") }, { key: "interval", label: tr("Interval") }, { key: "current_period_end", label: tr("Period end"), render: (s: any) => s.current_period_end ? fmtDateTime(s.current_period_end) : "—" },
            { key: "created_at", label: tr("Since"), render: (s: any) => <When ts={s.created_at} /> }]} empty={tr("No subscriptions.")} />
        </Section>
        <Section title={tr("Invoices")}>
          <Records rows={u.invoices} cols={[{ key: "status", label: tr("Status"), render: (i: any) => <Tag text={i.status} tone={toneFor(i.status)} /> },
            { key: "amount_paid", label: tr("Paid"), num: true, render: (i: any) => `${(i.amount_paid / 100).toFixed(2)} ${i.currency}` },
            { key: "created", label: tr("Date"), render: (i: any) => <When ts={i.created} /> },
            { key: "hosted_invoice_url", label: tr("Invoice"), render: (i: any) => i.hosted_invoice_url ? <a href={i.hosted_invoice_url} target="_blank" rel="noreferrer">{tr("Open")}</a> : "—" }]} empty={tr("No invoices.")} />
        </Section>
        <Section title={tr("Authentication history")}>
          <Records rows={u.auth_history} cols={[{ key: "action", label: tr("Event") }, { key: "result", label: tr("Result"), render: (e: any) => <Tag text={e.result || e.decision || "—"} tone={toneFor(e.result || e.decision || "")} /> },
            { key: "created_at", label: tr("When"), render: (e: any) => fmtDateTime(e.created_at) }]} empty={tr("Nothing recorded.")} />
        </Section>
        <Section title={tr("Security events")}>
          <Records rows={u.security_events} cols={[{ key: "action", label: tr("Event") }, { key: "target", label: tr("Target") },
            { key: "created_at", label: tr("When"), render: (e: any) => fmtDateTime(e.created_at) }]} empty={tr("No refusals recorded.")} />
        </Section>
        <Section title={tr("Sessions")}>
          <Records rows={u.sessions} cols={[{ key: "user_agent", label: tr("Device") }, { key: "created_at", label: tr("Signed in"), render: (s: any) => <When ts={s.created_at} /> },
            { key: "last_seen", label: tr("Last activity"), render: (s: any) => <When ts={s.last_seen} /> }, { key: "mfa_at", label: tr("MFA"), render: (s: any) => s.mfa_at ? tr("yes") : tr("no") }]} empty={tr("No active sessions.")} />
        </Section>
      </div>
      {suspend && <Modal title={tr("Suspend account")} onClose={() => setSuspend(false)} footer={<>
        <button className="btn" onClick={() => setSuspend(false)}>{tr("Cancel")}</button>
        <button className="btn danger" disabled={reason.trim().length < 3} onClick={() => { act(() => post(`/api/owner/users/${id}/suspend`, { reason }), tr("Account suspended")); setSuspend(false); }}>{tr("Suspend")}</button></>}>
        <p>{tr("The person is signed out everywhere and cannot sign in until reactivated. Their data is kept.")}</p>
        <Field label={tr("Reason (shown to the person)")}><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </Modal>}
      {confirmReset && <Confirm text={tr("End every session and require a new password at the next sign-in? A reset link is e-mailed to the person.")} yes={tr("Require reset")}
        onClose={() => setConfirmReset(false)} onYes={() => act(() => post(`/api/owner/users/${id}/require-password-reset`), tr("Password reset required"))} />}
      {assign && <Modal title={tr("Assign plan")} onClose={() => setAssign(false)} footer={<>
        <button className="btn" onClick={() => setAssign(false)}>{tr("Cancel")}</button>
        <button className="btn primary" disabled={!planId} onClick={() => { act(() => post(`/api/owner/users/${id}/subscription`, { plan_id: planId, months: months ? Number(months) : null, note: "owner assignment" }), tr("Plan assigned")); setAssign(false); }}>{tr("Assign")}</button></>}>
        <p className="muted small">{tr("For contracts and courtesy upgrades. An account with a Stripe subscription is changed through billing instead.")}</p>
        <Field label={tr("Plan")}><select className="select" value={planId} onChange={(e) => setPlanId(e.target.value)}><option value="">—</option>
          {(plans?.items || []).map((p: any) => <option key={p.id} value={p.id}>{p.name} ({p.code})</option>)}</select></Field>
        <Field label={tr("Months (blank = until cancelled)")}><input className="input" type="number" min={1} value={months} onChange={(e) => setMonths(e.target.value)} /></Field>
      </Modal>}
      {overrides !== null && <Modal title={tr("Entitlement overrides")} onClose={() => setOverrides(null)} footer={<>
        <button className="btn" onClick={() => setOverrides(null)}>{tr("Cancel")}</button>
        <button className="btn danger" onClick={() => { act(() => put(`/api/owner/users/${id}/entitlements`, { overrides: {} }), tr("Overrides cleared")); setOverrides(null); }}>{tr("Clear")}</button>
        <button className="btn primary" onClick={() => { let v: any; try { v = JSON.parse(overrides); } catch { return; }
          act(() => put(`/api/owner/users/${id}/entitlements`, { overrides: v, note: "owner override" }), tr("Overrides saved")); setOverrides(null); }}>{tr("Save")}</button></>}>
        <p className="muted small">{tr("JSON with any of max_workspaces, storage_quota_mb, max_members and features (e.g. {\"features\": {\"api_access\": true}}). 0 means unlimited.")}</p>
        <textarea className="textarea" rows={8} value={overrides} onChange={(e) => setOverrides(e.target.value)} />
      </Modal>}
    </div>
  );
}
