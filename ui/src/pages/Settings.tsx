import { useEffect, useState, type ReactNode } from "react";
import { del, get, patch, post, put } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { CURRENCIES, MONTHS, fmtDateTime, fmtNumber, fmtRelative } from "../lib/format";
import { Confirm, ErrorBox, Field, Icon, LanguagePicker, Loading, Modal, PageHead, go, useAction, useLoad, useToast } from "../components/ui";
import { openExternal } from "../lib/api";
import { N, tr } from "../lib/i18n";

const TABS: [string, string, string][] = [
  ["workspace", N("Workspace"), "viewer"], ["models", N("Models"), "viewer"], ["files", N("Files and review"), "viewer"], ["members", N("Members"), "viewer"],
  ["mcp-access", N("MCP access"), "viewer"], ["audit", N("Audit log"), "admin"], ["usage", N("Usage"), "admin"], ["account", N("Account"), "viewer"],
];

export function SettingsPage({ tab = "workspace" }: { tab?: string }): ReactNode {
  const { ws } = useApp();
  return (
    <div className="page">
      <PageHead title={tr("Settings")} />
      <div className="seg" role="tablist" style={{ marginBottom: 20 }}>
        {TABS.filter(([, , role]) => can(ws, role as any)).map(([key, label]) => (
          <button key={key} role="tab" aria-pressed={tab === key} onClick={() => go(`settings/${key}`)}>{tr(label)}</button>))}
      </div>
      {tab === "files" ? <FilesSettings /> : tab === "models" ? <Models /> : tab === "members" ? <Members /> : tab === "mcp-access" ? <McpAccess />
        : tab === "audit" ? <Audit /> : tab === "usage" ? <Usage /> : tab === "account" ? <Account /> : <WorkspaceSettings />}
    </div>
  );
}

function WorkspaceSettings(): ReactNode {
  const { ws, reloadSession } = useApp();
  const [f, setF] = useState({ ...ws });
  const [confirm, setConfirm] = useState(false);
  const { busy, run } = useAction();
  const admin = can(ws, "admin");
  useEffect(() => setF({ ...ws }), [ws.id]);
  return (
    <div className="panel panel-pad stack" style={{ maxWidth: 760 }}>
      <Field label={tr("Name")}><input className="input" value={f.name} disabled={!admin} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <div className="grid2">
        <Field label={tr("Base currency")}><select className="select" value={f.base_currency} disabled={!admin} onChange={(e) => setF({ ...f, base_currency: e.target.value })}>
          {[...new Set([f.base_currency, ...CURRENCIES])].map((c) => <option key={c}>{c}</option>)}</select></Field>
        <Field label={tr("Country (ISO code)")}><input className="input" maxLength={2} value={f.country} disabled={!admin} onChange={(e) => setF({ ...f, country: e.target.value.toUpperCase() })} /></Field>
        <Field label={tr("Fiscal year starts in")}><select className="select" value={f.fiscal_year_start_month} disabled={!admin}
          onChange={(e) => setF({ ...f, fiscal_year_start_month: Number(e.target.value) })}>{MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}</select></Field>
        <Field label={tr("Accounting framework")}><select className="select" value={f.accounting_framework} disabled={!admin} onChange={(e) => setF({ ...f, accounting_framework: e.target.value })}>
          {["IFRS", "IFRS for SMEs", "US GAAP", "UK GAAP (FRS 102)", "Local GAAP", "Other"].map((x) => <option key={x}>{x}</option>)}</select></Field>
      </div>
      <Field label={tr("Time zone")}><input className="input" value={f.timezone} disabled={!admin} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
      {admin && <div className="row"><button className="btn primary" disabled={busy} onClick={() => run(() => patch(wsPath(ws, ""), {
        name: f.name, base_currency: f.base_currency, country: f.country, fiscal_year_start_month: f.fiscal_year_start_month,
        accounting_framework: f.accounting_framework, timezone: f.timezone }), tr("Saved")).then(reloadSession)}>{tr("Save")}</button></div>}
      <DataBoundary />
      {ws.role === "owner" && (
        <div style={{ borderTop: "1px solid var(--rule)", paddingTop: 16, marginTop: 16 }}>
          <h3>{tr("Delete workspace")}</h3>
          <p className="small muted">{tr("Deletes every conversation, dataset, dashboard, workflow and stored credential of this workspace.")}</p>
          <button className="btn danger" onClick={() => setConfirm(true)}>{tr("Delete")}{" "}{ws.name}</button>
        </div>
      )}
      {confirm && <Confirm text={tr("Delete {0} and everything in it? This cannot be undone.", { 0: ws.name })} onClose={() => setConfirm(false)}
        onYes={async () => { await run(() => del(wsPath(ws, ""))); await reloadSession(); go("chat"); }} />}
    </div>
  );
}

