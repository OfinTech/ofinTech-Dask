import { useState, type ReactNode } from "react";
import { get, patch, post } from "../lib/api";
import { useApp } from "../lib/app";
import { ErrorBox, Field, Loading, Modal, PageHead, useAction, useLoad } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { tr } from "../lib/i18n";
import { Pager, Records, Section, Tag, Toggle, When, money, moneyMap, toneFor } from "./shared";

const EMPTY = { code: "", name: "", description: "", monthly_price: 0, annual_price: 0, currency: "USD", max_workspaces: 1, storage_quota_mb: 1024,
  max_members: 0, features: {} as Record<string, any>, trial_days: 0, billing_interval: "both", visibility: "public", contact_sales: false, status: "draft",
  is_default: false, position: 10, stripe_product_id: "", stripe_price_monthly: "", stripe_price_annual: "" };

export function PlansPage(): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data, error, reload } = useLoad(() => get("/api/owner/plans"), []);
  const [edit, setEdit] = useState<any | null>(null);
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const manage = perms.has("plans.manage");
  const save = async () => {
    const body: any = { ...edit };
    const id = body.id;
    delete body.id; delete body.subscribers; delete body.created_at; delete body.updated_at;
    for (const k of ["stripe_product_id", "stripe_price_monthly", "stripe_price_annual"]) if (!body[k]) delete body[k];
    const r: any = await run(() => (id ? patch(`/api/owner/plans/${id}`, body) : post("/api/owner/plans", body)), tr("Plan saved"));
    if (r?.pending_approval) run(async () => undefined, tr("The change awaits a second owner's approval."));
    setEdit(null);
    reload();
  };
  return (
    <div className="page">
      <PageHead title={tr("Plans & Features")} sub={data.hosted ? tr("What each hosted plan includes. Limits are enforced on the server; changes apply to subscribers at once.")
        : tr("This is a self-hosted installation: plans are kept for a hosted deployment and impose nothing here.")}
        actions={manage ? <button className="btn primary" onClick={() => setEdit({ ...EMPTY })}>{tr("New plan")}</button> : undefined} />
      <div className="kpi-grid">
        {data.items.map((p: any) => (
          <div key={p.id} className={`kpi-card ${p.status === "active" ? "good" : ""}`} style={{ cursor: manage ? "pointer" : "default" }} onClick={() => manage && setEdit({ ...p })}>
            <span className="kpi-name">{p.name} <span className="muted">({p.code})</span> {p.is_default && <Tag text={tr("default")} tone="blue" />} <Tag text={p.status} tone={toneFor(p.status)} />
              {p.visibility === "hidden" && <Tag text={tr("hidden")} />}</span>
            <span className="kpi-value">{p.contact_sales ? tr("Contact sales") : p.monthly_price ? `${money(p.monthly_price, p.currency)}/${tr("mo")}` : tr("Free")}</span>
            <span className="small muted">{p.annual_price ? `${money(p.annual_price, p.currency)}/${tr("yr")} · ` : ""}{tr("{0} workspaces", { 0: p.max_workspaces || "∞" })} · {p.storage_quota_mb ? `${p.storage_quota_mb} MB` : "∞"} · {tr("{0} subscribers", { 0: p.subscribers })}</span>
            <span className="small">{Object.entries(p.features).filter(([k, v]) => v === true && k in data.features).length}/{Object.keys(data.features).length} {tr("features")}
              {(p.monthly_price || p.annual_price) && !p.contact_sales && !(p.stripe_price_monthly || p.stripe_price_annual) ? <> · <Tag text={tr("not synced to Stripe")} tone="amber" /></> : null}</span>
          </div>
        ))}
      </div>
      <Section title={tr("Enterprise capabilities (as advertised on the Contact Sales page)")}>
        <Records rows={data.enterprise} cols={[{ key: "label", label: tr("Capability") }, { key: "status", label: tr("State"), render: (c: any) =>
          <Tag text={c.status === "available" ? tr("available") : c.status === "planned" ? tr("planned") : tr("by contract")} tone={c.status === "available" ? "green" : c.status === "planned" ? "amber" : "blue"} /> }]} />
        <p className="small muted">{tr("Nothing marked planned is sold as working. SSO (SAML/OIDC) is not implemented yet.")}</p>
      </Section>
      {edit && <Modal wide title={edit.id ? tr("Edit plan") : tr("New plan")} onClose={() => setEdit(null)} footer={<>
        <button className="btn" onClick={() => setEdit(null)}>{tr("Cancel")}</button>
        {edit.id && (edit.monthly_price || edit.annual_price) && !edit.contact_sales && <button className="btn" disabled={busy}
          onClick={() => void run(() => post(`/api/owner/plans/${edit.id}/sync-stripe`), tr("Stripe product and prices created")).then(() => { setEdit(null); reload(); })}>{tr("Sync to Stripe")}</button>}
        <button className="btn primary" disabled={busy || !edit.code || !edit.name} onClick={() => void save()}>{tr("Save")}</button></>}>
        <div className="grid2">
          <Field label={tr("Internal plan code")}><input className="input" value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></Field>
          <Field label={tr("Plan name")}><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
        </div>
        <Field label={tr("Public description")}><textarea className="textarea" rows={2} value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
        <div className="grid3">
          <Field label={tr("Monthly price (minor units)")}><input className="input" type="number" min={0} value={edit.monthly_price} onChange={(e) => setEdit({ ...edit, monthly_price: Number(e.target.value) })} /></Field>
          <Field label={tr("Annual price (minor units)")}><input className="input" type="number" min={0} value={edit.annual_price} onChange={(e) => setEdit({ ...edit, annual_price: Number(e.target.value) })} /></Field>
          <Field label={tr("Currency")}><input className="input" maxLength={3} value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value.toUpperCase() })} /></Field>
          <Field label={tr("Workspace limit (0 = unlimited)")}><input className="input" type="number" min={0} value={edit.max_workspaces} onChange={(e) => setEdit({ ...edit, max_workspaces: Number(e.target.value) })} /></Field>
          <Field label={tr("Storage quota (MB, 0 = unlimited)")}><input className="input" type="number" min={0} value={edit.storage_quota_mb} onChange={(e) => setEdit({ ...edit, storage_quota_mb: Number(e.target.value) })} /></Field>
          <Field label={tr("Members per workspace (0 = unlimited)")}><input className="input" type="number" min={0} value={edit.max_members} onChange={(e) => setEdit({ ...edit, max_members: Number(e.target.value) })} /></Field>
          <Field label={tr("Trial days")}><input className="input" type="number" min={0} value={edit.trial_days} onChange={(e) => setEdit({ ...edit, trial_days: Number(e.target.value) })} /></Field>
          <Field label={tr("Billing interval")}><select className="select" value={edit.billing_interval} onChange={(e) => setEdit({ ...edit, billing_interval: e.target.value })}>
            <option value="both">{tr("Monthly and annual")}</option><option value="month">{tr("Monthly")}</option><option value="year">{tr("Annual")}</option></select></Field>
          <Field label={tr("AI allowance (tokens/month, 0 = unlimited)")}><input className="input" type="number" min={0} value={edit.features.ai_monthly_tokens || 0}
            onChange={(e) => setEdit({ ...edit, features: { ...edit.features, ai_monthly_tokens: Number(e.target.value) } })} /></Field>
          <Field label={tr("Visibility")}><select className="select" value={edit.visibility} onChange={(e) => setEdit({ ...edit, visibility: e.target.value })}>
            <option value="public">{tr("Public")}</option><option value="hidden">{tr("Hidden")}</option></select></Field>
          <Field label={tr("Availability")}><select className="select" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}>
            <option value="draft">{tr("Draft")}</option><option value="active">{tr("Active")}</option><option value="archived">{tr("Archived")}</option></select></Field>
          <Field label={tr("Position")}><input className="input" type="number" value={edit.position} onChange={(e) => setEdit({ ...edit, position: Number(e.target.value) })} /></Field>
        </div>
        <div className="row wrap">
          <Toggle label={tr("Contact Sales mode (no online checkout)")} checked={Boolean(edit.contact_sales)} onChange={(v) => setEdit({ ...edit, contact_sales: v })} />
          <Toggle label={tr("Default plan for accounts without a subscription")} checked={Boolean(edit.is_default)} onChange={(v) => setEdit({ ...edit, is_default: v })} />
        </div>
        <h4>{tr("Feature entitlements")}</h4>
        <div className="grid2">
          {Object.entries(data.features).map(([k, label]) => <Toggle key={k} label={String(label)} checked={Boolean(edit.features[k])}
            onChange={(v) => setEdit({ ...edit, features: { ...edit.features, [k]: v } })} />)}
        </div>
        <div className="grid3">
          <Field label={tr("Stripe product ID")}><input className="input" value={edit.stripe_product_id || ""} onChange={(e) => setEdit({ ...edit, stripe_product_id: e.target.value })} /></Field>
          <Field label={tr("Stripe monthly price ID")}><input className="input" value={edit.stripe_price_monthly || ""} onChange={(e) => setEdit({ ...edit, stripe_price_monthly: e.target.value })} /></Field>
          <Field label={tr("Stripe annual price ID")}><input className="input" value={edit.stripe_price_annual || ""} onChange={(e) => setEdit({ ...edit, stripe_price_annual: e.target.value })} /></Field>
        </div>
      </Modal>}
    </div>
  );
}

