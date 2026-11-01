import { useState, type ReactNode } from "react";
import { get, patch, post } from "../lib/api";
import { useApp } from "../lib/app";
import { ErrorBox, Field, Loading, PageHead, useAction, useLoad } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { tr } from "../lib/i18n";
import { goOwner } from "./OwnerApp";
import { Kpi, Pager, Range, RangePicker, Records, Section, SeriesChart, Tag, When, rangeQuery, toneFor } from "./shared";

const CHARTS: [string, string, "bar" | "line" | "area" | "stacked_bar", string?][] = [
  ["traffic", "Website traffic (page views)", "area"], ["registrations", "New registrations", "bar"], ["active_users", "Active users", "line"],
  ["free_to_paid", "Free-to-paid conversions", "bar"], ["churn", "Subscription churn", "bar"], ["revenue", "Revenue (paid invoices)", "stacked_bar"],
  ["feature_timeline", "Feature usage over time", "stacked_bar"], ["workspaces_created", "Workspaces created", "bar"], ["ai_usage", "AI tokens used", "bar"],
  ["email", "E-mail delivery (sent)", "bar", "sent"], ["security", "Security refusals", "stacked_bar"], ["errors", "Error rate (failed actions)", "bar"],
];
const BREAKDOWNS: [string, string][] = [["sources", "Traffic sources"], ["referrers", "Referrers"], ["landing_pages", "Landing pages"], ["devices", "Devices"],
  ["browsers", "Browsers"], ["countries", "Countries"], ["languages", "Languages"], ["features", "Most-used product features"], ["plan_segments", "Plan segmentation"]];

export function AnalyticsPage(): ReactNode {
  const [range, setRange] = useState<Range>({ period: "30d" });
  const [tab, setTab] = useState<"website" | "platform" | "retention" | "definitions">("website");
  const rq = rangeQuery(range);
  const { data: k, error } = useLoad(() => get(`/api/owner/analytics/kpis?${rq}`), [rq]);
  const { data: defs } = useLoad(() => get("/api/owner/analytics/definitions"), []);
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  return (
    <div className="page">
      <PageHead title={tr("Analytics")} sub={tr("Website and product analytics from collected events only: no IP addresses, names, prompts or financial data. Each metric's definition is listed under Definitions.")}
        actions={<RangePicker value={range} onChange={setRange} />} />
      {k && !k.analytics_enabled && <div className="notice warn" style={{ marginBottom: 16 }}>{tr("Analytics collection is switched off (Integrations → Analytics). Figures from the engine's own records are still shown.")}</div>}
      <div className="seg" role="tablist" style={{ marginBottom: 16 }}>
        {(["website", "platform", "retention", "definitions"] as const).map((t) => <button key={t} role="tab" aria-pressed={tab === t} onClick={() => setTab(t)}>
          {t === "website" ? tr("Website") : t === "platform" ? tr("Platform") : t === "retention" ? tr("Cohorts") : tr("Definitions")}</button>)}
        <a className="btn small" style={{ marginInlineStart: 12 }} href={`/api/owner/export/kpis.csv?${rq}`} download>{tr("Export KPIs (CSV)")}</a>
      </div>
      {!k ? <Loading /> : tab === "website" ? <>
        <div className="kpi-grid" style={{ marginBottom: 16 }}>
          <Kpi label={tr("Page views")} value={k.page_views} hint={defs?.metrics.page_views} />
          <Kpi label={tr("Unique visitors")} value={k.unique_visitors} hint={defs?.metrics.unique_visitors} />
          <Kpi label={tr("New registrations")} value={k.new_users} />
          <Kpi label={tr("Pending sales enquiries")} value={k.pending_leads} />
        </div>
        <div className="grid2">
          {CHARTS.slice(0, 2).map(([m, label, kind, vk]) => <ChartPanel key={m} metric={m} label={label} kind={kind} valueKey={vk} rq={rq} />)}
          <Section title={tr("Conversion funnel")}><Funnel rq={rq} /></Section>
          {BREAKDOWNS.slice(0, 7).map(([m, label]) => <Breakdown key={m} metric={m} label={label} rq={rq} />)}
        </div>
      </> : tab === "platform" ? <>
        <div className="kpi-grid" style={{ marginBottom: 16 }}>
          <Kpi label={tr("DAU")} value={k.dau} /><Kpi label={tr("WAU")} value={k.wau} /><Kpi label={tr("MAU")} value={k.mau} />
          <Kpi label={tr("Active users (period)")} value={k.active_users} /><Kpi label={tr("Conversion rate")} value={k.conversion_rate} format="percent" />
          <Kpi label={tr("Churn rate")} value={k.churn_rate} format="percent" /><Kpi label={tr("Storage used")} value={k.storage_bytes} format="bytes" />
          <Kpi label={tr("Security alerts (period)")} value={k.security_alerts} />
        </div>
        <div className="grid2">
          {CHARTS.slice(2).map(([m, label, kind, vk]) => <ChartPanel key={m} metric={m} label={label} kind={kind} valueKey={vk} rq={rq} />)}
          {BREAKDOWNS.slice(7).map(([m, label]) => <Breakdown key={m} metric={m} label={label} rq={rq} />)}
          <Breakdown metric="storage" label="Storage by workspace" rq={rq} />
        </div>
      </> : tab === "retention" ? <Retention rq={rq} /> : <Section title={tr("Metric definitions and data sources")}>
        <Records rows={Object.entries(defs?.metrics || {}).map(([k2, v]) => ({ id: k2, metric: k2, definition: v }))} cols={[{ key: "metric", label: tr("Metric") }, { key: "definition", label: tr("Definition") }]} />
        <h4>{tr("Product events (from audit actions; names only)")}</h4>
        <Records rows={Object.entries(defs?.events || {}).map(([a, e]) => ({ id: a, action: a, event: e }))} cols={[{ key: "action", label: tr("Audit action") }, { key: "event", label: tr("Event") }]} />
      </Section>}
    </div>
  );
}

