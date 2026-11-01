// Pricing and Contact Sales for the hosted edition, and the person's own plan page (Settings → Plan).
// Nothing here changes a subscription: checkout and the portal are Stripe-hosted and the engine follows Stripe.
import { useState, type FormEvent, type ReactNode } from "react";
import { get, post } from "../lib/api";
import { useApp } from "../lib/app";
import { ErrorBox, Field, Loading, PageHead, useAction, useLoad } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { tr } from "../lib/i18n";

function money(minor: number, currency: string): string {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(minor / 100); } catch { return `${minor / 100} ${currency}`; }
}

const FEATURE_LABELS: Record<string, string> = {
  ask_finance: "Ask Your Finance", autonomous_reconciliation: "Autonomous reconciliation", financial_intelligence: "Financial Intelligence",
  bank_connections: "Bank connections", erp_connections: "ERP connections", file_storage: "Hosted file storage", advanced_analytics: "Advanced analytics",
  api_access: "API access", data_export: "Data export", advanced_audit_logs: "Advanced audit logs", workflows: "Workflows", priority_support: "Priority support",
};

export function PricingPage(): ReactNode {
  const { session } = useApp();
  const { data, error } = useLoad(() => get("/api/plans"), []);
  const [interval, setInterval] = useState<"month" | "year">("month");
  const [coupon, setCoupon] = useState("");
  const [check, setCheck] = useState<any>(null);
  const [contact, setContact] = useState(false);
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const mine = session.entitlements?.plan_code;
  async function buy(planId: string) {
    const r: any = await run(() => post("/api/billing/checkout", { plan_id: planId, interval, coupon: coupon || null }));
    if (r?.url) location.href = r.url;
  }
  return (
    <div className="page">
      <PageHead title={tr("Plans")} sub={data.hosted ? tr("The open-source edition stays free to self-host. Hosted plans add workspaces, storage and support.")
        : tr("This installation is self-hosted: every feature is available without a plan. Plans apply to the hosted service only.")}
        actions={<div className="seg" role="tablist"><button role="tab" aria-pressed={interval === "month"} onClick={() => setInterval("month")}>{tr("Monthly")}</button>
          <button role="tab" aria-pressed={interval === "year"} onClick={() => setInterval("year")}>{tr("Annual")}</button></div>} />
      <div className="kpi-grid">
        {data.plans.map((p: any) => {
          const price = interval === "month" ? p.monthly_price : p.annual_price;
          const current = mine === p.code;
          return (
            <div key={p.id} className={`kpi-card ${current ? "good" : ""}`} style={{ cursor: "default" }}>
              <span className="kpi-name"><strong>{p.name}</strong>{current && <> · <span className="tag green">{tr("your plan")}</span></>}</span>
              <span className="kpi-value">{p.contact_sales ? tr("Custom") : price ? <>{money(price, p.currency)}<span className="small muted">/{interval === "month" ? tr("mo") : tr("yr")}</span></> : tr("Free")}</span>
              <p className="small muted">{p.description}</p>
              <ul className="small">
                <li>{p.max_workspaces ? tr("{0} workspaces", { 0: p.max_workspaces }) : tr("Custom workspace limits")}</li>
                <li>{p.storage_quota_mb ? tr("{0} MB hosted storage", { 0: p.storage_quota_mb }) : tr("Custom storage allocation")}</li>
                <li>{p.max_members ? tr("{0} members per workspace", { 0: p.max_members }) : tr("Unlimited members")}</li>
                {Object.entries(p.features).filter(([k, v]) => v === true && FEATURE_LABELS[k]).map(([k]) => <li key={k}>{tr(FEATURE_LABELS[k])}</li>)}
                {p.trial_days ? <li>{tr("{0}-day free trial", { 0: p.trial_days })}</li> : null}
              </ul>
              {p.contact_sales ? <button className="btn" onClick={() => setContact(true)}>{tr("Contact sales")}</button>
                : price && data.billing && !current && data.hosted ? <button className="btn primary" disabled={busy} onClick={() => void buy(p.id)}>{tr("Choose {0}", { 0: p.name })}</button>
                : price && !data.billing && data.hosted ? <span className="small muted">{tr("Online billing is not available yet.")}</span> : null}
            </div>
          );
        })}
      </div>
      {data.billing && data.hosted && <div className="row" style={{ marginTop: 16 }}>
        <input className="input" style={{ maxWidth: 220 }} placeholder={tr("Coupon code")} value={coupon} onChange={(e) => { setCoupon(e.target.value.toUpperCase()); setCheck(null); }} />
        <button className="btn small" disabled={!coupon || busy} onClick={() => void run(() => post("/api/billing/coupon-check", { code: coupon, plan_id: data.plans.find((p: any) => !p.contact_sales && p.monthly_price)?.id, interval })).then(setCheck)}>{tr("Check")}</button>
        {check && <span className="small">{check.percent_off ? `${check.percent_off}% ${tr("off")}` : `${money(check.amount_off, check.currency)} ${tr("off")}`} → {money(check.total, check.currency)}</span>}
      </div>}
      <section className="panel panel-pad" style={{ marginTop: 24 }}>
        <h3>{tr("Enterprise")}</h3>
        <ul className="small">{data.enterprise.map((c: any) => <li key={c.key}>{c.label} — <span className={`tag ${c.status === "available" ? "green" : c.status === "planned" ? "amber" : "blue"}`}>{c.status === "available" ? tr("available") : c.status === "planned" ? tr("planned") : tr("by contract")}</span></li>)}</ul>
        <button className="btn" onClick={() => setContact(true)}>{tr("Contact sales")}</button>
      </section>
      {contact && <ContactSales onClose={() => setContact(false)} />}
    </div>
  );
}