function DataBoundary(): ReactNode {
  const { ws, session, reloadSession } = useApp();
  const { busy, run } = useAction();
  const serverSealed = !!session.features.sealed;
  const sealed = serverSealed || ws.egress_policy === "sealed";
  const set = (policy: "open" | "sealed") => run(() => patch(wsPath(ws, ""), { egress_policy: policy }),
    policy === "sealed" ? tr("Data now stays inside the organization") : tr("Outside services are allowed again")).then(reloadSession);
  return (
    <div style={{ borderTop: "1px solid var(--rule)", paddingTop: 16, marginTop: 16 }} className="stack">
      <h3>{tr("Keep data inside the organization")}</h3>
      <p className="small muted">{tr("When on, nothing is sent to outside services: e-mail, Telegram, WhatsApp, Google Drive, MCP servers and outgoing webhooks are switched off, and only a model on your own network can be used. OfinTech Desk never makes payments, whether this is on or off.")}</p>
      <div className="row">
        <span className={"tag " + (sealed ? "green" : "")}>{sealed ? tr("On") : tr("Off")}</span>
        {serverSealed && <span className="small muted">{tr("Set for the whole server by its administrator.")}</span>}
        {!serverSealed && ws.role === "owner" && (sealed
          ? <button className="btn" disabled={busy} onClick={() => set("open")}>{tr("Allow outside services")}</button>
          : <button className="btn primary" disabled={busy} onClick={() => set("sealed")}>{tr("Keep data inside")}</button>)}
        {!serverSealed && ws.role !== "owner" && <span className="small muted">{tr("Only the workspace owner can change this.")}</span>}
      </div>
    </div>
  );
}

