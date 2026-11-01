import { useState, type ReactNode } from "react";
import { del, get, patch, post } from "../lib/api";
import { useApp } from "../lib/app";
import { Confirm, ErrorBox, Field, Loading, Modal, PageHead, useAction, useLoad } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { tr } from "../lib/i18n";
import { goOwner } from "./OwnerApp";
import { Pager, Records, Section, Tag, When, bytes, toneFor } from "./shared";

const LIMIT = 50;

export function WorkspacesPage({ id }: { id?: string }): ReactNode {
  return id ? <WorkspaceDetail id={id} /> : <WorkspaceList />;
}

function Usage({ s }: { s: any }): ReactNode {
  return <>{bytes(s.total)}{s.quota ? <span className="small muted"> / {bytes(s.quota)} ({s.percent}%)</span> : null}{s.over && <> <Tag text={tr("over quota")} tone="red" /></>}</>;
}

function WorkspaceList(): ReactNode {
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ q, state, limit: String(LIMIT), offset: String(offset) });
  const { data, error } = useLoad(() => get(`/api/owner/workspaces?${params}`), [params.toString()]);
  return (
    <div className="page">
      <PageHead title={tr("Workspaces")} sub={tr("Ownership, members, plan, state and storage of every workspace. Contents stay inside the workspace.")} />
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <input className="input" style={{ maxWidth: 280 }} placeholder={tr("Search name or id")} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
        <select className="select" style={{ width: 170 }} value={state} onChange={(e) => setState(e.target.value)} aria-label={tr("State")}>
          <option value="">{tr("Any state")}</option>{["active", "read_only", "suspended", "archived"].map((s) => <option key={s} value={s}>{tr(s.replace("_", " "))}</option>)}</select>
      </div>
      <ErrorBox error={error} />
      {!data ? <Loading /> : <>
        <Records rows={data.items} onRow={(w: any) => goOwner(`workspaces/${w.id}`)} cols={[
          { key: "name", label: tr("Workspace"), render: (w: any) => <><strong>{w.name}</strong><br /><span className="small muted">{w.base_currency} · {w.country || "—"}</span></> },
          { key: "owner", label: tr("Owner"), render: (w: any) => w.owner ? <>{w.owner.name}<br /><span className="small muted">{w.owner.email}</span></> : "—" },
          { key: "members", label: tr("Members"), num: true },
          { key: "plan", label: tr("Plan"), render: (w: any) => w.plan || "—" },
          { key: "state", label: tr("State"), render: (w: any) => <Tag text={tr(w.state.status.replace("_", " "))} tone={toneFor(w.state.status)} /> },
          { key: "storage", label: tr("Storage"), render: (w: any) => <Usage s={w.storage} /> },
          { key: "created_at", label: tr("Created"), render: (w: any) => <When ts={w.created_at} /> },
          { key: "last_activity_at", label: tr("Last activity"), render: (w: any) => <When ts={w.last_activity_at} /> },
        ]} empty={tr("No workspaces match.")} />
        <Pager total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
      </>}
    </div>
  );
}

