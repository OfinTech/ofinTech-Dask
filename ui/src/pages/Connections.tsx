import { useState, type ReactNode } from "react";
import { del, get, openExternal, patch, post, put } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtRelative } from "../lib/format";
import { Confirm, ErrorBox, Field, Icon, Loading, Modal, PageHead, StatusTag, useAction, useLoad, useToast } from "../components/ui";
import { N, tr } from "../lib/i18n";

/** Event groups and delivery statuses named by the engine (listed so they are translated). */
export const WEBHOOK_WORDS = [N("workflow"), N("approval"), N("file"), N("forecast"), N("cost centre"), N("kpi"), N("memory"), N("dataset"),
  N("connection"), N("member"), N("document"), N("test"), N("delivered"), N("received"), N("workflow started"), N("retrying"),
  N("workflow runs started, finished or failed; workflows changed"), N("approvals decided"),
  N("files created, revised, reviewed, edited, uploaded or deleted"), N("forecasts created, submitted, approved, archived or made the plan"),
  N("cost centres and their budgets"), N("KPI targets and formulas changed"), N("memory proposed, saved, edited or deleted"),
  N("datasets imported or deleted"), N("connections and syncs"), N("members added, roles changed, removed"), N("documents added or deleted"),
  N("test events sent from the Connections page")];

const ACCOUNTING = [
  ["quickbooks", "QuickBooks Online", "Profit and loss by month, balance sheet, invoices and bills (read-only)."],
  ["zoho_books", "Zoho Books", "All Zoho data centres. Profit and loss, balance sheet, invoices and bills (read-only)."],
  ["xero", "Xero", "Profit and loss, balance sheet, invoices and bills (read-only, granular scopes)."],
] as const;

export function ConnectionsPage(): ReactNode {
  const { ws, session } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, "/connections")), [ws.id]);
  const [oauthOpen, setOauthOpen] = useState(false);
  if (data.loading && !data.data) return <div className="page"><Loading /></div>;
  const conns: any[] = data.data?.connections || [];
  const admin = can(ws, "admin");

  return (
    <div className="page">
      <PageHead title={tr("Connections")} sub={tr("Accounting systems, Google, messaging apps and MCP servers this workspace can use. Credentials are encrypted on this device or server and never shown again.")}
        actions={session.user?.instance_admin && <button className="btn" onClick={() => setOauthOpen(true)}>{tr("OAuth apps")}</button>} />
      <ErrorBox error={data.error} />
      <h2>{tr("Accounting systems")}</h2>
      <div className="grid3" style={{ marginTop: 12 }}>
        {ACCOUNTING.map(([key, label, desc]) => (
          <ProviderCard key={key} provider={key} label={label} desc={desc} conns={conns.filter((c) => c.provider === key)}
            configured={data.data?.oauth_apps?.[key]} admin={admin} onChange={data.reload} onSetup={() => setOauthOpen(true)} />
        ))}
        <OdooCard conns={conns.filter((c) => c.provider === "odoo")} admin={admin} onChange={data.reload} />
        <CustomCard conns={conns.filter((c) => c.provider === "custom")} admin={admin} onChange={data.reload} />
      </div>
      <div className="section">
        <h2>{tr("Google")}</h2>
        <div className="grid3" style={{ marginTop: 12 }}>
          <ProviderCard provider="google" label={tr("Gmail, Drive and Sheets")} desc="Search and read e-mail, draft and send replies (with
            approval), import spreadsheets from Drive and e-mail attachments." conns={conns.filter((c) => c.provider === "google")}
            configured={data.data?.oauth_apps?.google} admin={admin} onChange={data.reload} onSetup={() => setOauthOpen(true)} />
        </div>
      </div>
      <div className="section">
        <h2>{tr("Messaging")}</h2>
        <p className="muted small" style={{ maxWidth: "75ch" }}>{tr("Members ask the assistant from Telegram or WhatsApp after linking their own chat with a one-time code. Unlinked chats get no data, and approvals always happen in the app.")}</p>
        <div className="grid2" style={{ marginTop: 12 }}>
          <TelegramCard conns={conns.filter((c) => c.provider === "telegram")} admin={admin} onChange={data.reload} />
          <WhatsAppCard conns={conns.filter((c) => c.provider === "whatsapp")} admin={admin} onChange={data.reload} />
        </div>
      </div>
      <div className="section">
        <h2>{tr("Knowledge")}</h2>
        <p className="muted small" style={{ maxWidth: "75ch" }}>{tr("Documents are searched by the built-in knowledge base (see Documents). Connect PipesHub to add OCR for scanned PDFs and images, its knowledge-graph retrieval and its connectors: files added here (including chat attachments) are uploaded to this workspace's PipesHub knowledge base and searched there too.")}</p>
        <div className="grid2" style={{ marginTop: 12 }}>
          <PipesHubCard conn={conns.find((c) => c.provider === "pipeshub")} admin={admin} onChange={data.reload} />
        </div>
      </div>
      <div className="section"><Webhooks /></div>
      <div className="section"><McpServers /></div>
      {oauthOpen && <OAuthApps onClose={() => { setOauthOpen(false); data.reload(); }} />}
    </div>
  );
}