function Models(): ReactNode {
  const { ws } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, "/providers")), [ws.id]);
  const catalog = useLoad(() => get<any[]>("/api/llm/catalog"), []);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const { busy, run } = useAction();
  const toast = useToast();
  const admin = can(ws, "admin");
  if (!data.data) return <Loading />;
  const d = data.data;
  return (
    <div className="stack" style={{ maxWidth: 900 }}>
      <p className="muted">{tr("Bring your own key for any provider, or run models locally with Ollama or LM Studio. Each conversation and workflow step can use a different model; this is the default.")}</p>
      {d.providers.length > 0 && (
        <div className="panel panel-pad grid2">
          <Field label={tr("Default provider")}><select className="select" value={d.default_provider_id || ""} disabled={!admin}
            onChange={(e) => { const p = d.providers.find((x: any) => x.id === e.target.value);
              run(() => put(wsPath(ws, "/default-model"), { provider_id: e.target.value, model: p?.models[0] || "default" })).then(data.reload); }}>
            <option value="" disabled>{tr("Choose…")}</option>{d.providers.map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></Field>
          <Field label={tr("Default model")}><input className="input" list="def-models" disabled={!admin || !d.default_provider_id} defaultValue={d.default_model || ""}
            key={d.default_model} onBlur={(e) => e.target.value && e.target.value !== d.default_model &&
              run(() => put(wsPath(ws, "/default-model"), { provider_id: d.default_provider_id, model: e.target.value }), tr("Default model saved")).then(data.reload)} />
            <datalist id="def-models">{(d.providers.find((p: any) => p.id === d.default_provider_id)?.models || []).map((m: string) => <option key={m} value={m} />)}</datalist></Field>
          <Field label={tr("Model for short jobs")} hint={tr("A smaller, cheaper model for search answers. Leave it on the default model if unsure.")}>
            <select className="select" value={d.utility_provider_id || ""} disabled={!admin}
              onChange={(e) => { const p = d.providers.find((x: any) => x.id === e.target.value);
                run(() => put(wsPath(ws, "/utility-model"), { provider_id: e.target.value || null, model: p?.models[0] || null }), tr("Saved")).then(data.reload); }}>
              <option value="">{tr("Same as the default model")}</option>{d.providers.map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></Field>
          {d.utility_provider_id && <Field label={tr("Short-job model")}><input className="input" list="util-models" disabled={!admin} defaultValue={d.utility_model || ""}
            key={d.utility_model} onBlur={(e) => e.target.value && e.target.value !== d.utility_model &&
              run(() => put(wsPath(ws, "/utility-model"), { provider_id: d.utility_provider_id, model: e.target.value }), tr("Saved")).then(data.reload)} />
            <datalist id="util-models">{(d.providers.find((p: any) => p.id === d.utility_provider_id)?.models || []).map((m: string) => <option key={m} value={m} />)}</datalist></Field>}
        </div>
      )}
      <ul className="list panel">
        {d.providers.length === 0 && <li className="muted">{tr("No providers yet.")}</li>}
        {d.providers.map((p: any) => (
          <li key={p.id}>
            <div className="spread"><div><strong>{p.label}</strong> <span className="tag">{p.kind_label}</span>
              <div className="xs muted">{p.base_url}{p.has_key ? tr(", key stored") : ""}, {p.models.length}{" "}{tr("models")}</div></div>
              {admin && <div className="row">
                <button className="btn small" disabled={busy} onClick={() => run(() => post(wsPath(ws, `/providers/${p.id}/models/refresh`)), tr("Model list updated")).then(data.reload)}>{tr("Load models")}</button>
                <button className="btn small" disabled={busy} onClick={() => run(() => post(wsPath(ws, `/providers/${p.id}/test`), { model: p.models[0] || d.default_model }))
                  .then((r: any) => r && toast(tr("The model replied: {0}", { 0: r.reply })))}>{tr("Test")}</button>
                <button className="btn small" onClick={() => setEditing(p)}>{tr("Edit")}</button>
                <button className="btn small danger" onClick={() => run(() => del(wsPath(ws, `/providers/${p.id}`))).then(data.reload)}>{tr("Remove")}</button>
              </div>}
            </div>
          </li>
        ))}
      </ul>
      {admin && <div><button className="btn primary" onClick={() => setAdding(true)}><Icon name="plus" />{tr("Add provider")}</button></div>}
      {adding && <ProviderForm catalog={catalog.data || []} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); data.reload(); }} />}
      {editing && <ProviderForm catalog={catalog.data || []} existing={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); data.reload(); }} />}
    </div>
  );
}