function WorkspaceDetail({ id }: { id: string }): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data: w, error, reload } = useLoad(() => get(`/api/owner/workspaces/${id}`), [id]);
  const { busy, run } = useAction();
  const [stateChange, setStateChange] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [support, setSupport] = useState(false);
  const [supportReason, setSupportReason] = useState("");
  const [hours, setHours] = useState("4");
  const [removing, setRemoving] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!w) return <div className="page"><Loading /></div>;
  const act = (fn: () => Promise<unknown>, ok?: string) => void run(fn, ok).then(reload);
  const manage = perms.has("workspaces.manage");
  return (
    <div className="page">
      <PageHead title={w.name} sub={<>{tr("Owner")}: {w.owner ? `${w.owner.name} (${w.owner.email})` : "—"} · {tr("id")} {w.id}</>}
        actions={<a className="btn small" href="#/owner/workspaces">{tr("All workspaces")}</a>} />
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <div className="kpi-card"><span className="kpi-name">{tr("State")}</span><span className="kpi-value">{tr(w.state.status.replace("_", " "))}</span>{w.state.reason && <span className="small muted">{w.state.reason}</span>}</div>
        <div className="kpi-card"><span className="kpi-name">{tr("Plan")}</span><span className="kpi-value">{w.plan || "—"}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Members")}</span><span className="kpi-value">{w.members.length}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Storage")}</span><span className="kpi-value">{bytes(w.storage.total)}</span><span className="small muted"><Usage s={w.storage} /></span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("AI tokens (30 days)")}</span><span className="kpi-value">{Intl.NumberFormat().format(w.ai_tokens_30d || 0)}</span></div>
      </div>
      <Section title={tr("Usage (counts only — no contents are shown)")}>
        <div className="grid3 small">
          {Object.entries(w.counts).map(([k, v]) => <div key={k}><strong>{String(v)}</strong> {tr(k.replace(/_/g, " "))}</div>)}
        </div>
      </Section>
      {manage && <Section title={tr("Administration")}>
        <div className="row wrap">
          {w.state.status !== "active" && <button className="btn" disabled={busy} onClick={() => act(() => post(`/api/owner/workspaces/${id}/state`, { status: "active" }), tr("Workspace restored"))}>{tr("Restore")}</button>}
          {w.state.status !== "read_only" && <button className="btn" onClick={() => setStateChange("read_only")}>{tr("Make read-only")}</button>}
          {w.state.status !== "suspended" && <button className="btn danger" onClick={() => setStateChange("suspended")}>{tr("Suspend")}</button>}
          {w.state.status !== "archived" && <button className="btn" onClick={() => setStateChange("archived")}>{tr("Archive")}</button>}
          {perms.has("support.access") && <button className="btn" onClick={() => setSupport(true)}>{tr("Request support access")}</button>}
          {perms.has("workspaces.delete") && w.state.status === "archived" && <button className="btn danger" onClick={() => setDeleting(true)}>{tr("Delete permanently")}</button>}
        </div>
        <p className="small muted">{tr("Read-only keeps everything visible but refuses changes; suspended and archived refuse every request. Deletion needs the workspace archived first and cannot be undone.")}</p>
      </Section>}
      <div className="grid2">
        <Section title={tr("Members")}>
          <Records rows={w.members} cols={[{ key: "name", label: tr("Member"), render: (m: any) => <>{m.name}<br /><span className="small muted">{m.email}</span></> },
            { key: "role", label: tr("Role"), render: (m: any) => manage ? <select className="select" value={m.role} aria-label={tr("Role")}
              onChange={(e) => act(() => patch(`/api/owner/workspaces/${id}/members/${m.user_id}`, { role: e.target.value }), tr("Role changed"))}>
              {["viewer", "editor", "admin", "owner"].map((r) => <option key={r} value={r}>{tr(r)}</option>)}</select> : m.role },
            { key: "created_at", label: tr("Since"), render: (m: any) => <When ts={m.created_at} /> },
            { key: "x", label: "", render: (m: any) => manage ? <button className="btn ghost small" onClick={() => setRemoving(m.user_id)}>{tr("Remove")}</button> : null }]} />
        </Section>
        <Section title={tr("Support access")}>
          <Records rows={w.support_grants} cols={[{ key: "admin_email", label: tr("Administrator") }, { key: "reason", label: tr("Reason") },
            { key: "granted_at", label: tr("Granted"), render: (g: any) => <When ts={g.granted_at} /> },
            { key: "expires_at", label: tr("Expires"), render: (g: any) => g.revoked_at ? <Tag text={tr("revoked")} /> : g.expires_at < Date.now() / 1000 ? <Tag text={tr("expired")} /> : fmtDateTime(g.expires_at) },
            { key: "x", label: "", render: (g: any) => !g.revoked_at && g.expires_at > Date.now() / 1000 ? <button className="btn ghost small" onClick={() => act(() => del(`/api/owner/support-access/${g.id}`), tr("Access revoked"))}>{tr("Revoke")}</button> : null }]}
            empty={tr("No support access has been granted.")} />
        </Section>
        <Section title={tr("Security events")}>
          <Records rows={w.security_events} cols={[{ key: "action", label: tr("Event") }, { key: "user_id", label: tr("Account") },
            { key: "created_at", label: tr("When"), render: (e: any) => fmtDateTime(e.created_at) }]} empty={tr("No refusals recorded.")} />
        </Section>
      </div>
      {stateChange && <Modal title={tr("Change workspace state")} onClose={() => setStateChange(null)} footer={<>
        <button className="btn" onClick={() => setStateChange(null)}>{tr("Cancel")}</button>
        <button className="btn danger" onClick={() => { act(() => post(`/api/owner/workspaces/${id}/state`, { status: stateChange, reason: reason || null }), tr("State changed")); setStateChange(null); }}>{tr("Apply")}</button></>}>
        <p>{tr("New state")}: <strong>{tr(stateChange.replace("_", " "))}</strong></p>
        <Field label={tr("Reason (shown to the workspace's administrators)")}><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </Modal>}
      {support && <Modal title={tr("Request support access")} onClose={() => setSupport(false)} footer={<>
        <button className="btn" onClick={() => setSupport(false)}>{tr("Cancel")}</button>
        <button className="btn primary" disabled={supportReason.trim().length < 5} onClick={() => { act(() => post(`/api/owner/workspaces/${id}/support-access`, { reason: supportReason, hours: Number(hours) }), tr("Support access granted")); setSupport(false); }}>{tr("Grant")}</button></>}>
        <p className="small muted">{tr("Read-only access as a viewer, for a bounded time, with a reason. The grant and every request made under it are audited and visible to the workspace's administrators.")}</p>
        <Field label={tr("Reason (ticket, request)")}><input className="input" value={supportReason} onChange={(e) => setSupportReason(e.target.value)} /></Field>
        <Field label={tr("Hours (1–72)")}><input className="input" type="number" min={1} max={72} value={hours} onChange={(e) => setHours(e.target.value)} /></Field>
      </Modal>}
      {removing && <Confirm text={tr("Remove this member from the workspace?")} yes={tr("Remove")} onClose={() => setRemoving(null)}
        onYes={() => act(() => del(`/api/owner/workspaces/${id}/members/${removing}`), tr("Member removed"))} />}
      {deleting && <Confirm text={tr("Delete {0} and everything in it? This cannot be undone.", { 0: w.name })} onClose={() => setDeleting(false)}
        onYes={() => void run(() => del(`/api/owner/workspaces/${id}`), tr("Workspace deleted")).then(() => goOwner("workspaces"))} />}
    </div>
  );
}