// ---- webhooks ------------------------------------------------------------------------------------
function Webhooks(): ReactNode {
  const { ws, session } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, "/webhooks")), [ws.id]);
  const [creating, setCreating] = useState<"in" | "out" | null>(null);
  const [secret, setSecret] = useState<{ hook: any; secret: string } | null>(null);
  const [log, setLog] = useState<any>(null);
  const [confirm, setConfirm] = useState<any>(null);
  const { busy, run } = useAction();
  const admin = can(ws, "admin");
  const hooks: any[] = data.data?.webhooks || [];
  const groups: Record<string, string> = data.data?.event_groups || {};
  const workflows: any[] = data.data?.workflows || [];
  const incoming = hooks.filter((h) => h.direction === "in");
  const outgoing = hooks.filter((h) => h.direction === "out");

  async function toggle(h: any) {
    if (await run(() => patch(wsPath(ws, `/webhooks/${h.id}`), { enabled: !h.enabled }))) data.reload();
  }
  async function rotate(h: any) {
    const r: any = await run(() => post(wsPath(ws, `/webhooks/${h.id}/secret`)));
    if (r) { setSecret({ hook: h, secret: r.secret }); data.reload(); }
  }
  async function sendTest(h: any) {
    if (await run(() => post(wsPath(ws, `/webhooks/${h.id}/test`)), tr("Test event queued; it is sent within a few seconds"))) data.reload();
  }

  function Hook({ h }: { h: any }): ReactNode {
    return (
      <div className="stack" style={{ borderTop: "1px solid var(--rule)", paddingTop: 10 }}>
        <div className="spread"><strong className="small">{h.name}</strong>
          <span className="row" style={{ gap: 6 }}>
            {h.last_status && <StatusTag status={h.last_status} />}
            <span className={`tag ${h.enabled ? "green" : ""}`}>{h.enabled ? tr("on") : tr("off")}</span></span></div>
        <div className="xs" style={{ overflowWrap: "anywhere" }}>
          <span className="muted">{h.direction === "in" ? tr("Endpoint") : tr("Sends to")}</span>{" "}<code>{h.direction === "in" ? h.endpoint : h.url}</code></div>
        <div className="xs muted">
          {h.direction === "in"
            ? (h.workflow_name ? tr("Starts the workflow “{0}”.", { 0: h.workflow_name }) : tr("Deliveries are logged; no workflow is linked."))
            : (h.events.includes("*") ? tr("Every event.") : h.events.map((e: string) => tr(e.replace(/_/g, " "))).join(", "))}
          {" · "}{tr("secret {0}…", { 0: h.secret_prefix })}
          {h.last_delivery_at ? ` · ${tr("last delivery {0}", { 0: fmtRelative(h.last_delivery_at) })}` : ""}
        </div>
        <div className="row wrap">
          <button className="btn small" onClick={() => setLog(h)}>{tr("Deliveries ({0})", { 0: h.deliveries })}{h.failed ? <span className="tag red" style={{ marginInlineStart: 6 }}>{h.failed}</span> : null}</button>
          {admin && h.direction === "out" && <button className="btn small" disabled={busy || !h.enabled} onClick={() => sendTest(h)}><Icon name="send" />{tr("Send a test")}</button>}
          {admin && <button className="btn small" disabled={busy} onClick={() => toggle(h)}>{h.enabled ? tr("Switch off") : tr("Switch on")}</button>}
          {admin && <button className="btn small" disabled={busy} onClick={() => rotate(h)}>{tr("New secret")}</button>}
          {admin && <button className="btn small danger" onClick={() => setConfirm(h)}>{tr("Delete")}</button>}
        </div>
      </div>
    );
  }

  return (
    <>
      <h2>{tr("Webhooks")}</h2>
      <p className="muted small" style={{ maxWidth: "75ch" }}>
        {tr("Connect this workspace to anything with HTTPS. An incoming webhook gives another system an address to call, with a secret, and can start a workflow with what it sent. An outgoing webhook posts signed JSON to your address when things happen here: approvals, forecasts, files, cost centres, workflow runs.")}
        {!session.features.whatsapp && <> {tr("Incoming webhooks are reachable on the Team Server, which has a public HTTPS address.")}</>}
      </p>
      <ErrorBox error={data.error} />
      <div className="grid2" style={{ marginTop: 12 }}>
        <div className="panel panel-pad stack">
          <h3>{tr("Incoming")}</h3>
          <p className="small muted" style={{ margin: 0 }}>{tr("Your ERP, bank portal, ticketing or CI system posts to the endpoint. The sender proves itself with the secret as a bearer token or an HMAC-SHA256 signature of the body.")}</p>
          {incoming.map((h) => <Hook key={h.id} h={h} />)}
          {!incoming.length && <p className="xs muted" style={{ margin: 0 }}>{tr("No incoming webhooks yet.")}</p>}
          {admin && <div><button className="btn primary small" onClick={() => setCreating("in")}><Icon name="plus" />{tr("Add incoming webhook")}</button></div>}
        </div>
        <div className="panel panel-pad stack">
          <h3>{tr("Outgoing")}</h3>
          <p className="small muted" style={{ margin: 0 }}>{tr("Each delivery carries the event name and an HMAC-SHA256 signature of the body (X-OfinTech-Signature-256). Failed deliveries are retried after 1, 10 and 60 minutes.")}</p>
          {outgoing.map((h) => <Hook key={h.id} h={h} />)}
          {!outgoing.length && <p className="xs muted" style={{ margin: 0 }}>{tr("No outgoing webhooks yet.")}</p>}
          {admin && <div><button className="btn primary small" onClick={() => setCreating("out")}><Icon name="plus" />{tr("Add outgoing webhook")}</button></div>}
        </div>
      </div>
      {creating && <WebhookEditor direction={creating} groups={groups} workflows={workflows} onClose={() => setCreating(null)}
        onCreated={(h) => { setCreating(null); setSecret({ hook: h, secret: h.secret }); data.reload(); }} />}
      {secret && <SecretModal hook={secret.hook} secret={secret.secret} onClose={() => setSecret(null)} />}
      {log && <DeliveriesModal hook={log} admin={admin} onClose={() => { setLog(null); data.reload(); }} />}
      {confirm && <Confirm text={tr("Delete the webhook “{0}”? Its secret stops working at once and its delivery log is removed.", { 0: confirm.name })}
        onClose={() => setConfirm(null)} onYes={() => run(() => del(wsPath(ws, `/webhooks/${confirm.id}`))).then(data.reload)} />}
    </>
  );
}