function ProviderForm({ catalog, existing, onClose, onSaved }: { catalog: any[]; existing?: any; onClose: () => void; onSaved: () => void }): ReactNode {
  const { ws } = useApp();
  const [kind, setKind] = useState(existing?.kind || "openai");
  const k = catalog.find((c) => c.kind === kind);
  const [f, setF] = useState({ label: existing?.label || "", base_url: existing?.base_url || "", api_key: "", models: (existing?.models || []).join("\n") });
  const { busy, run } = useAction();
  const [warning, setWarning] = useState<string | null>(null);
  async function save() {
    const models = f.models.split(/[\n,]/).map((m: string) => m.trim()).filter(Boolean);
    const r: any = await run(() => existing
      ? patch(wsPath(ws, `/providers/${existing.id}`), { label: f.label || undefined, base_url: f.base_url || undefined, api_key: f.api_key || undefined, models })
      : post(wsPath(ws, "/providers"), { kind, label: f.label || undefined, base_url: f.base_url || undefined, api_key: f.api_key || undefined, models }), tr("Saved"));
    if (r?.warning) { setWarning(r.warning); return; }
    if (r) onSaved();
  }
  return (
    <Modal title={existing ? `Edit ${existing.label}` : tr("Add model provider")} onClose={onClose} footer={<>
      <button className="btn" onClick={warning ? onSaved : onClose}>{warning ? tr("Done") : tr("Cancel")}</button>
      {!warning && <button className="btn primary" disabled={busy} onClick={save}>{tr("Save")}</button>}</>}>
      <div className="stack">
        {!existing && <Field label={tr("Provider")}><select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
          {catalog.map((c) => <option key={c.kind} value={c.kind} disabled={!c.available}>{c.label}{c.available ? "" : tr(" (desktop app only)")}</option>)}</select></Field>}
        <Field label={tr("Display name")}><input className="input" placeholder={k?.label} value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></Field>
        {(k?.editable_url || existing) && <Field label={tr("Base URL")} hint={k?.kind === "openai_compatible" ? tr("The /v1 address of your server, e.g. https://llm.example.com/v1") : undefined}>
          <input className="input" placeholder={k?.base_url} value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} /></Field>}
        {(k?.needs_key || kind === "openai_compatible" || existing) && <Field label={tr("API key")} hint={<>{existing?.has_key ? tr("Leave empty to keep the stored key. ") : ""}
          {k?.docs?.startsWith("http") && <a href={k.docs} onClick={(e) => { e.preventDefault(); openExternal(k.docs); }}>{tr("Get a key")}</a>}</>}>
          <input className="input" type="password" autoComplete="off" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} /></Field>}
        <Field label={tr("Models (optional)")} hint={tr("One per line. Leave empty to load the provider's list automatically.")}>
          <textarea className="textarea code-input" value={f.models} onChange={(e) => setF({ ...f, models: e.target.value })} /></Field>
        {warning && <div className="notice warn small">{warning}</div>}
      </div>
    </Modal>
  );
}

function Members(): ReactNode {
  const { ws, session } = useApp();
  const members = useLoad(() => get<any[]>(wsPath(ws, "/members")), [ws.id]);
  const invites = useLoad(() => (can(ws, "admin") && session.features.team ? get<any[]>(wsPath(ws, "/invites")) : Promise.resolve([])), [ws.id]);
  const [inv, setInv] = useState({ email: "", role: "editor" });
  const [link, setLink] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const { busy, run } = useAction();
  const admin = can(ws, "admin");
  return (
    <div className="stack" style={{ maxWidth: 900 }}>
      <p className="muted">{tr("Viewers read; editors chat, import data, build dashboards and workflows and approve actions; admins manage connections, models and members; owners can also delete the workspace.")}</p>
      <div className="table-wrap" style={{ maxHeight: "none" }}><table className="ledger"><thead><tr><th>{tr("Name")}</th><th>{tr("E-mail")}</th><th>{tr("Chat apps")}</th><th>{tr("Role")}</th><th /></tr></thead>
        <tbody>{(members.data || []).map((m) => (
          <tr key={m.id}><td>{m.name}</td><td>{m.is_local ? tr("this computer") : m.email}</td><td>{m.channels.join(", ") || "—"}</td>
            <td>{admin && m.id !== session.user?.id ? <select className="select" value={m.role} aria-label={tr("Role of {0}", { 0: m.name })}
              onChange={(e) => run(() => patch(wsPath(ws, `/members/${m.id}`), { role: e.target.value }), tr("Role changed")).then(members.reload)}>
              {["viewer", "editor", "admin", "owner"].map((r) => <option key={r}>{r}</option>)}</select> : m.role}</td>
            <td>{admin && m.id !== session.user?.id && <button className="btn small danger" onClick={() =>
              run(() => del(wsPath(ws, `/members/${m.id}`))).then(members.reload)}>{tr("Remove")}</button>}</td></tr>))}</tbody></table></div>
      {!session.features.team ? (
        <div className="notice small">{tr("The desktop app is for one person. To work with colleagues, run the Team Server and connect this app to it (see the README).")}</div>
      ) : admin && (
        <div className="panel panel-pad stack">
          <h3>{tr("Invite someone")}</h3>
          <div className="row wrap">
            <input className="input" style={{ maxWidth: 300 }} type="email" placeholder={tr("name@company.com")} value={inv.email} onChange={(e) => setInv({ ...inv, email: e.target.value })} aria-label={tr("E-mail")} />
            <select className="select" style={{ width: 140 }} value={inv.role} onChange={(e) => setInv({ ...inv, role: e.target.value })} aria-label={tr("Role")}>
              <option>{tr("viewer")}</option><option>{tr("editor")}</option><option>{tr("admin")}</option></select>
            <button className="btn primary" disabled={busy || !inv.email} onClick={async () => {
              const r: any = await run(() => post(wsPath(ws, "/invites"), inv));
              if (r) {
                setLink(r.added ? null : r.emailed ? null : r.link);
                setSent(r.added ? `${r.email} already had an account and was added as ${r.role}${r.emailed ? "; they were told by e-mail" : ""}.`
                  : r.emailed ? `An invitation e-mail was sent to ${r.email}.` : null);
                setInv({ ...inv, email: "" }); invites.reload(); members.reload();
              }
            }}>{session.features.email ? tr("Invite") : tr("Create invitation")}</button>
          </div>
          {sent && <div className="notice small">{sent}</div>}
          {!session.features.email && <p className="xs muted">{tr("E-mail is not set up on this server (DESK_SMTP_*), so invitations are links for you to send.")}</p>}
          {link && <div className="notice small">{tr("Send this link to the person (it works once, for 7 days):")}<br /><code>{link}</code>
            <button className="btn ghost small" onClick={() => navigator.clipboard?.writeText(link)}>{tr("Copy")}</button></div>}
          {(invites.data || []).filter((i) => !i.accepted_at).length > 0 && (
            <ul className="list">{(invites.data || []).filter((i) => !i.accepted_at).map((i) => (
              <li key={i.id} className="spread small" style={{ padding: "6px 0" }}><span>{i.email}{" "}{tr("as")}{" "}{i.role}{tr(", expires")}{" "}{fmtRelative(i.expires_at)}</span>
                <button className="btn ghost small" onClick={() => run(() => del(wsPath(ws, `/invites/${i.id}`))).then(invites.reload)}>{tr("Revoke")}</button></li>))}</ul>
          )}
        </div>
      )}
    </div>
  );
}

