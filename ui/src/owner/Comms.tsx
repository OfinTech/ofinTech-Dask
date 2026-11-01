import { useState, type ReactNode } from "react";
import { del, get, post, put } from "../lib/api";
import { useApp } from "../lib/app";
import { ErrorBox, Field, Loading, Modal, PageHead, useAction, useLoad } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { tr } from "../lib/i18n";
import { Records, Section, SeriesChart, Tag, Toggle, When, toneFor } from "./shared";

const EMPTY_ANN = { title: "", body: "", category: "general", channels: ["in_app"] as string[], targetKind: "all", plan_codes: [] as string[], new_users_days: 7, scheduled_at: "", send_now: true };

export function NotificationsPage(): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data, error, reload } = useLoad(() => get("/api/owner/announcements"), []);
  const { data: alerts, reload: reloadAlerts } = useLoad(() => get("/api/owner/alerts"), []);
  const [edit, setEdit] = useState<any | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [history, setHistory] = useState<any | null>(null);
  const { data: deliveries } = useLoad(() => (history ? get(`/api/owner/announcements/${history.id}/deliveries`) : Promise.resolve(null)), [history?.id]);
  const [rules, setRules] = useState<any | null>(null);
  const [hook, setHook] = useState<{ url?: string; secret?: string }>({});
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const manage = perms.has("notifications.manage");
  const target = (e: any) => e.targetKind === "all" ? { all: true } : e.targetKind === "plans" ? { plan_codes: e.plan_codes } : { new_users_days: Number(e.new_users_days) };
  const body = (e: any) => ({ title: e.title, body: e.body, category: e.category, channels: e.channels, target: target(e),
    scheduled_at: e.scheduled_at ? Date.parse(e.scheduled_at) / 1000 : null, send_now: e.send_now && !e.scheduled_at });
  const r = rules || alerts?.rules || {};
  return (
    <div className="page">
      <PageHead title={tr("Notifications")} sub={tr("Announcements to people (in-app, e-mail, optional webhook) and the alerts the owner receives. People choose per category what they receive; security and billing notices always reach them in-app.")}
        actions={manage ? <button className="btn primary" onClick={() => setEdit({ ...EMPTY_ANN })}>{tr("New announcement")}</button> : undefined} />
      <Section title={tr("Announcements")}>
        <Records rows={data.items} cols={[{ key: "title", label: tr("Title") }, { key: "category", label: tr("Category") },
          { key: "channels", label: tr("Channels"), render: (a: any) => a.channels.join(", ") },
          { key: "target", label: tr("Audience"), render: (a: any) => a.target.all ? tr("everyone") : a.target.plan_codes ? tr("plans: {0}", { 0: a.target.plan_codes.join(", ") }) : a.target.new_users_days ? tr("new users ({0} days)", { 0: a.target.new_users_days }) : tr("selected") },
          { key: "status", label: tr("Status"), render: (a: any) => <Tag text={a.status} tone={toneFor(a.status)} /> },
          { key: "delivered", label: tr("Delivered"), num: true },
          { key: "when", label: tr("When"), render: (a: any) => a.sent_at ? fmtDateTime(a.sent_at) : a.scheduled_at ? `${tr("scheduled")} ${fmtDateTime(a.scheduled_at)}` : <When ts={a.created_at} /> },
          { key: "x", label: "", render: (a: any) => <div className="row">
            <button className="btn ghost small" onClick={() => setHistory(a)}>{tr("History")}</button>
            {manage && a.status !== "canceled" && <button className="btn ghost small" disabled={busy} onClick={() => void run(() => post(`/api/owner/announcements/${a.id}/send`), tr("Sent")).then(reload)}>{a.status === "sent" ? tr("Send to new recipients") : tr("Send now")}</button>}
            {manage && ["draft", "scheduled"].includes(a.status) && <button className="btn ghost small" disabled={busy} onClick={() => void run(() => post(`/api/owner/announcements/${a.id}/cancel`), tr("Cancelled")).then(reload)}>{tr("Cancel")}</button>}
          </div> }]} empty={tr("No announcements yet.")} />
      </Section>
      <Section title={tr("Owner alerts")} actions={manage ? <button className="btn small" disabled={busy || (!rules && !hook.url && !hook.secret)} onClick={() => void run(() => put("/api/owner/alerts", { rules, webhook_url: hook.url, webhook_secret: hook.secret || undefined }), tr("Alert rules saved")).then(() => { setRules(null); setHook({}); reloadAlerts(); })}>{tr("Save")}</button> : null}>
        {!alerts ? <Loading /> : <>
          <Records rows={Object.entries(r).map(([k, v]: any) => ({ id: k, key: k, ...v }))} cols={[{ key: "key", label: tr("Event"), render: (x: any) => tr(x.key.replace(/_/g, " ")) },
            { key: "in_app", label: tr("In-app"), render: (x: any) => <input type="checkbox" disabled={!manage} checked={Boolean(x.in_app)} onChange={(e) => setRules({ ...r, [x.key]: { ...r[x.key], in_app: e.target.checked } })} /> },
            { key: "email", label: tr("E-mail"), render: (x: any) => <input type="checkbox" disabled={!manage} checked={Boolean(x.email)} onChange={(e) => setRules({ ...r, [x.key]: { ...r[x.key], email: e.target.checked } })} /> },
            { key: "threshold", label: tr("Threshold"), render: (x: any) => x.threshold !== undefined ? <input className="input" style={{ width: 90 }} type="number" min={1} disabled={!manage} value={x.threshold} onChange={(e) => setRules({ ...r, [x.key]: { ...r[x.key], threshold: Number(e.target.value) } })} /> : "—" }]} />
          <div className="grid2">
            <Field label={tr("Webhook URL for announcements (optional, https)")}><input className="input" disabled={!manage} value={hook.url ?? alerts.webhook_url ?? ""} onChange={(e) => setHook({ ...hook, url: e.target.value })} /></Field>
            <Field label={tr("Webhook signing secret (write-only)")}><input className="input" type="password" disabled={!manage} value={hook.secret ?? ""} onChange={(e) => setHook({ ...hook, secret: e.target.value })} placeholder={alerts.webhook_secret?.configured ? tr("saved — enter to replace") : ""} /></Field>
          </div>
        </>}
      </Section>
      {edit && <Modal wide title={tr("New announcement")} onClose={() => setEdit(null)} footer={<>
        <button className="btn" onClick={() => setEdit(null)}>{tr("Cancel")}</button>
        <button className="btn" disabled={busy} onClick={() => void run(() => post("/api/owner/announcements/preview", body(edit))).then(setPreview)}>{tr("Preview audience")}</button>
        <button className="btn primary" disabled={busy || !edit.title || !edit.body} onClick={() => void run(() => post("/api/owner/announcements", body(edit)), edit.scheduled_at ? tr("Scheduled") : edit.send_now ? tr("Sent") : tr("Saved as draft")).then(() => { setEdit(null); setPreview(null); reload(); })}>
          {edit.scheduled_at ? tr("Schedule") : edit.send_now ? tr("Send now") : tr("Save draft")}</button></>}>
        <Field label={tr("Title")}><input className="input" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></Field>
        <Field label={tr("Message")}><textarea className="textarea" rows={5} value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} /></Field>
        <div className="grid3">
          <Field label={tr("Category")}><select className="select" value={edit.category} onChange={(e) => setEdit({ ...edit, category: e.target.value })}>{data.categories.map((c: string) => <option key={c} value={c}>{tr(c)}</option>)}</select></Field>
          <Field label={tr("Audience")}><select className="select" value={edit.targetKind} onChange={(e) => setEdit({ ...edit, targetKind: e.target.value })}>
            <option value="all">{tr("All users")}</option><option value="plans">{tr("By plan")}</option><option value="new">{tr("New users")}</option></select></Field>
          <Field label={tr("Schedule (blank = now)")}><input className="input" type="datetime-local" value={edit.scheduled_at} onChange={(e) => setEdit({ ...edit, scheduled_at: e.target.value })} /></Field>
        </div>
        {edit.targetKind === "plans" && <div className="row wrap">{data.plans.map((p: string) => <Toggle key={p} label={p} checked={edit.plan_codes.includes(p)} onChange={(v) => setEdit({ ...edit, plan_codes: v ? [...edit.plan_codes, p] : edit.plan_codes.filter((x: string) => x !== p) })} />)}</div>}
        {edit.targetKind === "new" && <Field label={tr("Registered within the last … days")}><input className="input" type="number" min={1} max={365} value={edit.new_users_days} onChange={(e) => setEdit({ ...edit, new_users_days: e.target.value })} /></Field>}
        <div className="row wrap">{data.channels.map((c: string) => <Toggle key={c} label={tr(c.replace("_", "-"))} checked={edit.channels.includes(c)} onChange={(v) => setEdit({ ...edit, channels: v ? [...edit.channels, c] : edit.channels.filter((x: string) => x !== c) })} />)}
          {!edit.scheduled_at && <Toggle label={tr("Send immediately")} checked={edit.send_now} onChange={(v) => setEdit({ ...edit, send_now: v })} />}</div>
        {preview && <p className="notice small">{tr("{0} recipient(s)", { 0: preview.recipients })}{preview.sample.length ? `: ${preview.sample.join(", ")}${preview.recipients > preview.sample.length ? "…" : ""}` : ""}</p>}
      </Modal>}
      {history && <Modal title={history.title} onClose={() => setHistory(null)}>
        {!deliveries ? <Loading /> : <Records rows={deliveries.items.map((d: any, i: number) => ({ id: i, ...d }))} cols={[{ key: "email", label: tr("Recipient"), render: (d: any) => d.email || d.user_id }, { key: "channel", label: tr("Channel") },
          { key: "created_at", label: tr("Delivered"), render: (d: any) => fmtDateTime(d.created_at) }]} empty={tr("Not delivered yet.")} />}
      </Modal>}
    </div>
  );
}