function WebhookEditor({ direction, groups, workflows, onClose, onCreated }: { direction: "in" | "out"; groups: Record<string, string>;
  workflows: any[]; onClose: () => void; onCreated: (h: any) => void }): ReactNode {
  const { ws } = useApp();
  const [f, setF] = useState({ name: "", url: "", workflow_id: "", events: [] as string[], all: false });
  const { busy, run } = useAction();
  const ok = f.name.trim() && (direction === "in" || (/^https?:\/\/\S+$/i.test(f.url.trim()) && (f.all || f.events.length > 0)));
  async function create() {
    const body = { direction, name: f.name.trim(), url: direction === "out" ? f.url.trim() : null,
      events: direction === "out" ? (f.all ? ["*"] : f.events) : [], workflow_id: direction === "in" && f.workflow_id ? f.workflow_id : null };
    const r: any = await run(() => post(wsPath(ws, "/webhooks"), body), tr("Webhook created"));
    if (r) onCreated(r);
  }
  return (
    <Modal title={direction === "in" ? tr("Add incoming webhook") : tr("Add outgoing webhook")} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>{tr("Cancel")}</button>
      <button className="btn primary" disabled={busy || !ok} onClick={create}>{tr("Create")}</button></>}>
      <div className="stack">
        <Field label={tr("Name")} hint={direction === "in" ? tr("Who will call it, e.g. Bank portal, SAP, Jira.") : tr("Where it goes, e.g. Slack relay, Data warehouse.")}>
          <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></Field>
        {direction === "in" ? (
          <>
            <Field label={tr("Workflow to start (optional)")} hint={tr("The delivery's JSON is handed to the workflow as its trigger; the workflow's own approval steps still apply.")}>
              <select className="select" value={f.workflow_id} onChange={(e) => setF({ ...f, workflow_id: e.target.value })}>
                <option value="">{tr("Only log the deliveries")}</option>
                {workflows.map((w) => <option key={w.id} value={w.id} disabled={!w.enabled}>{w.name}{w.enabled ? "" : ` (${tr("off")})`}</option>)}
              </select></Field>
            <p className="small muted">{tr("At most 256 KB and 60 deliveries a minute per webhook. The last 500 deliveries are kept.")}</p>
          </>
        ) : (
          <>
            <Field label={tr("Address")} hint={tr("HTTPS, reachable from this server. Private and local addresses are refused on the Team Server.")}>
              <input className="input" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://example.com/hooks/ofintech" inputMode="url" /></Field>
            <div className="small" style={{ fontWeight: 600 }}>{tr("Send these events")}</div>
            <label className="check small"><input type="checkbox" checked={f.all} onChange={(e) => setF({ ...f, all: e.target.checked })} /><span>{tr("Everything that is audited")}</span></label>
            {!f.all && <div className="grid2">
              {Object.entries(groups).map(([k, desc]) => (
                <label key={k} className="check small"><input type="checkbox" checked={f.events.includes(k)}
                  onChange={(e) => setF({ ...f, events: e.target.checked ? [...f.events, k] : f.events.filter((x) => x !== k) })} />
                  <span><strong>{tr(k.replace(/_/g, " "))}</strong> <span className="muted">{tr(desc)}</span></span></label>))}
            </div>}
          </>
        )}
      </div>
    </Modal>
  );
}

function SecretModal({ hook, secret, onClose }: { hook: any; secret: string; onClose: () => void }): ReactNode {
  const copy = (s: string) => navigator.clipboard?.writeText(s);
  return (
    <Modal title={tr("Webhook secret")} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>{tr("I have stored it")}</button>}>
      <div className="stack">
        <p className="small">{tr("Copy the secret now; it is not shown again. It is stored encrypted here.")}</p>
        {hook.direction === "in" && <>
          <div className="xs muted">{tr("Endpoint")}</div>
          <code className="excel" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{hook.endpoint}</code>
        </>}
        <div className="xs muted">{tr("Secret")}</div>
        <code className="excel" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{secret}</code>
        <div className="row"><button className="btn small" onClick={() => copy(secret)}>{tr("Copy secret")}</button>
          {hook.direction === "in" && <button className="btn small" onClick={() => copy(hook.endpoint)}>{tr("Copy endpoint")}</button>}</div>
        {hook.direction === "in" ? (
          <div className="small stack" style={{ gap: 4 }}>
            <div>{tr("The sender posts JSON to the endpoint with one of:")}</div>
            <code className="excel">Authorization: Bearer {"<secret>"}</code>
            <code className="excel">X-OfinTech-Signature-256: sha256={"<HMAC-SHA256 of the body with the secret, hex>"}</code>
            <div className="muted">{tr("An optional X-OfinTech-Event header names the event; otherwise the JSON's “event” or “type” field is used.")}</div>
          </div>
        ) : (
          <div className="small stack" style={{ gap: 4 }}>
            <div>{tr("Verify each delivery before trusting it:")}</div>
            <code className="excel">X-OfinTech-Signature-256: sha256={"<HMAC-SHA256 of the body with the secret, hex>"}</code>
            <div className="muted">{tr("X-OfinTech-Event carries the event name and X-OfinTech-Delivery the delivery id. Answer with any 2xx status within 15 seconds.")}</div>
          </div>
        )}
      </div>
    </Modal>
  );
}