export function ContactSales({ onClose }: { onClose: () => void }): ReactNode {
  const { session } = useApp();
  const [f, setF] = useState<any>({ name: session.user?.name || "", email: session.user?.email || "", company: "", country: "", company_size: "", users_estimate: "", workspaces_needed: "",
    storage_estimate: "", features: [] as string[], deployment: "", message: "" });
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await post("/api/sales/contact", { ...f, users_estimate: f.users_estimate ? Number(f.users_estimate) : null, workspaces_needed: f.workspaces_needed ? Number(f.workspaces_needed) : null,
        country: f.country || null, company_size: f.company_size || null, deployment: f.deployment || null, lang: document.documentElement.lang });
      setDone(true);
    } catch (err) { setError((err as Error).message); }
  }
  const toggle = (k: string) => setF({ ...f, features: f.features.includes(k) ? f.features.filter((x: string) => x !== k) : [...f.features, k] });
  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <form className="modal wide" role="dialog" aria-modal="true" aria-label={tr("Contact sales")} onSubmit={submit}>
        <div className="modal-head"><h2>{tr("Contact sales")}</h2><button type="button" className="btn ghost small" onClick={onClose}>✕</button></div>
        <div className="modal-body stack">
          {done ? <p>{tr("Thank you. We received your enquiry and will be in touch shortly.")}</p> : <>
            <div className="grid2">
              <Field label={tr("Name")}><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
              <Field label={tr("Business e-mail")}><input className="input" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
              <Field label={tr("Company")}><input className="input" required value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} /></Field>
              <Field label={tr("Country (ISO code)")}><input className="input" maxLength={2} value={f.country} onChange={(e) => setF({ ...f, country: e.target.value.toUpperCase() })} /></Field>
              <Field label={tr("Company size")}><select className="select" value={f.company_size} onChange={(e) => setF({ ...f, company_size: e.target.value })}><option value="">—</option>{["1-10", "11-50", "51-200", "201-1000", "1000+"].map((s) => <option key={s}>{s}</option>)}</select></Field>
              <Field label={tr("Deployment preference")}><select className="select" value={f.deployment} onChange={(e) => setF({ ...f, deployment: e.target.value })}><option value="">—</option><option value="hosted">{tr("Hosted")}</option><option value="private">{tr("Private deployment")}</option><option value="undecided">{tr("Undecided")}</option></select></Field>
              <Field label={tr("Estimated users")}><input className="input" type="number" min={0} value={f.users_estimate} onChange={(e) => setF({ ...f, users_estimate: e.target.value })} /></Field>
              <Field label={tr("Workspaces needed")}><input className="input" type="number" min={0} value={f.workspaces_needed} onChange={(e) => setF({ ...f, workspaces_needed: e.target.value })} /></Field>
              <Field label={tr("Estimated storage")}><input className="input" placeholder="e.g. 500 GB" value={f.storage_estimate} onChange={(e) => setF({ ...f, storage_estimate: e.target.value })} /></Field>
            </div>
            <Field label={tr("Interested features")}><div className="row wrap">{["sso", "audit_retention", "private_deployment", "custom_integrations", "dedicated_support", "financial_ai"].map((k) =>
              <label key={k} className="row small"><input type="checkbox" checked={f.features.includes(k)} onChange={() => toggle(k)} /> {tr(k.replace(/_/g, " "))}</label>)}</div></Field>
            <Field label={tr("Message")}><textarea className="textarea" rows={3} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} /></Field>
            <input type="text" name="website" tabIndex={-1} autoComplete="off" style={{ position: "absolute", left: -9999 }} aria-hidden="true" onChange={(e) => setF({ ...f, website: e.target.value })} />
            <ErrorBox error={error} />
          </>}
        </div>
        <div className="modal-foot"><button type="button" className="btn" onClick={onClose}>{done ? tr("Close") : tr("Cancel")}</button>{!done && <button className="btn primary">{tr("Send enquiry")}</button>}</div>
      </form>
    </div>
  );
}