export function EmailPage(): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data, error, reload } = useLoad(() => get("/api/owner/email"), []);
  const [f, setF] = useState<any>({});
  const [testTo, setTestTo] = useState("");
  const [tpl, setTpl] = useState<any | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [suppress, setSuppress] = useState("");
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const configure = perms.has("email.configure");
  const templates = perms.has("email.templates");
  return (
    <div className="page">
      <PageHead title={tr("Email Settings")} sub={tr("SMTP delivery for the platform's messages, editable templates in English and Arabic, and the delivery queue. The password is stored encrypted and never shown.")} />
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <div className={`kpi-card ${data.configured ? "good" : "watch"}`}><span className="kpi-name">{tr("Provider")}</span><span className="kpi-value">{data.configured ? tr("Configured") : tr("Not configured")}</span><span className="small muted">{data.source === "dashboard" ? tr("dashboard settings") : data.source === "environment" ? tr("environment variables") : "—"}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Sent (24 h)")}</span><span className="kpi-value">{data.last_24h.sent}</span></div>
        <div className={`kpi-card ${data.last_24h.failed ? "act" : ""}`}><span className="kpi-name">{tr("Failed (24 h)")}</span><span className="kpi-value">{data.last_24h.failed}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Queued")}</span><span className="kpi-value">{data.stats.queued}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Suppressed addresses")}</span><span className="kpi-value">{data.suppressed}</span></div>
      </div>
      <Section title={tr("SMTP")} actions={configure ? <button className="btn primary" disabled={busy || !Object.keys(f).length} onClick={() => void run(() => put("/api/owner/email", f), tr("E-mail settings saved")).then(() => { setF({}); reload(); })}>{tr("Save")}</button> : null}>
        <div className="grid3">
          <Field label={tr("SMTP host")}><input className="input" disabled={!configure} value={f.smtp_host ?? data.host} onChange={(e) => setF({ ...f, smtp_host: e.target.value })} /></Field>
          <Field label={tr("Port")}><input className="input" type="number" disabled={!configure} value={f.smtp_port ?? data.port} onChange={(e) => setF({ ...f, smtp_port: Number(e.target.value) })} /></Field>
          <Field label={tr("Security")}><select className="select" disabled={!configure} value={f.smtp_security ?? data.security} onChange={(e) => setF({ ...f, smtp_security: e.target.value })}>
            <option value="starttls">STARTTLS</option><option value="ssl">TLS/SSL</option><option value="none">{tr("none (internal relay, no login)")}</option></select></Field>
          <Field label={tr("Username")}><input className="input" disabled={!configure} value={f.smtp_user ?? data.user} onChange={(e) => setF({ ...f, smtp_user: e.target.value })} /></Field>
          <Field label={tr("Password (write-only)")}><input className="input" type="password" disabled={!configure} value={f.smtp_password ?? ""} onChange={(e) => setF({ ...f, smtp_password: e.target.value })} placeholder={data.password.configured ? tr("saved — enter to replace") : ""} /></Field>
          <Field label={tr("Sender e-mail")}><input className="input" disabled={!configure} value={f.from_address ?? (data.sender.match(/<(.+)>/)?.[1] || "")} onChange={(e) => setF({ ...f, from_address: e.target.value })} /></Field>
          <Field label={tr("Sender name")}><input className="input" disabled={!configure} value={f.from_name ?? (data.sender.split("<")[0].trim() || "")} onChange={(e) => setF({ ...f, from_name: e.target.value })} /></Field>
          <Field label={tr("Reply-to address")}><input className="input" disabled={!configure} value={f.reply_to ?? data.reply_to} onChange={(e) => setF({ ...f, reply_to: e.target.value })} /></Field>
          <Field label={tr("Max delivery attempts")}><input className="input" type="number" min={1} max={20} disabled={!configure} value={f.max_attempts ?? 5} onChange={(e) => setF({ ...f, max_attempts: Number(e.target.value) })} /></Field>
        </div>
        {configure && <div className="row"><input className="input" style={{ maxWidth: 280 }} type="email" placeholder={tr("Send a test message to…")} value={testTo} onChange={(e) => setTestTo(e.target.value)} />
          <button className="btn" disabled={busy || !testTo} onClick={() => void run(() => post("/api/owner/email/test", { to: testTo }), tr("Test message sent"))}>{tr("Send test")}</button></div>}
      </Section>
      <Section title={tr("Templates")}>
        <Records rows={data.templates.map((t: any) => ({ id: `${t.key}/${t.lang}`, ...t }))} cols={[{ key: "key", label: tr("Template"), render: (t: any) => tr(t.key.replace(/_/g, " ")) }, { key: "lang", label: tr("Language") },
          { key: "subject", label: tr("Subject") }, { key: "custom", label: tr("State"), render: (t: any) => t.custom ? <Tag text={tr("customised")} tone="blue" /> : <Tag text={tr("default")} /> },
          { key: "x", label: "", render: (t: any) => <div className="row"><button className="btn ghost small" onClick={() => void run(() => get(`/api/owner/email/templates/${t.key}/${t.lang}/preview`)).then((p) => setPreview({ ...p, t }))}>{tr("Preview")}</button>
            {templates && <button className="btn ghost small" onClick={() => setTpl({ ...t })}>{tr("Edit")}</button>}
            {templates && t.custom && <button className="btn ghost small" onClick={() => void run(() => del(`/api/owner/email/templates/${t.key}/${t.lang}`), tr("Template reset")).then(reload)}>{tr("Reset")}</button>}</div> }]} />
      </Section>
      <div className="grid2">
        <Section title={tr("Delivery (30 days)")}><SeriesChart points={data.daily.map((d: any) => ({ bucket: d.day, value: d.sent }))} kind="bar" title={tr("Sent")} /></Section>
        <Section title={tr("Suppression list")} actions={configure ? <div className="row"><input className="input" style={{ maxWidth: 220 }} placeholder={tr("address")} value={suppress} onChange={(e) => setSuppress(e.target.value)} />
          <button className="btn small" disabled={!suppress} onClick={() => void run(() => post("/api/owner/email/suppressions", { address: suppress, reason: "manual" }), tr("Suppressed")).then(() => { setSuppress(""); reload(); })}>{tr("Add")}</button></div> : null}>
          <Records rows={data.suppressions.map((s: any) => ({ id: s.address, ...s }))} cols={[{ key: "address", label: tr("Address") }, { key: "reason", label: tr("Reason") },
            { key: "x", label: "", render: (s: any) => configure ? <button className="btn ghost small" onClick={() => void run(() => del(`/api/owner/email/suppressions/${encodeURIComponent(s.address)}`), tr("Removed")).then(reload)}>{tr("Remove")}</button> : null }]} empty={tr("No suppressed addresses.")} />
        </Section>
      </div>
      <Section title={tr("Recent messages")}>
        <Records rows={data.jobs} cols={[{ key: "to_address", label: tr("To") }, { key: "subject", label: tr("Subject") }, { key: "template_key", label: tr("Template") },
          { key: "status", label: tr("Status"), render: (j: any) => <Tag text={j.status} tone={toneFor(j.status)} /> }, { key: "attempts", label: tr("Attempts"), num: true },
          { key: "last_error", label: tr("Error"), render: (j: any) => <span className="small">{j.last_error || "—"}</span> }, { key: "created_at", label: tr("Queued"), render: (j: any) => <When ts={j.created_at} /> },
          { key: "x", label: "", render: (j: any) => configure && ["failed", "suppressed"].includes(j.status) ? <button className="btn ghost small" onClick={() => void run(() => post(`/api/owner/email/jobs/${j.id}/retry`), tr("Queued again")).then(reload)}>{tr("Retry")}</button> : null }]} empty={tr("No messages yet.")} />
      </Section>
      {tpl && <Modal wide title={`${tr(tpl.key.replace(/_/g, " "))} (${tpl.lang})`} onClose={() => setTpl(null)} footer={<>
        <button className="btn" onClick={() => setTpl(null)}>{tr("Cancel")}</button>
        <button className="btn primary" disabled={busy} onClick={() => void run(() => put(`/api/owner/email/templates/${tpl.key}/${tpl.lang}`, { subject: tpl.subject, body: tpl.body }), tr("Template saved")).then(() => { setTpl(null); reload(); })}>{tr("Save")}</button></>}>
        <p className="small muted">{tr("Placeholders: {platform}, {name}, {url}, {link}, {plan}, {amount}, {invoice_url}, {workspace}, {percent}, {title}, {body}, {company}, {email}, {country}, {message}. Unknown placeholders render empty.")}</p>
        <Field label={tr("Subject")}><input className="input" dir="auto" value={tpl.subject} onChange={(e) => setTpl({ ...tpl, subject: e.target.value })} /></Field>
        <Field label={tr("Body")}><textarea className="textarea" dir="auto" rows={10} value={tpl.body} onChange={(e) => setTpl({ ...tpl, body: e.target.value })} /></Field>
      </Modal>}
      {preview && <Modal title={preview.subject} onClose={() => setPreview(null)}><pre dir="auto" style={{ whiteSpace: "pre-wrap", font: "inherit" }}>{preview.body}</pre></Modal>}
    </div>
  );
}