function DeliveriesModal({ hook, admin, onClose }: { hook: any; admin: boolean; onClose: () => void }): ReactNode {
  const { ws } = useApp();
  const data = useLoad(() => get<any[]>(wsPath(ws, `/webhooks/${hook.id}/deliveries?limit=100`)), [hook.id]);
  const [open, setOpen] = useState<string | null>(null);
  const { busy, run } = useAction();
  const rows = data.data || [];
  return (
    <Modal title={tr("Deliveries: {0}", { 0: hook.name })} onClose={onClose} wide>
      <ErrorBox error={data.error} />
      <div className="row" style={{ marginBottom: 8 }}><button className="btn small" onClick={data.reload}><Icon name="refresh" />{tr("Refresh")}</button></div>
      {data.loading && !data.data ? <Loading /> : rows.length === 0 ? <p className="muted">{tr("No deliveries yet.")}</p> : (
        <div className="table-wrap" style={{ maxHeight: 520 }}>
          <table className="ledger">
            <thead><tr><th>{tr("When")}</th><th>{tr("Event")}</th><th>{tr("Status")}</th><th>{tr("HTTP")}</th><th className="num">{tr("Attempts")}</th><th>{tr("Response")}</th><th /></tr></thead>
            <tbody>{rows.map((d) => (
              <>
                <tr key={d.id} className="clickable" onClick={() => setOpen(open === d.id ? null : d.id)}>
                  <td className="small" title={d.created_at}>{fmtRelative(d.created_at)}</td>
                  <td className="small">{d.event || "—"}{d.run_id ? <div className="xs"><a href={`#/workflows?run=${d.run_id}`} onClick={(e) => e.stopPropagation()}>{tr("workflow run")}</a></div> : null}</td>
                  <td><StatusTag status={d.status} /></td>
                  <td className="small">{d.http_status ?? "—"}</td>
                  <td className="num">{d.attempts}</td>
                  <td className="small" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.response || ""}</td>
                  <td>{admin && hook.direction === "out" && d.status !== "pending" && <button className="btn small" disabled={busy}
                    onClick={(e) => { e.stopPropagation(); run(() => post(wsPath(ws, `/webhooks/${hook.id}/deliveries/${d.id}/redeliver`)), tr("Queued again")).then(data.reload); }}>{tr("Send again")}</button>}</td>
                </tr>
                {open === d.id && <tr key={`${d.id}-p`}><td colSpan={7}>
                  <pre className="code-input xs" style={{ whiteSpace: "pre-wrap", margin: 0, maxHeight: 240, overflow: "auto" }}>{d.payload ? JSON.stringify(d.payload, null, 1) : d.response || ""}</pre></td></tr>}
              </>
            ))}</tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}

function ProviderCard({ provider, label, desc, conns, configured, admin, onChange, onSetup }: { provider: string; label: string;
  desc: string; conns: any[]; configured: boolean; admin: boolean; onChange: () => void; onSetup: () => void }): ReactNode {
  const { ws, session } = useApp();
  const toast = useToast();
  const { busy, run } = useAction();
  const [confirm, setConfirm] = useState<any>(null);

  async function connect(connectionId?: string) {
    const r = await run(() => post<{ url: string }>(wsPath(ws, `/connections/${provider}/authorize`), { connection_id: connectionId }));
    if (!r) return;
    openExternal(r.url);
    toast(tr("Finish signing in in your browser. This page updates when the connection is ready."));
    let tries = 0;
    const t = setInterval(() => { onChange(); if (++tries > 40) clearInterval(t); }, 3000);
  }

  return (
    <div className="panel panel-pad stack">
      <h3>{label}</h3>
      <p className="small muted" style={{ margin: 0 }}>{desc}</p>
      {conns.map((c) => (
        <div key={c.id} className="stack" style={{ borderTop: "1px solid var(--rule)", paddingTop: 10 }}>
          <div className="spread"><strong className="small">{c.label}</strong><StatusTag status={c.status} /></div>
          <div className="xs muted">{c.last_sync_at ? `Synced ${fmtRelative(c.last_sync_at)}` : c.config?.email || tr("Not synced yet")}</div>
          {c.last_error && <div className="notice error xs">{c.last_error}</div>}
          {c.config?.organizations?.length > 1 && admin && (
            <select className="select" value={c.config.organization_id} aria-label={tr("Organization")}
              onChange={(e) => run(() => patch(wsPath(ws, `/connections/${c.id}`), { organization_id: e.target.value })).then(onChange)}>
              {c.config.organizations.map((o: any) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>
          )}
          {c.config?.tenants?.length > 1 && admin && (
            <select className="select" value={c.config.tenant_id} aria-label={tr("Organisation")}
              onChange={(e) => run(() => patch(wsPath(ws, `/connections/${c.id}`), { tenant_id: e.target.value })).then(onChange)}>
              {c.config.tenants.map((o: any) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>
          )}
          <div className="row wrap">
            {provider !== "google" && can(ws, "editor") && <button className="btn small" disabled={busy}
              onClick={() => run(() => post(wsPath(ws, `/connections/${c.id}/sync`)), tr("Sync finished")).then(onChange)}><Icon name="refresh" />{tr("Sync now")}</button>}
            {admin && <button className="btn small" onClick={() => connect(c.id)}>{tr("Reconnect")}</button>}
            {admin && <button className="btn small danger" onClick={() => setConfirm(c)}>{tr("Disconnect")}</button>}
          </div>
        </div>
      ))}
      {admin && (configured ? <div><button className="btn primary small" disabled={busy} onClick={() => connect()}>
        <Icon name="link" />{conns.length ? tr("Connect another") : tr("Connect")}</button></div>
        : <p className="small">{tr("Not set up on this installation yet.")}{" "}
          {session.user?.instance_admin ? <button className="btn small" onClick={onSetup}>{tr("Add OAuth app")}</button>
            : tr("Ask the installation administrator to add the OAuth app.")}</p>)}
      {confirm && <Confirm text={tr("Disconnect {0}? Synced datasets stay; stored tokens are deleted.", { 0: confirm.label })} yes="Disconnect"
        onClose={() => setConfirm(null)} onYes={() => run(() => del(wsPath(ws, `/connections/${confirm.id}`))).then(onChange)} />}
    </div>
  );
}

function PairButton({ conn }: { conn: any }): ReactNode {
  const { ws } = useApp();
  const [code, setCode] = useState<any>(null);
  const { run } = useAction();
  return (
    <>
      <button className="btn small" onClick={async () => setCode(await run(() => post(wsPath(ws, `/connections/${conn.id}/pair`))))}>
        {tr("Link my account")}</button>
      {code && (
        <Modal title={tr("Link your chat")} onClose={() => setCode(null)}>
          <p>{code.instructions}</p>
          <p style={{ fontSize: 28, fontWeight: 700, letterSpacing: ".12em" }}>{code.code}</p>
          <button className="btn" onClick={() => openExternal(code.link)}>{tr("Open the chat app")}</button>
          <p className="small muted" style={{ marginTop: 12 }}>{tr("The code works once and expires in 15 minutes.")}</p>
        </Modal>
      )}
    </>
  );
}

function Links({ conn, onChange }: { conn: any; onChange: () => void }): ReactNode {
  const { ws } = useApp();
  const { run } = useAction();
  if (!conn.links.length) return <p className="xs muted">{tr("No chats linked yet.")}</p>;
  return (
    <ul className="list">
      {conn.links.map((l: any) => (
        <li key={l.id} className="spread small" style={{ padding: "4px 0" }}><span>{l.user_name} ({l.display_name || tr("chat")})</span>
          <button className="btn ghost small" onClick={() => run(() => del(wsPath(ws, `/connections/${conn.id}/links/${l.id}`))).then(onChange)}>{tr("Unlink")}</button></li>
      ))}
    </ul>
  );
}

function TelegramCard({ conns, admin, onChange }: { conns: any[]; admin: boolean; onChange: () => void }): ReactNode {
  const { ws } = useApp();
  const [token, setToken] = useState("");
  const { busy, run } = useAction();
  return (
    <div className="panel panel-pad stack">
      <h3>{tr("Telegram")}</h3>
      {conns.map((c) => (
        <div key={c.id} className="stack">
          <div className="spread"><strong className="small">{c.label}</strong><StatusTag status={c.status} /></div>
          <div className="xs muted">{c.config.mode === "polling" ? tr("Receives messages while this app is running.") : tr("Webhook on the Team Server.")}</div>
          <Links conn={c} onChange={onChange} />
          <div className="row"><PairButton conn={c} />
            {admin && <button className="btn small danger" onClick={() => run(() => del(wsPath(ws, `/connections/${c.id}`))).then(onChange)}>{tr("Remove")}</button>}</div>
        </div>
      ))}
      {admin && conns.length === 0 && (
        <form className="stack" onSubmit={async (e) => { e.preventDefault(); if (await run(() => post(wsPath(ws, "/connections/telegram"), { bot_token: token }), tr("Telegram bot connected"))) { setToken(""); onChange(); } }}>
          <Field label={tr("Bot token")} hint={<>{tr("Create a bot with")}{" "}<a href="https://t.me/BotFather" onClick={(e) => { e.preventDefault(); openExternal("https://t.me/BotFather"); }}>{tr("@BotFather")}</a>{" "}{tr("and paste its token.")}</>}>
            <input className="input" type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" /></Field>
          <div><button className="btn primary small" disabled={busy || !token}>{tr("Connect bot")}</button></div>
        </form>
      )}
    </div>
  );
}

function WhatsAppCard({ conns, admin, onChange }: { conns: any[]; admin: boolean; onChange: () => void }): ReactNode {
  const { ws, session } = useApp();
  const [f, setF] = useState({ phone_number_id: "", display_number: "", access_token: "", app_secret: "", template_name: "" });
  const [created, setCreated] = useState<any>(null);
  const { busy, run } = useAction();
  return (
    <div className="panel panel-pad stack">
      <h3>{tr("WhatsApp Business")}</h3>
      {!session.features.whatsapp && <p className="small muted">{tr("WhatsApp delivers messages to a public HTTPS address, so it is connected on the Team Server.")}</p>}
      {conns.map((c) => (
        <div key={c.id} className="stack">
          <div className="spread"><strong className="small">{c.label}</strong><StatusTag status={c.status} /></div>
          <div className="xs muted">{tr("Webhook:")}{" "}{c.webhook_url}</div>
          <Links conn={c} onChange={onChange} />
          <div className="row"><PairButton conn={c} />
            {admin && <button className="btn small danger" onClick={() => run(() => del(wsPath(ws, `/connections/${c.id}`))).then(onChange)}>{tr("Remove")}</button>}</div>
        </div>
      ))}
      {created && <div className="notice small">{tr("Add this webhook in the Meta app dashboard (WhatsApp → Configuration) and subscribe to “messages”.")}<br />{tr("Callback URL:")}{" "}<strong>{created.webhook_url}</strong><br />{tr("Verify token:")}{" "}<strong>{created.verify_token}</strong>
        <br />{tr("The verify token is shown only now.")}</div>}
      {admin && session.features.whatsapp && conns.length === 0 && (
        <form className="stack" onSubmit={async (e) => {
          e.preventDefault();
          const r = await run(() => post(wsPath(ws, "/connections/whatsapp"), { ...f, template_name: f.template_name || null }), tr("WhatsApp connected"));
          if (r) { setCreated(r); onChange(); }
        }}>
          <div className="grid2">
            <Field label={tr("Phone number ID")}><input className="input" value={f.phone_number_id} onChange={(e) => setF({ ...f, phone_number_id: e.target.value })} /></Field>
            <Field label={tr("Display number")}><input className="input" placeholder="+44 7700 900123" value={f.display_number} onChange={(e) => setF({ ...f, display_number: e.target.value })} /></Field>
          </div>
          <Field label={tr("Permanent access token")} hint={tr("A system-user token from Meta Business Settings.")}><input className="input" type="password" value={f.access_token} onChange={(e) => setF({ ...f, access_token: e.target.value })} /></Field>
          <Field label={tr("App secret")} hint={tr("Used to verify that webhooks come from Meta.")}><input className="input" type="password" value={f.app_secret} onChange={(e) => setF({ ...f, app_secret: e.target.value })} /></Field>
          <Field label={tr("Notification template (optional)")} hint={tr("An approved template with one body variable, used for workflow notifications outside the 24-hour reply window.")}>
            <input className="input" value={f.template_name} onChange={(e) => setF({ ...f, template_name: e.target.value })} /></Field>
          <div><button className="btn primary small" disabled={busy}>{tr("Connect WhatsApp")}</button></div>
        </form>
      )}
    </div>
  );
}

function McpServers(): ReactNode {
  const { ws, session } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, "/mcp-servers")), [ws.id]);
  const [adding, setAdding] = useState(false);
  const { busy, run } = useAction();
  const admin = can(ws, "admin");
  return (
    <>
      <div className="spread"><h2>{tr("MCP servers")}</h2>
        {admin && <button className="btn small" onClick={() => setAdding(true)}><Icon name="plus" />{tr("Add MCP server")}</button>}</div>
      <p className="muted small" style={{ maxWidth: "75ch" }}>{tr("Give the assistant tools from any Model Context Protocol server. Each tool asks for approval unless you allow it to run on its own; only tools the server marks read-only can run unattended.")}</p>
      {(data.data?.servers || []).map((s: any) => (
        <div key={s.id} className="panel panel-pad stack" style={{ marginTop: 12 }}>
          <div className="spread">
            <div><strong>{s.name}</strong> <span className="tag">{s.transport === "stdio" ? tr("local command") : tr("remote")}</span>
              <div className="xs muted">{s.transport === "stdio" ? s.command_preview : s.url}</div></div>
            {admin && <div className="row">
              <label className="check small"><input type="checkbox" checked={s.enabled} onChange={(e) =>
                run(() => patch(wsPath(ws, `/mcp-servers/${s.id}`), { enabled: e.target.checked })).then(data.reload)} />{tr("On")}</label>
              <button className="btn small" disabled={busy} onClick={() => run(() => post(wsPath(ws, `/mcp-servers/${s.id}/refresh`)), tr("Tools refreshed")).then(data.reload)}><Icon name="refresh" />{tr("Refresh")}</button>
              <button className="btn small danger" onClick={() => run(() => del(wsPath(ws, `/mcp-servers/${s.id}`))).then(data.reload)}>{tr("Remove")}</button>
            </div>}
          </div>
          {s.last_error && <div className="notice error small">{s.last_error}</div>}
          {s.tools.length > 0 && (
            <div className="table-wrap"><table className="ledger"><thead><tr><th>{tr("Tool")}</th><th>{tr("Description")}</th><th>{tr("When the assistant calls it")}</th></tr></thead>
              <tbody>{s.tools.map((t: any) => {
                const policy = s.tool_policy[t.name] || s.default_policy;
                return (
                  <tr key={t.name}><td><strong>{t.name}</strong>{t.read_only && <span className="tag green" style={{ marginInlineStart: 6 }}>{tr("read-only")}</span>}</td>
                    <td className="small">{t.description}</td>
                    <td><select className="select" value={policy} disabled={!admin} aria-label={tr("Policy for {0}", { 0: t.name })} onChange={(e) =>
                      run(() => patch(wsPath(ws, `/mcp-servers/${s.id}`), { tool_policy: { ...s.tool_policy, [t.name]: e.target.value } })).then(data.reload)}>
                      <option value="ask">{tr("Ask for approval")}</option>
                      <option value="auto" disabled={!t.read_only}>{tr("Run without asking")}</option>
                      <option value="off">{tr("Don't use")}</option></select></td></tr>
                );
              })}</tbody></table></div>
          )}
        </div>
      ))}
      {adding && <AddMcp stdio={session.features.stdio_mcp} onClose={() => setAdding(false)} onAdded={() => { setAdding(false); data.reload(); }} />}
    </>
  );
}

function AddMcp({ stdio, onClose, onAdded }: { stdio: boolean; onClose: () => void; onAdded: () => void }): ReactNode {
  const { ws } = useApp();
  const [f, setF] = useState({ name: "", transport: "http", url: "", command: "", args: "", headers: "", env: "" });
  const { busy, run } = useAction();
  const parseKV = (s: string) => Object.fromEntries(s.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const i = l.indexOf(l.includes(":") && f.transport === "http" ? ":" : "=");
    return [l.slice(0, i).trim(), l.slice(i + 1).trim()];
  }));
  async function add() {
    const body = { name: f.name, transport: f.transport, url: f.url || null, command: f.command || null,
      args: f.args.split("\n").map((x) => x.trim()).filter(Boolean), headers: f.transport === "http" ? parseKV(f.headers) : {},
      env: f.transport === "stdio" ? parseKV(f.env) : {} };
    const r = await run(() => post(wsPath(ws, "/mcp-servers"), body), tr("MCP server added"));
    if (r) onAdded();
  }
  return (
    <Modal title={tr("Add MCP server")} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>{tr("Cancel")}</button><button className="btn primary" disabled={busy || !f.name} onClick={add}>{tr("Add server")}</button></>}>
      <div className="stack">
        <div className="grid2">
          <Field label={tr("Name")}><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder={tr("e.g. Banking")} /></Field>
          <Field label={tr("Type")}>
            <select className="select" value={f.transport} onChange={(e) => setF({ ...f, transport: e.target.value })}>
              <option value="http">{tr("Remote (Streamable HTTP)")}</option>
              {stdio && <option value="stdio">{tr("Local command (this computer)")}</option>}
            </select>
          </Field>
        </div>
        {f.transport === "http" ? (
          <>
            <Field label={tr("Server URL")}><input className="input" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder={tr("https://mcp.example.com/mcp")} /></Field>
            <Field label={tr("Headers (optional)")} hint={tr("One per line, e.g. Authorization: Bearer abc. Stored encrypted.")}>
              <textarea className="textarea code-input" value={f.headers} onChange={(e) => setF({ ...f, headers: e.target.value })} /></Field>
          </>
        ) : (
          <>
            <div className="notice warn small">{tr("A local server runs on this computer with your permissions. Add only servers you trust.")}</div>
            <Field label={tr("Command")} hint={tr("The program only, e.g. npx, uvx or a full path.")}><input className="input code-input" value={f.command} onChange={(e) => setF({ ...f, command: e.target.value })} /></Field>
            <Field label={tr("Arguments")} hint={tr("One per line.")}><textarea className="textarea code-input" value={f.args} onChange={(e) => setF({ ...f, args: e.target.value })} /></Field>
            <Field label={tr("Environment variables (optional)")} hint={tr("One per line, e.g. API_KEY=abc. Stored encrypted.")}>
              <textarea className="textarea code-input" value={f.env} onChange={(e) => setF({ ...f, env: e.target.value })} /></Field>
          </>
        )}
      </div>
    </Modal>
  );
}