function ChartPanel({ metric, label, kind, valueKey, rq }: { metric: string; label: string; kind: "bar" | "line" | "area" | "stacked_bar"; valueKey?: string; rq: string }): ReactNode {
  const { data } = useLoad(() => get(`/api/owner/analytics/series/${metric}?${rq}`), [metric, rq]);
  return <Section title={tr(label)} actions={<a className="small" href={`/api/owner/export/${metric}.csv?${rq}`} download>CSV</a>}>
    {!data ? <Loading /> : <SeriesChart points={data.points} kind={kind} valueKey={valueKey || "value"} title={tr(label)} />}</Section>;
}

function Breakdown({ metric, label, rq }: { metric: string; label: string; rq: string }): ReactNode {
  const { data } = useLoad(() => get(`/api/owner/analytics/series/${metric}?${rq}`), [metric, rq]);
  return <Section title={tr(label)}>{!data ? <Loading /> : <Records rows={data.points.map((p: any, i: number) => ({ id: i, ...p }))}
    cols={[{ key: "name", label: tr("Name"), render: (p: any) => String(p.name ?? "—") }, { key: "value", label: tr("Count"), num: true, render: (p: any) => Intl.NumberFormat().format(p.value) },
      ...(data.points[0]?.workspaces !== undefined ? [{ key: "workspaces", label: tr("Workspaces"), num: true }] : [])]} empty={tr("No data for this selection.")} />}</Section>;
}

