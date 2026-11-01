import { useState, type ReactNode } from "react";
import { get, post } from "../lib/api";
import { useApp } from "../lib/app";
import { ErrorBox, Loading, PageHead, useAction, useLoad } from "../components/ui";
import { tr } from "../lib/i18n";
import { Kpi, Range, RangePicker, Records, Section, SeriesChart, When, moneyMap, rangeQuery, bytes } from "./shared";

export function OverviewPage(): ReactNode {
  const { session } = useApp();
  const [range, setRange] = useState<Range>({ period: "30d" });
  const { data, error, reload } = useLoad(() => get(`/api/owner/overview?${rangeQuery(range)}`), [rangeQuery(range)]);
  const { data: growth } = useLoad(() => get(`/api/owner/analytics/series/user_growth?${rangeQuery(range)}`), [rangeQuery(range)]);
  const { data: revenue } = useLoad(() => get(`/api/owner/analytics/series/revenue?${rangeQuery(range)}`), [rangeQuery(range)]);
  const { data: funnel } = useLoad(() => get(`/api/owner/analytics/series/funnel?${rangeQuery(range)}`), [rangeQuery(range)]);
  const { run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!data) return <div className="page"><Loading /></div>;
  const k = data.kpis;
  const d = data.definitions || {};
  return (
    <div className="page">
      <PageHead title={tr("Overview")} sub={session.hosted ? tr("The hosted platform at a glance. Every figure comes from collected data; unavailable values show as n/a.")
        : tr("Self-hosted edition: plans and billing are off. Figures come from this installation's own records.")} actions={<RangePicker value={range} onChange={setRange} />} />
      {!!data.pending_changes?.length && <div className="notice warn" style={{ marginBottom: 16 }}>
        {tr("{0} critical change(s) await a second owner's approval.", { 0: data.pending_changes.length })} <a href="#/owner/security">{tr("Review")}</a></div>}
      <div className="kpi-group"><h3 className="small muted">{tr("People")}</h3><div className="kpi-grid">
        <Kpi label={tr("Total registered users")} value={k.total_users} hint={d.total_users} />
        <Kpi label={tr("New users today")} value={k.new_users_today} />
        <Kpi label={tr("New users this week")} value={k.new_users_week} />
        <Kpi label={tr("New users this month")} value={k.new_users_month} />
        <Kpi label={tr("Active users (period)")} value={k.active_users} hint={d.active_users} />
        <Kpi label={tr("DAU")} value={k.dau} hint={d.dau} />
        <Kpi label={tr("WAU")} value={k.wau} hint={d.wau} />
        <Kpi label={tr("MAU")} value={k.mau} hint={d.mau} />
      </div></div>
      <div className="kpi-group"><h3 className="small muted">{tr("Workspaces and revenue")}</h3><div className="kpi-grid">
        <Kpi label={tr("Total workspaces")} value={k.total_workspaces} />
        <Kpi label={tr("Free workspaces")} value={k.free_workspaces} hint={d.free_workspaces} />
        <Kpi label={tr("Paid workspaces")} value={k.paid_workspaces} hint={d.paid_workspaces} />
        <Kpi label={tr("Active subscriptions")} value={k.active_subscriptions} hint={d.active_subscriptions} />
        <Kpi label={tr("Trials")} value={k.trial_subscriptions} />
        <Kpi label={tr("Cancelled (period)")} value={k.cancelled_subscriptions} tone={k.cancelled_subscriptions?.value ? "watch" : undefined} />
        <Kpi label={tr("MRR")} value={moneyMap(k.mrr)} format="text" hint={d.mrr} />
        <Kpi label={tr("ARR")} value={moneyMap(k.arr)} format="text" hint={d.arr} />
        <Kpi label={tr("Failed payments (period)")} value={k.failed_payments} tone={k.failed_payments?.value ? "act" : undefined} />
        <Kpi label={tr("Conversion rate")} value={k.conversion_rate} format="percent" hint={d.conversion_rate} />
        <Kpi label={tr("Churn rate")} value={k.churn_rate} format="percent" hint={d.churn_rate} />
        <Kpi label={tr("Storage used")} value={bytes(k.storage_bytes)} format="text" />
        <Kpi label={tr("Security alerts (period)")} value={k.security_alerts} tone={k.security_alerts?.value > 20 ? "watch" : undefined} hint={d.security_alerts} />
        <Kpi label={tr("Pending sales enquiries")} value={k.pending_leads} tone={k.pending_leads ? "watch" : undefined} />
      </div></div>
      <div className="grid2">
        <Section title={tr("User growth")}>{growth ? <SeriesChart points={growth.points} kind="area" valueKey="cumulative" title={tr("Accounts")} /> : <Loading />}</Section>
        <Section title={tr("Revenue (paid invoices)")}>{revenue ? <SeriesChart points={revenue.points} kind="bar" title={tr("Paid")} /> : <Loading />}</Section>
        <Section title={tr("Conversion funnel")}>{funnel ? <SeriesChart points={funnel.points} kind="bar" title={tr("Visitors / accounts")} /> : <Loading />}</Section>
        <Section title={tr("Alerts")} actions={data.alerts?.length ? <button className="btn small" onClick={() => void run(() => post("/api/me/notifications/read", { ids: null })).then(reload)}>{tr("Mark all read")}</button> : null}>
          <Records cols={[{ key: "title", label: tr("Alert") }, { key: "category", label: tr("Category") }, { key: "created_at", label: tr("When"), render: (r: any) => <When ts={r.created_at} /> }]}
            rows={data.alerts || []} empty={tr("No unread alerts.")} onRow={(r: any) => { if (r.link) location.hash = r.link; }} />
        </Section>
      </div>
      <Section title={tr("Platform status")}>
        <div className="grid3">
          <div><strong>{tr("Billing")}</strong><br /><span className="muted small">{data.billing?.ready ? tr("Stripe connected ({0} mode)", { 0: data.billing.mode }) : tr("Not configured")}</span></div>
          <div><strong>{tr("E-mail")}</strong><br /><span className="muted small">{data.email?.configured ? tr("Configured ({0})", { 0: data.email.source }) : tr("Not configured")}
            {data.email?.stats?.failed ? ` · ${tr("{0} failed", { 0: data.email.stats.failed })}` : ""}</span></div>
          <div><strong>{tr("Version")}</strong><br /><span className="muted small">{data.version} · {data.hosted ? tr("hosted") : tr("self-hosted")}</span></div>
        </div>
      </Section>
    </div>
  );
}