function OAuthApps({ onClose }: { onClose: () => void }): ReactNode {
  const apps = useLoad(() => get<any[]>("/api/oauth-apps"), []);
  const [edit, setEdit] = useState<Record<string, any>>({});
  const { busy, run } = useAction();
  return (
    <Modal wide title={tr("OAuth apps")} onClose={onClose}>
      <p className="small muted">{tr("Each accounting system and Google needs an OAuth app registered by whoever runs this installation. Register the redirect URI shown below in the provider's developer portal, then paste the client ID and secret.")}</p>
      {(apps.data || []).map((a) => {
        const e = edit[a.provider] || { client_id: a.client_id || "", client_secret: "", environment: a.options?.environment || "production" };
        return (
          <div key={a.provider} className="panel panel-pad stack" style={{ marginTop: 12 }}>
            <div className="spread"><strong>{a.label}</strong>{a.configured ? <span className="tag green">{tr("set up")}{a.source === "environment" ? tr(" (environment)") : ""}</span> : <span className="tag">{tr("not set up")}</span>}</div>
            <div className="small">{tr("Redirect URI:")}{" "}<code>{a.redirect_uri}</code>{" "}
              <a href={a.developer_portal} onClick={(ev) => { ev.preventDefault(); openExternal(a.developer_portal); }}>{tr("Developer portal")}</a></div>
            <div className="xs muted">{tr("Scopes:")}{" "}{a.scopes.join(" ")}</div>
            {a.source !== "environment" && (
              <div className="grid3">
                <Field label={tr("Client ID")}><input className="input" value={e.client_id} onChange={(x) => setEdit({ ...edit, [a.provider]: { ...e, client_id: x.target.value } })} /></Field>
                <Field label={tr("Client secret")} hint={a.configured ? tr("Leave empty to keep the current secret.") : undefined}>
                  <input className="input" type="password" value={e.client_secret} onChange={(x) => setEdit({ ...edit, [a.provider]: { ...e, client_secret: x.target.value } })} /></Field>
                {a.provider === "quickbooks" ? (
                  <Field label={tr("Environment")}><select className="select" value={e.environment} onChange={(x) => setEdit({ ...edit, [a.provider]: { ...e, environment: x.target.value } })}>
                    <option value="production">{tr("Production")}</option><option value="sandbox">{tr("Sandbox")}</option></select></Field>
                ) : <span />}
              </div>
            )}
            {a.source !== "environment" && <div><button className="btn small primary" disabled={busy || !e.client_id} onClick={() =>
              run(() => put(`/api/oauth-apps/${a.provider}`, { client_id: e.client_id, client_secret: e.client_secret || null,
                environment: a.provider === "quickbooks" ? e.environment : null }), tr("Saved")).then(apps.reload)}>{tr("Save")}</button></div>}
          </div>
        );
      })}
    </Modal>
  );
}