function Funnel({ rq }: { rq: string }): ReactNode {
  const { data } = useLoad(() => get(`/api/owner/analytics/series/funnel?${rq}`), [rq]);
  if (!data) return <Loading />;
  const top = Math.max(1, ...data.points.map((p: any) => p.value));
  return <div className="stack">{data.points.map((p: any) => <div key={p.name}><div className="row" style={{ justifyContent: "space-between" }}><span className="small">{tr(p.name.replace(/_/g, " "))}</span><strong className="small">{p.value}</strong></div>
    <div className="split"><div className="split-a" style={{ width: `${(100 * p.value) / top}%` }} /></div></div>)}</div>;
}

function Retention({ rq }: { rq: string }): ReactNode {
  const { data } = useLoad(() => get(`/api/owner/analytics/series/retention?${rq}`), [rq]);
  return <Section title={tr("Weekly sign-up cohorts: share still active after 1, 2, 4 and 8 weeks")}>
    {!data ? <Loading /> : <Records rows={data.points.map((p: any) => ({ id: p.cohort, ...p }))} cols={[{ key: "cohort", label: tr("Cohort") }, { key: "size", label: tr("Accounts"), num: true },
      ...[1, 2, 4, 8].map((w) => ({ key: `w${w}`, label: tr("Week {0}", { 0: w }), num: true, render: (p: any) => p[`w${w}`] === null ? "—" : `${p[`w${w}`]}%` }))]} empty={tr("No cohorts in this period.")} />}</Section>;
}

export function LeadsPage({ id }: { id?: string }): ReactNode {
  return id ? <LeadDetail id={id} /> : <LeadInbox />;
}

function LeadInbox(): ReactNode {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ status, q, limit: "50", offset: String(offset) });
  const { data, error } = useLoad(() => get(`/api/owner/leads?${params}`), [params.toString()]);
  return (
    <div className="page">
      <PageHead title={tr("Sales Leads")} sub={tr("Enterprise enquiries from the Contact Sales form.")} actions={<a className="btn small" href="/api/owner/export/leads.csv" download>{tr("Export CSV")}</a>} />
      {data && <div className="kpi-grid" style={{ marginBottom: 16 }}>
        {Object.entries(data.stats.by_status).map(([s, n]) => <Kpi key={s} label={tr(s)} value={n as number} />)}
        <Kpi label={tr("Won rate")} value={data.stats.won_rate} format="percent" />
      </div>}
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <input className="input" style={{ maxWidth: 280 }} placeholder={tr("Search name, e-mail or company")} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
        <select className="select" style={{ width: 170 }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label={tr("Status")}>
          <option value="">{tr("Any status")}</option>{(data?.statuses || []).map((s: string) => <option key={s} value={s}>{tr(s)}</option>)}</select>
      </div>
      <ErrorBox error={error} />
      {!data ? <Loading /> : <>
        <Records rows={data.items} onRow={(l: any) => goOwner(`leads/${l.id}`)} cols={[
          { key: "company", label: tr("Company"), render: (l: any) => <><strong>{l.company}</strong><br /><span className="small muted">{l.name} · {l.email}</span></> },
          { key: "country", label: tr("Country") }, { key: "company_size", label: tr("Size") },
          { key: "users_estimate", label: tr("Users"), num: true }, { key: "workspaces_needed", label: tr("Workspaces"), num: true }, { key: "deployment", label: tr("Deployment") },
          { key: "status", label: tr("Status"), render: (l: any) => <Tag text={tr(l.status)} tone={toneFor(l.status)} /> },
          { key: "follow_up_at", label: tr("Follow-up"), render: (l: any) => l.follow_up_at ? fmtDateTime(l.follow_up_at) : "—" },
          { key: "created_at", label: tr("Received"), render: (l: any) => <When ts={l.created_at} /> }]} empty={tr("No enquiries yet.")} />
        <Pager total={data.total} limit={50} offset={offset} onChange={setOffset} />
      </>}
    </div>
  );
}