function McpAccess(): ReactNode {
  const { ws } = useApp();
  const keys = useLoad(() => get<any[]>(wsPath(ws, "/mcp-keys")), [ws.id]);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<any>(null);
  const { busy, run } = useAction();
  return (
    <div className="stack" style={{ maxWidth: 820 }}>
      <p className="muted">{tr("Use this workspace from other MCP clients (Claude Desktop, IDEs, other agents). They get read-only finance tools: calculators, datasets and accounting reports. They can never send messages or change records.")}</p>
      <div className="row"><input className="input" style={{ maxWidth: 280 }} placeholder={tr("Key name, e.g. Claude Desktop")} value={name}
        onChange={(e) => setName(e.target.value)} aria-label={tr("Key name")} />
        <button className="btn primary" disabled={busy || !name} onClick={async () => { const r = await run(() => post(wsPath(ws, "/mcp-keys"), { name }));
          if (r) { setCreated(r); setName(""); keys.reload(); } }}>{tr("Create access key")}</button></div>
      {created && <div className="notice small">{tr("Endpoint:")}{" "}<code>{created.endpoint}</code><br />{tr("Key (shown once):")}{" "}<code>{created.key}</code><br />
        {tr("Send it as")}{" "}<code>{tr("Authorization: Bearer &lt;key&gt;")}</code>.</div>}
      <ul className="list panel">{(keys.data || []).length === 0 && <li className="muted">{tr("No keys.")}</li>}
        {(keys.data || []).map((k) => <li key={k.id} className="spread"><span>{k.name}<span className="xs muted">{" "}{tr("created")}{" "}{fmtRelative(k.created_at)}
          {k.last_used_at ? tr(", last used {0}", { 0: fmtRelative(k.last_used_at) }) : tr(", never used")}</span></span>
          <button className="btn small danger" onClick={() => run(() => del(wsPath(ws, `/mcp-keys/${k.id}`))).then(keys.reload)}>{tr("Revoke")}</button></li>)}</ul>
    </div>
  );
}