function PipesHubCard({ conn, admin, onChange }: { conn: any; admin: boolean; onChange: () => void }): ReactNode {
  const { ws } = useApp();
  const toast = useToast();
  const [f, setF] = useState({ base_url: conn?.config?.base_url || "", token: "", kb_id: conn?.config?.kb_id || "",
    send: conn?.config?.send || "all", include_other_sources: !!conn?.config?.include_other_sources });
  const { busy, run } = useAction();
  return (
    <div className="panel panel-pad stack">
      <div className="spread"><h3>{tr("PipesHub")}</h3>{conn && <StatusTag status={conn.status} />}</div>
      {conn?.config?.kb_name && <p className="xs muted" style={{ margin: 0 }}>{tr("Knowledge base:")}{" "}{conn.config.kb_name}</p>}
      {conn?.last_error && <div className="notice error xs">{conn.last_error}</div>}
      {admin ? (
        <form className="stack" onSubmit={async (e) => {
          e.preventDefault();
          const r: any = await run(() => post(wsPath(ws, "/connections/pipeshub"), { ...f, token: f.token || null, kb_id: f.kb_id || null }));
          if (r) {
            toast(`Connected to “${r.kb_name}”. ${r.documents_queued} documents are being sent.`);
            setF({ ...f, token: "", kb_id: r.kb_id });
            onChange();
          }
        }}>
          <Field label={tr("PipesHub address")} hint={tr("e.g. https://pipeshub.example.com")}><input className="input" value={f.base_url}
            onChange={(e) => setF({ ...f, base_url: e.target.value })} /></Field>
          <Field label={conn ? tr("Personal access token (leave empty to keep)") : tr("Personal access token")}
            hint={tr("In PipesHub: Workspace settings → Developer settings → Personal access tokens. Scopes: kb:read, kb:write, kb:upload, kb:delete, semantic:write.")}>
            <input className="input" type="password" autoComplete="off" placeholder={tr("phpat_…")} value={f.token}
              onChange={(e) => setF({ ...f, token: e.target.value })} /></Field>
          <Field label={tr("Knowledge base id (optional)")} hint={tr("Empty: a knowledge base is created for this workspace.")}>
            <input className="input" value={f.kb_id} onChange={(e) => setF({ ...f, kb_id: e.target.value })} /></Field>
          <div className="seg" role="radiogroup" aria-label={tr("Which documents to send")}>
            <button type="button" aria-pressed={f.send === "all"} onClick={() => setF({ ...f, send: "all" })}>{tr("Send every document")}</button>
            <button type="button" aria-pressed={f.send === "collections"} onClick={() => setF({ ...f, send: "collections" })}>{tr("Only chosen collections")}</button>
          </div>
          <label className="check small"><input type="checkbox" checked={f.include_other_sources}
            onChange={(e) => setF({ ...f, include_other_sources: e.target.checked })} />
            <span>{tr("Also search everything else the token's user can read in PipesHub (SharePoint, Confluence, Drive…). Everyone in this workspace will be able to query it.")}</span></label>
          <div><button className="btn primary small" disabled={busy || !f.base_url || (!conn && !f.token)}>{conn ? tr("Update") : tr("Connect PipesHub")}</button>
            {conn && <button type="button" className="btn small danger" style={{ marginInlineStart: 8 }}
              onClick={() => run(() => del(wsPath(ws, `/connections/${conn.id}`))).then(onChange)}>{tr("Disconnect")}</button>}</div>
        </form>
      ) : <p className="small muted">{conn ? conn.label : tr("Not connected.")}</p>}
    </div>
  );
}