export function SubscriptionsPage(): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ status, q, limit: "50", offset: String(offset) });
  const { data, error, reload } = useLoad(() => get(`/api/owner/subscriptions?${params}`), [params.toString()]);
  const { data: invoices } = useLoad(() => get("/api/owner/invoices?limit=50"), []);
  const { busy, run } = useAction();
  return (
    <div className="page">
      <PageHead title={tr("Subscriptions")} sub={tr("Stripe is the authority for paid subscriptions; owner-assigned ones are contracts recorded here.")}
        actions={<a className="btn small" href="/api/owner/export/subscriptions.csv" download>{tr("Export CSV")}</a>} />
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <div className="kpi-card"><span className="kpi-name">{tr("MRR")}</span><span className="kpi-value">{moneyMap(data?.mrr?.by_currency)}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("ARR")}</span><span className="kpi-value">{moneyMap(data?.mrr?.arr_by_currency)}</span></div>
        <div className="kpi-card"><span className="kpi-name">{tr("Subscriptions")}</span><span className="kpi-value">{data?.total ?? "—"}</span></div>
      </div>
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <input className="input" style={{ maxWidth: 280 }} placeholder={tr("Search e-mail or id")} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
        <select className="select" style={{ width: 170 }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label={tr("Status")}>
          <option value="">{tr("Any status")}</option>{["trialing", "active", "past_due", "canceled", "unpaid"].map((s) => <option key={s} value={s}>{s}</option>)}</select>
      </div>
      <ErrorBox error={error} />
      {!data ? <Loading /> : <>
        <Records rows={data.items} cols={[
          { key: "email", label: tr("Account") }, { key: "plan_name", label: tr("Plan") },
          { key: "status", label: tr("Status"), render: (s: any) => <><Tag text={s.status} tone={toneFor(s.status)} />{s.cancel_at_period_end ? <> <Tag text={tr("ends at period end")} tone="amber" /></> : null}{s.scheduled_plan_id ? <> <Tag text={tr("change scheduled")} tone="blue" /></> : null}</> },
          { key: "interval", label: tr("Interval") }, { key: "source", label: tr("Source") },
          { key: "amount", label: tr("Amount"), num: true, render: (s: any) => money(s.interval === "year" ? s.annual_price : s.monthly_price, s.currency) },
          { key: "current_period_end", label: tr("Period end"), render: (s: any) => s.current_period_end ? fmtDateTime(s.current_period_end) : "—" },
          { key: "updated_at", label: tr("Updated"), render: (s: any) => <When ts={s.updated_at} /> },
          { key: "x", label: "", render: (s: any) => perms.has("subscriptions.manage") && s.status !== "canceled" ? <div className="row">
            {s.source === "stripe" && <button className="btn ghost small" disabled={busy} onClick={() => void run(() => post(`/api/owner/subscriptions/${s.id}/reconcile`), tr("Reconciled with Stripe")).then(reload)}>{tr("Reconcile")}</button>}
            <button className="btn ghost small" disabled={busy} onClick={() => void run(() => post(`/api/owner/subscriptions/${s.id}/cancel`), tr("Cancellation scheduled")).then(reload)}>{tr("Cancel at period end")}</button>
          </div> : null },
        ]} empty={tr("No subscriptions yet.")} />
        <Pager total={data.total} limit={50} offset={offset} onChange={setOffset} />
      </>}
      <Section title={tr("Recent invoices")}>
        {!invoices ? <Loading /> : <Records rows={invoices.items} cols={[{ key: "email", label: tr("Account") }, { key: "status", label: tr("Status"), render: (i: any) => <Tag text={i.status} tone={toneFor(i.status)} /> },
          { key: "amount_paid", label: tr("Paid"), num: true, render: (i: any) => money(i.amount_paid, i.currency) }, { key: "amount_due", label: tr("Due"), num: true, render: (i: any) => money(i.amount_due, i.currency) },
          { key: "amount_refunded", label: tr("Refunded"), num: true, render: (i: any) => i.amount_refunded ? money(i.amount_refunded, i.currency) : "—" },
          { key: "created", label: tr("Date"), render: (i: any) => <When ts={i.created} /> },
          { key: "hosted_invoice_url", label: "", render: (i: any) => i.hosted_invoice_url ? <a href={i.hosted_invoice_url} target="_blank" rel="noreferrer">{tr("Open")}</a> : null }]} empty={tr("No invoices yet.")} />}
      </Section>
    </div>
  );
}