function Audit(): ReactNode {
  const { ws } = useApp();
  const rows = useLoad(() => get<any[]>(wsPath(ws, "/audit")), [ws.id]);
  if (!rows.data) return <Loading />;
  return (
    <div className="table-wrap" style={{ maxHeight: "70vh" }}><table className="ledger"><thead><tr><th>{tr("When")}</th><th>{tr("Who")}</th><th>{tr("Action")}</th><th>{tr("Target")}</th><th>{tr("Detail")}</th></tr></thead>
      <tbody>{rows.data.map((r) => <tr key={r.id}><td className="small">{fmtDateTime(r.created_at)}</td><td>{r.user_name || tr("system")}</td>
        <td><code>{r.action}</code></td><td className="small">{r.target}</td><td className="xs muted">{r.detail ? JSON.stringify(r.detail).slice(0, 160) : ""}</td></tr>)}</tbody></table></div>
  );
}

function Usage(): ReactNode {
  const { ws } = useApp();
  const u = useLoad(() => get<any>(wsPath(ws, "/usage")), [ws.id]);
  if (!u.data) return <Loading />;
  return (
    <div className="stack" style={{ maxWidth: 820 }}>
      <p className="muted">{tr("Language model calls in the last 30 days. Costs depend on each provider's prices.")}</p>
      <div className="table-wrap"><table className="ledger"><thead><tr><th>{tr("Provider")}</th><th>{tr("Model")}</th><th className="num">{tr("Calls")}</th><th className="num">{tr("Input tokens")}</th><th className="num">{tr("Output tokens")}</th><th className="num" title={tr("Input tokens served from the provider's prompt cache, billed at a fraction of the price")}>{tr("From cache")}</th></tr></thead>
        <tbody>{u.data.by_model.map((r: any, i: number) => <tr key={i}><td>{r.provider || "—"}</td><td>{r.model}</td><td className="num">{fmtNumber(r.calls, 0)}</td>
          <td className="num">{fmtNumber(r.input_tokens, 0)}</td><td className="num">{fmtNumber(r.output_tokens, 0)}</td>
          <td className="num">{r.input_tokens ? `${Math.round((100 * (r.cache_read || 0)) / r.input_tokens)}%` : "—"}</td></tr>)}</tbody></table></div>
    </div>
  );
}

function Account(): ReactNode {
  const { session } = useApp();
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("desk-theme") || "system"; } catch { return "system"; } });
  const [pw, setPw] = useState({ current: "", new: "" });
  const [error, setError] = useState<string | null>(null);
  const { busy, run } = useAction();
  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("desk-theme", theme); } catch { /* ignore */ }
  }, [theme]);
  return (
    <div className="stack" style={{ maxWidth: 560 }}>
      <div className="panel panel-pad stack">
        <h3>{tr("Appearance")}</h3>
        <div className="seg">{[["system", N("System")], ["light", N("Light")], ["dark", N("Dark")]].map(([t, label]) => <button key={t} aria-pressed={theme === t} onClick={() => setTheme(t)}>{tr(label)}</button>)}</div>
      </div>
      <div className="panel panel-pad stack">
        <h3>{tr("Language")}</h3>
        <p className="small muted" style={{ margin: 0 }}>{tr("The app uses this language, and the assistant answers in it unless you write in another one.")}</p>
        <LanguagePicker />
      </div>
      {session.mode === "server" && (
        <div className="panel panel-pad stack">
          <h3>{tr("Password")}</h3>
          <Field label={tr("Current password")}><input className="input" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
          <Field label={tr("New password")} hint={tr("At least 12 characters with upper- and lower-case letters and a digit.")}><input className="input" type="password" autoComplete="new-password" value={pw.new} onChange={(e) => setPw({ ...pw, new: e.target.value })} /></Field>
          <ErrorBox error={error} />
          <div className="row"><button className="btn primary" disabled={busy} onClick={async () => { setError(null);
            const r = await run(() => post("/api/auth/password", pw), tr("Password changed")); if (r) setPw({ current: "", new: "" }); }}>{tr("Change password")}</button>
            <button className="btn" onClick={async () => { const r = await run(() => post("/api/auth/sign-out-everywhere"), tr("Other devices signed out")); void r; }}>{tr("Sign out of other devices")}</button>
            <button className="btn" onClick={() => post("/api/auth/logout").then(() => {
              try { localStorage.setItem("desk-signed-out", `user:${Date.now()}`); } catch { /* private mode */ }  // other tabs follow
              location.reload();
            })}>{tr("Sign out")}</button></div>
        </div>
      )}
    </div>
  );
}