function KeyConnection({ c, admin, onChange, children }: { c: any; admin: boolean; onChange: () => void; children?: ReactNode }): ReactNode {
  const { ws } = useApp();
  const { busy, run } = useAction();
  const [confirm, setConfirm] = useState(false);
  const push = c.provider === "custom" && c.config?.mode !== "pull";
  return (
    <div className="stack" style={{ borderTop: "1px solid var(--rule)", paddingTop: 10 }}>
      <div className="spread"><strong className="small">{c.label}</strong><StatusTag status={c.status} /></div>
      <div className="xs muted">{c.last_sync_at ? `${push ? "Data received" : "Synced"} ${fmtRelative(c.last_sync_at)}`
        : push ? tr("Waiting for data from your system") : tr("Not synced yet")}
        {c.config?.api ? ` · ${c.config.api === "json2" ? "JSON-2 API" : "JSON-RPC"}` : ""}</div>
      {c.last_error && <div className="notice error xs">{c.last_error}</div>}
      {children}
      <div className="row wrap">
        {!push && can(ws, "editor") && <button className="btn small" disabled={busy}
          onClick={() => run(() => post(wsPath(ws, `/connections/${c.id}/sync`)), tr("Sync finished")).then(onChange)}><Icon name="refresh" />{tr("Sync now")}</button>}
        {admin && <button className="btn small danger" onClick={() => setConfirm(true)}>{tr("Disconnect")}</button>}
      </div>
      {confirm && <Confirm text={tr("Disconnect {0}? Synced datasets stay; stored keys are deleted.", { 0: c.label })} yes="Disconnect"
        onClose={() => setConfirm(false)} onYes={() => run(() => del(wsPath(ws, `/connections/${c.id}`))).then(onChange)} />}
    </div>
  );
}