/** Settings → Plan: the person's plan, usage against it, invoices, and the billing actions. */
export function PlanTab(): ReactNode {
  const { session, reloadSession } = useApp();
  const { data, error, reload } = useLoad(() => get("/api/me/plan"), []);
  const { busy, run } = useAction();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const e = data.entitlements;
  const sub = data.subscription;
  return (
    <div className="stack" style={{ maxWidth: 820 }}>
      <div className="panel panel-pad stack">
        <h3>{data.plan ? data.plan.name : tr("No plan")}{sub && <> · <span className={`tag ${sub.status === "active" ? "green" : "amber"}`}>{sub.status}</span></>}</h3>
        {!session.hosted && <p className="muted small">{tr("Self-hosted installation: no limits apply.")}</p>}
        {session.hosted && <ul className="small">
          <li>{tr("Workspaces")}: {e.max_workspaces || tr("unlimited")}</li>
          <li>{tr("Hosted storage")}: {e.storage_quota_mb ? `${e.storage_quota_mb} MB` : tr("unlimited")}</li>
          <li>{tr("Members per workspace")}: {e.max_members || tr("unlimited")}</li>
          <li>{tr("AI allowance")}: {e.features.ai_monthly_tokens ? tr("{0} of {1} tokens used this month", { 0: Intl.NumberFormat().format(data.ai_tokens_this_month), 1: Intl.NumberFormat().format(e.features.ai_monthly_tokens) }) : tr("unlimited")}</li>
          {sub?.current_period_end && <li>{sub.cancel_at_period_end ? tr("Ends on {0}", { 0: fmtDateTime(sub.current_period_end) }) : tr("Renews on {0}", { 0: fmtDateTime(sub.current_period_end) })}</li>}
          {sub?.status === "past_due" && <li className="tag red">{tr("Payment failed — update your payment method to keep the plan.")}</li>}
        </ul>}
        <div className="row wrap">
          <a className="btn primary" href="#/pricing">{tr("See plans")}</a>
          {data.billing && sub?.source === "stripe" && data.portal && <button className="btn" disabled={busy} onClick={() => void run(() => post("/api/billing/portal")).then((r: any) => { if (r?.url) location.href = r.url; })}>{tr("Manage billing")}</button>}
          {sub && sub.status !== "canceled" && !sub.cancel_at_period_end && <button className="btn danger" disabled={busy} onClick={() => void run(() => post("/api/billing/cancel"), tr("Your subscription ends at the end of the period.")).then(() => { reload(); void reloadSession(); })}>{tr("Cancel subscription")}</button>}
        </div>
      </div>
      {!!data.invoices.length && <div className="panel panel-pad"><h3>{tr("Invoices")}</h3>
        <table className="ledger"><thead><tr><th>{tr("Date")}</th><th>{tr("Status")}</th><th className="num">{tr("Amount")}</th><th /></tr></thead><tbody>
          {data.invoices.map((i: any) => <tr key={i.id}><td>{fmtDateTime(i.created)}</td><td>{i.status}</td><td className="num">{(i.amount_paid / 100).toFixed(2)} {i.currency}</td>
            <td>{i.hosted_invoice_url && <a href={i.hosted_invoice_url} target="_blank" rel="noreferrer">{tr("Open")}</a>}</td></tr>)}</tbody></table></div>}
    </div>
  );
}