function FilesSettings(): ReactNode {
  const { ws, session } = useApp();
  const st = useLoad(() => get<any>(wsPath(ws, "/files-settings")), [ws.id]);
  const providers = useLoad(() => get<any>(wsPath(ws, "/providers")), [ws.id]);
  const [f, setF] = useState({ auto_review: true, provider_id: "", model: "" });
  const { busy, run } = useAction();
  const admin = can(ws, "admin");
  useEffect(() => {
    if (st.data) setF({ auto_review: st.data.auto_review, provider_id: st.data.provider_id || "", model: st.data.model || "" });
  }, [st.data]);
  if (!st.data) return <Loading />;
  const current = (providers.data?.providers || []).find((p: any) => p.id === f.provider_id);
  const storage = st.data.storage;
  return (
    <div className="stack" style={{ maxWidth: 760 }}>
      <div className="panel panel-pad stack">
        <h3>{tr("Independent review")}</h3>
        <p className="small muted" style={{ margin: 0 }}>{tr("After the assistant creates a workbook or report, a reviewer checks it like a financial controller would. The reviewer sees only the file, its sources and read-only tools, never the conversation. Using a different model from the one that drafted the file makes the check more independent.")}</p>
        <label className="check"><input type="checkbox" checked={f.auto_review} disabled={!admin}
          onChange={(e) => setF({ ...f, auto_review: e.target.checked })} /><span>{tr("Review every new file and version automatically")}</span></label>
        <div className="grid2">
          <Field label={tr("Reviewer model provider")}><select className="select" value={f.provider_id} disabled={!admin}
            onChange={(e) => setF({ ...f, provider_id: e.target.value, model: "" })}>
            <option value="">{tr("Same as the workspace default")}</option>
            {(providers.data?.providers || []).map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></Field>
          <Field label={tr("Reviewer model")}><select className="select" value={f.model} disabled={!admin || !f.provider_id}
            onChange={(e) => setF({ ...f, model: e.target.value })}>
            <option value="">{tr("Provider default")}</option>
            {(current?.models || []).map((m: string) => <option key={m} value={m}>{m}</option>)}</select></Field>
        </div>
        {admin && <div><button className="btn primary" disabled={busy} onClick={() => run(() => put(wsPath(ws, "/files-settings"),
          { auto_review: f.auto_review, provider_id: f.provider_id || null, model: f.model || null }), tr("Saved")).then(st.reload)}>{tr("Save")}</button></div>}
      </div>
      <div className="panel panel-pad stack">
        <h3>{tr("File storage")}</h3>
        <p className="small" style={{ margin: 0 }}>{storage.kind === "s3"
          ? <>{tr("S3-compatible object storage at")}{" "}<code>{storage.endpoint}</code>{tr(", bucket")}{" "}<code>{storage.bucket}</code>
            {storage.prefix ? <>{tr(", prefix")}{" "}<code>{storage.prefix}</code></> : null}.</>
          : <>{tr("This")}{" "}{session.mode === "desktop" ? tr("computer's") : tr("server's")}{" "}{tr("data folder.")}</>}
          {storage.encrypted ? tr(" Files are encrypted by OfinTech Desk before they are stored.") : ""}</p>
        <p className="xs muted" style={{ margin: 0 }}>{tr("Documents, imports and generated files are kept here. The operator sets this with the DESK_STORAGE and DESK_S3_* settings (see docs/DEPLOYMENT.md).")}</p>
      </div>
    </div>
  );
}
