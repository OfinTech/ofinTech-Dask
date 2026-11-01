import { useState, type ReactNode } from "react";
import { get, patch, post, put } from "../lib/api";
import { useApp } from "../lib/app";
import { ErrorBox, Field, Loading, Modal, PageHead, useAction, useLoad } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { tr } from "../lib/i18n";
import { Records, Section, Tag, Toggle, When, money, toneFor } from "./shared";

export function BillingPage(): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data, error, reload } = useLoad(() => get("/api/owner/billing"), []);
  const [f, setF] = useState<any>({});
  const [health, setHealth] = useState<any>(null);
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const configure = perms.has("billing.configure");
  const save = () => void run(() => put("/api/owner/billing", f), tr("Billing settings saved")).then((r: any) => {
    if (r?.pending_approval) run(async () => undefined, tr("The change awaits a second owner's approval."));
    setF({}); reload();
  });
  return (
    <div className="page">
      <PageHead title={tr("Billing")} sub={tr("Stripe Checkout and the Customer Portal are Stripe-hosted; card data never touches this server. Keys are stored encrypted and never shown again.")} />
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <div className={`kpi-card ${data.ready ? "good" : "watch"}`}><span className="kpi-name">{tr("Status")}</span><span className="kpi-value">{data.ready ? tr("Ready") : data.enabled ? tr("Incomplete") : tr("Off")}</span>
          <span className="small muted">{tr("{0} mode", { 0: data.mode })}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Webhook events")}</span><span className="kpi-value">{data.events_total}</span>
          <span className="small muted">{data.events_failed ? <Tag text={tr("{0} failed", { 0: data.events_failed })} tone="red" /> : tr("none failed")}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Secret key")}</span><span className="kpi-value">{data.secret_key.configured ? `…${data.secret_key.last4}` : "—"}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Webhook secret")}</span><span className="kpi-value">{data.webhook_secret.configured ? `…${data.webhook_secret.last4}` : "—"}</span></div>
      </div>
      {!!data.plans_unsynced?.length && <div className="notice warn" style={{ marginBottom: 16 }}>{tr("Paid plans without Stripe prices yet: {0}. Open the plan and use “Sync to Stripe”.", { 0: data.plans_unsynced.join(", ") })}</div>}
      <Section title={tr("Stripe configuration")} actions={configure ? <button className="btn primary" disabled={busy || !Object.keys(f).length} onClick={save}>{tr("Save")}</button> : null}>
        <div className="row wrap">
          <Toggle label={tr("Billing enabled")} disabled={!configure} checked={f.enabled ?? data.enabled} onChange={(v) => setF({ ...f, enabled: v })} />
          <Toggle label={tr("Customer billing portal")} disabled={!configure} checked={f.portal_enabled ?? data.portal_enabled} onChange={(v) => setF({ ...f, portal_enabled: v })} />
          <Toggle label={tr("Stripe Tax")} disabled={!configure} checked={f.tax_enabled ?? data.tax_enabled} onChange={(v) => setF({ ...f, tax_enabled: v })} />
          <Toggle label={tr("Allow promotion codes at checkout")} disabled={!configure} checked={f.allow_promotion_codes ?? data.allow_promotion_codes} onChange={(v) => setF({ ...f, allow_promotion_codes: v })} />
        </div>
        <div className="grid2">
          <Field label={tr("Environment")}><select className="select" disabled={!configure} value={f.mode ?? data.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}>
            <option value="test">{tr("Test")}</option><option value="live">{tr("Live")}</option></select></Field>
          <Field label={tr("Publishable key")}><input className="input" disabled={!configure} value={f.publishable_key ?? data.publishable_key} onChange={(e) => setF({ ...f, publishable_key: e.target.value })} placeholder="pk_test_…" /></Field>
          <Field label={tr("Secret key (write-only)")}><input className="input" type="password" disabled={!configure} value={f.secret_key ?? ""} onChange={(e) => setF({ ...f, secret_key: e.target.value })} placeholder={data.secret_key.configured ? tr("saved — enter to replace") : "sk_test_…"} /></Field>
          <Field label={tr("Webhook signing secret (write-only)")}><input className="input" type="password" disabled={!configure} value={f.webhook_secret ?? ""} onChange={(e) => setF({ ...f, webhook_secret: e.target.value })} placeholder={data.webhook_secret.configured ? tr("saved — enter to replace") : "whsec_…"} /></Field>
          <Field label={tr("Supported currencies (comma-separated)")}><input className="input" disabled={!configure} value={(f.currencies ?? data.currencies).join(", ")} onChange={(e) => setF({ ...f, currencies: e.target.value.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean) })} /></Field>
          <Field label={tr("Grace period after a failed payment (days)")}><input className="input" type="number" min={1} max={60} disabled={!configure} value={f.grace_days ?? data.grace_days} onChange={(e) => setF({ ...f, grace_days: Number(e.target.value) })} /></Field>
        </div>
        <p className="small muted">{tr("Webhook endpoint to register in Stripe")}: <code>{data.webhook_url}</code> — {tr("events: checkout.session.completed, customer.subscription.*, invoice.paid, invoice.payment_failed, charge.refunded")}</p>
        {configure && <div className="row"><button className="btn" disabled={busy} onClick={() => void run(() => post("/api/owner/billing/test")).then(setHealth)}>{tr("Test connection")}</button>
          {health && (health.ok ? <Tag text={tr("Stripe reachable ({0})", { 0: health.live ? tr("live key") : tr("test key") })} tone="green" /> : <Tag text={health.error} tone="red" />)}</div>}
      </Section>
      <Section title={tr("Webhook events")}>
        <Records rows={data.events} cols={[{ key: "id", label: tr("Event") }, { key: "type", label: tr("Type") }, { key: "status", label: tr("Status"), render: (e: any) => <Tag text={e.status} tone={toneFor(e.status)} /> },
          { key: "attempts", label: tr("Attempts"), num: true }, { key: "error", label: tr("Error"), render: (e: any) => <span className="small">{e.error || "—"}</span> },
          { key: "received_at", label: tr("Received"), render: (e: any) => <When ts={e.received_at} /> },
          { key: "x", label: "", render: (e: any) => configure && ["failed", "dead_letter"].includes(e.status) ? <button className="btn ghost small" onClick={() => void run(() => post(`/api/owner/billing/events/${e.id}/retry`), tr("Retried")).then(reload)}>{tr("Retry")}</button> : null }]}
          empty={tr("No webhook events received yet.")} />
      </Section>
    </div>
  );
}