function LeadDetail({ id }: { id: string }): ReactNode {
  const { session } = useApp();
  const perms = new Set(session.user?.permissions || []);
  const { data: l, error, reload } = useLoad(() => get(`/api/owner/leads/${id}`), [id]);
  const { data: list } = useLoad(() => get("/api/owner/leads?limit=1"), []);
  const [note, setNote] = useState("");
  const [follow, setFollow] = useState("");
  const { busy, run } = useAction();
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!l) return <div className="page"><Loading /></div>;
  const manage = perms.has("sales.manage");
  const act = (fn: () => Promise<unknown>, ok?: string) => void run(fn, ok).then(reload);
  return (
    <div className="page">
      <PageHead title={l.company} sub={<>{l.name} · <a href={`mailto:${l.email}`}>{l.email}</a> · {tr("received")} {fmtDateTime(l.created_at)}</>} actions={<a className="btn small" href="#/owner/leads">{tr("All leads")}</a>} />
      <div className="grid2">
        <Section title={tr("Enquiry")}>
          <dl className="small">
            <dt>{tr("Country")}</dt><dd>{l.country || "—"}</dd><dt>{tr("Company size")}</dt><dd>{l.company_size || "—"}</dd>
            <dt>{tr("Estimated users")}</dt><dd>{l.users_estimate ?? "—"}</dd><dt>{tr("Workspaces needed")}</dt><dd>{l.workspaces_needed ?? "—"}</dd>
            <dt>{tr("Storage estimate")}</dt><dd>{l.storage_estimate || "—"}</dd><dt>{tr("Deployment preference")}</dt><dd>{l.deployment || "—"}</dd>
            <dt>{tr("Interested features")}</dt><dd>{l.features.join(", ") || "—"}</dd><dt>{tr("Source")}</dt><dd>{l.source}</dd>
          </dl>
          {l.message && <blockquote className="small" style={{ whiteSpace: "pre-wrap" }}>{l.message}</blockquote>}
        </Section>
        <Section title={tr("Work the lead")}>
          <div className="grid2">
            <Field label={tr("Status")}><select className="select" disabled={!manage} value={l.status} onChange={(e) => act(() => patch(`/api/owner/leads/${id}`, { status: e.target.value }), tr("Status updated"))}>
              {(list?.statuses || [l.status]).map((s: string) => <option key={s} value={s}>{tr(s)}</option>)}</select></Field>
            <Field label={tr("Assigned to")}><select className="select" disabled={!manage} value={l.assigned_to || ""} onChange={(e) => act(() => patch(`/api/owner/leads/${id}`, e.target.value ? { assigned_to: e.target.value } : { clear_assignee: true }), tr("Assignment updated"))}>
              <option value="">—</option>{(list?.admins || []).map((a: any) => <option key={a.user_id} value={a.user_id}>{a.name} ({a.role})</option>)}</select></Field>
          </div>
          <Field label={tr("Follow-up reminder")}><div className="row"><input className="input" type="datetime-local" value={follow} onChange={(e) => setFollow(e.target.value)} disabled={!manage} />
            <button className="btn small" disabled={!manage || !follow || busy} onClick={() => act(() => patch(`/api/owner/leads/${id}`, { follow_up_at: Date.parse(follow) / 1000 }), tr("Reminder set"))}>{tr("Set")}</button>
            {l.follow_up_at && <span className="small muted">{tr("due")} {fmtDateTime(l.follow_up_at)}</span>}</div></Field>
          {manage && <Field label={tr("Internal note")}><textarea className="textarea" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
            <button className="btn small" disabled={!note.trim() || busy} onClick={() => { act(() => post(`/api/owner/leads/${id}/notes`, { body: note }), tr("Note added")); setNote(""); }}>{tr("Add note")}</button></Field>}
          <Records rows={l.notes} cols={[{ key: "body", label: tr("Note") }, { key: "author", label: tr("By") }, { key: "created_at", label: tr("When"), render: (n: any) => fmtDateTime(n.created_at) }]} empty={tr("No notes yet.")} />
        </Section>
      </div>
    </div>
  );
}