function OdooCard({ conns, admin, onChange }: { conns: any[]; admin: boolean; onChange: () => void }): ReactNode {
  const { ws } = useApp();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ base_url: "", database: "", username: "", api_key: "", company_id: "" });
  const { busy, run } = useAction();
  return (
    <div className="panel panel-pad stack">
      <h3>{tr("Odoo")}</h3>
      <p className="small muted" style={{ margin: 0 }}>{tr("Odoo 17 or later, Community or Enterprise, Odoo Online and odoo.sh. Profit and loss, balance sheet, invoices and bills from posted entries (read-only), with an API key.")}</p>
      {conns.map((c) => <KeyConnection key={c.id} c={c} admin={admin} onChange={onChange} />)}
      {admin && <div><button className="btn primary small" onClick={() => setOpen(true)}><Icon name="link" />
        {conns.length ? tr("Connect another") : tr("Connect")}</button></div>}
      {open && (
        <Modal title={tr("Connect Odoo")} onClose={() => setOpen(false)} footer={<>
          <button className="btn" onClick={() => setOpen(false)}>{tr("Cancel")}</button>
          <button className="btn primary" disabled={busy || !f.base_url || !f.api_key} onClick={async () => {
            const r = await run(() => post(wsPath(ws, "/connections/odoo"), { ...f, database: f.database || null,
              username: f.username || null, company_id: f.company_id ? Number(f.company_id) : null }), tr("Connected; the first sync is running"));
            if (r) { setOpen(false); onChange(); }
          }}>{tr("Connect")}</button></>}>
          <div className="stack">
            <Field label={tr("Odoo address")} hint={tr("e.g. https://mycompany.odoo.com")}><input className="input" value={f.base_url}
              onChange={(e) => setF({ ...f, base_url: e.target.value })} /></Field>
            <Field label={tr("API key")} hint={tr("In Odoo: your avatar → My Preferences → Account Security → New API Key. Use a user with read access to Accounting.")}><input className="input" type="password" autoComplete="off" value={f.api_key}
              onChange={(e) => setF({ ...f, api_key: e.target.value })} /></Field>
            <div className="grid2">
              <Field label={tr("Database (optional)")} hint={tr("Only if the server hosts several databases")}><input className="input"
                value={f.database} onChange={(e) => setF({ ...f, database: e.target.value })} /></Field>
              <Field label={tr("Company id (optional)")} hint={tr("For multi-company databases")}><input className="input" inputMode="numeric"
                value={f.company_id} onChange={(e) => setF({ ...f, company_id: e.target.value.replace(/\D/g, "") })} /></Field>
            </div>
            <Field label={tr("Login (Odoo 17 and 18 only)")} hint={tr("Odoo 19 and later use the API key alone (JSON-2 API)")}>
              <input className="input" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
          </div>
        </Modal>
      )}
    </div>
  );
}

function CustomCard({ conns, admin, onChange }: { conns: any[]; admin: boolean; onChange: () => void }): ReactNode {
  const { ws } = useApp();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ label: "", mode: "pull", base_url: "", api_key: "" });
  const [key, setKey] = useState<string | null>(null);
  const { busy, run } = useAction();
  return (
    <div className="panel panel-pad stack">
      <h3>{tr("Other accounting system")}</h3>
      <p className="small muted" style={{ margin: 0 }}>{tr("Any ledger or ERP through the Open Accounting Connector: OfinTech Desk reads your adapter (pull) or your system sends JSON (push). See docs/ACCOUNTING_API.md and")}{" "}
        <a href="/api/connector-spec/openapi.json" target="_blank" rel="noreferrer">{tr("the OpenAPI file")}</a>.</p>
      {conns.map((c) => (
        <KeyConnection key={c.id} c={c} admin={admin} onChange={onChange}>
          {c.config?.mode !== "pull" && <div className="xs">
            <div className="muted">{tr("Send to")}</div><code className="excel">{c.ingest_url}</code>
            <div className="muted" style={{ marginTop: 4 }}>{tr("Key")}{" "}{c.ingest_key_prefix ? `${c.ingest_key_prefix}…` : tr("not issued")}
              {admin && <> · <button className="linklike xs" onClick={async () => {
                const r: any = await run(() => post(wsPath(ws, `/connections/${c.id}/ingest-key`)));
                if (r) setKey(r.ingest_key);
              }}>{tr("Rotate key")}</button></>}</div></div>}
        </KeyConnection>
      ))}
      {admin && <div><button className="btn primary small" onClick={() => setOpen(true)}><Icon name="plus" />{tr("Add system")}</button></div>}
      {open && (
        <Modal title={tr("Link another accounting system")} onClose={() => setOpen(false)} footer={<>
          <button className="btn" onClick={() => setOpen(false)}>{tr("Cancel")}</button>
          <button className="btn primary" disabled={busy || !f.label || (f.mode === "pull" && (!f.base_url || !f.api_key))} onClick={async () => {
            const r: any = await run(() => post(wsPath(ws, "/connections/custom"), f));
            if (r) { setOpen(false); onChange(); if (r.ingest_key) setKey(r.ingest_key); }
          }}>{f.mode === "pull" ? tr("Connect") : tr("Create ingest key")}</button></>}>
          <div className="stack">
            <Field label={tr("Name of the system")}><input className="input" placeholder={tr("e.g. Tally, Sage 300, Our ERP")} value={f.label}
              onChange={(e) => setF({ ...f, label: e.target.value })} /></Field>
            <div className="seg" role="radiogroup" aria-label={tr("Mode")}>
              <button aria-pressed={f.mode === "pull"} onClick={() => setF({ ...f, mode: "pull" })}>{tr("Pull from an adapter")}</button>
              <button aria-pressed={f.mode === "push"} onClick={() => setF({ ...f, mode: "push" })}>{tr("Push from my system")}</button>
            </div>
            {f.mode === "pull" ? <>
              <Field label={tr("Adapter address")} hint={tr("HTTPS address of your adapter (the five /v1 endpoints)")}><input className="input"
                value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} /></Field>
              <Field label={tr("Adapter key")} hint={tr("Sent as Authorization: Bearer <key>")}><input className="input" type="password"
                autoComplete="off" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} /></Field>
            </> : <p className="small muted">{tr("You get an ingest key once. Your system then posts profit and loss, balance sheet, invoices and bills as JSON to this server. Push needs the Team Server, which is reachable from your network.")}</p>}
          </div>
        </Modal>
      )}
      {key && (
        <Modal title={tr("Ingest key")} onClose={() => setKey(null)} footer={<button className="btn primary" onClick={() => setKey(null)}>{tr("I have stored it")}</button>}>
          <div className="stack">
            <p className="small">{tr("Copy this key now; it is not shown again. Send it as")}{" "}<code>{tr("Authorization: Bearer &lt;key&gt;")}</code>.</p>
            <code className="excel" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{key}</code>
            <button className="btn small" onClick={() => navigator.clipboard?.writeText(key)}>{tr("Copy")}</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