const EMPTY_COUPON = { code: "", kind: "percent", percent_off: 10, amount_off: 500, currency: "USD", duration: "once", duration_months: 3, expires_at: "", max_redemptions: "",
  per_customer_limit: 1, plan_ids: [] as string[], first_time_only: false, min_amount: "" };

export function CouponsPage(): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data, error, reload } = useLoad(() => get("/api/owner/coupons"), []);
  const { data: plans } = useLoad(() => get("/api/owner/plans"), []);
  const [edit, setEdit] = useState<any | null>(null);
  const [showRed, setShowRed] = useState<any | null>(null);
  const { data: red } = useLoad(() => (showRed ? get(`/api/owner/coupons/${showRed.id}/redemptions`) : Promise.resolve(null)), [showRed?.id]);
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const manage = perms.has("coupons.manage");
  const create = () => {
    const body: any = { code: edit.code, duration: edit.duration, per_customer_limit: Number(edit.per_customer_limit) || 1, plan_ids: edit.plan_ids, first_time_only: edit.first_time_only };
    if (edit.kind === "percent") body.percent_off = Number(edit.percent_off); else { body.amount_off = Number(edit.amount_off); body.currency = edit.currency; }
    if (edit.duration === "repeating") body.duration_months = Number(edit.duration_months);
    if (edit.expires_at) body.expires_at = Date.parse(edit.expires_at) / 1000;
    if (edit.max_redemptions) body.max_redemptions = Number(edit.max_redemptions);
    if (edit.min_amount) body.min_amount = Number(edit.min_amount);
    void run(() => post("/api/owner/coupons", body), tr("Coupon created")).then(() => { setEdit(null); reload(); });
  };
  const perf = new Map((data.performance || []).map((p: any) => [p.id, p]));
  return (
    <div className="page">
      <PageHead title={tr("Coupons")} sub={tr("Discount codes validated on the server and mirrored to Stripe as a coupon plus a promotion code. Discount, duration and currency are fixed after creation, as in Stripe.")}
        actions={manage ? <button className="btn primary" onClick={() => setEdit({ ...EMPTY_COUPON })}>{tr("New coupon")}</button> : undefined} />
      <Records rows={data.items} cols={[
        { key: "code", label: tr("Code"), render: (c: any) => <strong>{c.code}</strong> },
        { key: "discount", label: tr("Discount"), render: (c: any) => c.percent_off ? `${c.percent_off}%` : money(c.amount_off, c.currency) },
        { key: "duration", label: tr("Duration"), render: (c: any) => c.duration === "repeating" ? tr("{0} months", { 0: c.duration_months }) : tr(c.duration) },
        { key: "redemptions", label: tr("Redemptions"), num: true, render: (c: any) => `${c.redemptions}${c.max_redemptions ? ` / ${c.max_redemptions}` : ""}` },
        { key: "discounted", label: tr("Discounted revenue"), num: true, render: (c: any) => money((perf.get(c.id) as any)?.discounted || 0, c.currency || "USD") },
        { key: "expires_at", label: tr("Expires"), render: (c: any) => c.expires_at ? fmtDateTime(c.expires_at) : "—" },
        { key: "stripe", label: tr("Stripe"), render: (c: any) => c.stripe_promotion_code_id ? <Tag text={tr("synced")} tone="green" /> : <Tag text={tr("not synced")} tone="amber" /> },
        { key: "active", label: tr("Status"), render: (c: any) => c.active ? <Tag text={tr("active")} tone="green" /> : <Tag text={tr("inactive")} /> },
        { key: "x", label: "", render: (c: any) => <div className="row">
          <button className="btn ghost small" onClick={() => setShowRed(c)}>{tr("History")}</button>
          {manage && <button className="btn ghost small" disabled={busy} onClick={() => void run(() => patch(`/api/owner/coupons/${c.id}`, { active: !c.active }), c.active ? tr("Coupon disabled") : tr("Coupon enabled")).then(reload)}>{c.active ? tr("Disable") : tr("Enable")}</button>}
          {manage && !c.stripe_promotion_code_id && <button className="btn ghost small" disabled={busy} onClick={() => void run(() => post(`/api/owner/coupons/${c.id}/sync-stripe`), tr("Synced to Stripe")).then(reload)}>{tr("Sync")}</button>}
        </div> },
      ]} empty={tr("No coupons yet.")} />
      {edit && <Modal title={tr("New coupon")} onClose={() => setEdit(null)} footer={<>
        <button className="btn" onClick={() => setEdit(null)}>{tr("Cancel")}</button>
        <button className="btn primary" disabled={busy || !edit.code} onClick={create}>{tr("Create")}</button></>}>
        <Field label={tr("Coupon code")}><input className="input" value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value.toUpperCase() })} placeholder="LAUNCH10" /></Field>
        <div className="grid2">
          <Field label={tr("Type")}><select className="select" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}><option value="percent">{tr("Percentage")}</option><option value="amount">{tr("Fixed amount")}</option></select></Field>
          {edit.kind === "percent" ? <Field label={tr("Percent off")}><input className="input" type="number" min={1} max={100} value={edit.percent_off} onChange={(e) => setEdit({ ...edit, percent_off: e.target.value })} /></Field>
            : <><Field label={tr("Amount off (minor units)")}><input className="input" type="number" min={1} value={edit.amount_off} onChange={(e) => setEdit({ ...edit, amount_off: e.target.value })} /></Field>
              <Field label={tr("Currency")}><input className="input" maxLength={3} value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value.toUpperCase() })} /></Field></>}
          <Field label={tr("Duration")}><select className="select" value={edit.duration} onChange={(e) => setEdit({ ...edit, duration: e.target.value })}>
            <option value="once">{tr("once")}</option><option value="repeating">{tr("repeating")}</option><option value="forever">{tr("forever")}</option></select></Field>
          {edit.duration === "repeating" && <Field label={tr("Months")}><input className="input" type="number" min={1} max={36} value={edit.duration_months} onChange={(e) => setEdit({ ...edit, duration_months: e.target.value })} /></Field>}
          <Field label={tr("Expiration date")}><input className="input" type="date" value={edit.expires_at} onChange={(e) => setEdit({ ...edit, expires_at: e.target.value })} /></Field>
          <Field label={tr("Redemption limit (blank = none)")}><input className="input" type="number" min={1} value={edit.max_redemptions} onChange={(e) => setEdit({ ...edit, max_redemptions: e.target.value })} /></Field>
          <Field label={tr("Per-customer limit")}><input className="input" type="number" min={1} value={edit.per_customer_limit} onChange={(e) => setEdit({ ...edit, per_customer_limit: e.target.value })} /></Field>
          <Field label={tr("Minimum purchase (minor units)")}><input className="input" type="number" min={0} value={edit.min_amount} onChange={(e) => setEdit({ ...edit, min_amount: e.target.value })} /></Field>
        </div>
        <Field label={tr("Applicable plans (none = all)")}>
          <div className="row wrap">{(plans?.items || []).filter((p: any) => !p.contact_sales && (p.monthly_price || p.annual_price)).map((p: any) =>
            <Toggle key={p.id} label={p.name} checked={edit.plan_ids.includes(p.id)} onChange={(v) => setEdit({ ...edit, plan_ids: v ? [...edit.plan_ids, p.id] : edit.plan_ids.filter((x: string) => x !== p.id) })} />)}</div>
        </Field>
        <Toggle label={tr("First-time customers only")} checked={edit.first_time_only} onChange={(v) => setEdit({ ...edit, first_time_only: v })} />
      </Modal>}
      {showRed && <Modal title={tr("Redemptions of {0}", { 0: showRed.code })} onClose={() => setShowRed(null)}>
        {!red ? <Loading /> : <Records rows={red.items} cols={[{ key: "email", label: tr("Customer") }, { key: "amount_discounted", label: tr("Discounted"), num: true, render: (r: any) => money(r.amount_discounted, showRed.currency || "USD") },
          { key: "created_at", label: tr("When"), render: (r: any) => fmtDateTime(r.created_at) }]} empty={tr("Not redeemed yet.")} />}
      </Modal>}
    </div>
  );
}
