// Executive overview: how the business is doing, in plain language, and the drill-down from any figure to the
// ledger lines behind it (overview → profit and loss → accounts → ledger lines → one entry). The reporting month
// travels in the query string so every level keeps its context; the server checks the viewer role at every step.
import { useState, type ReactNode } from "react";
import { get, post } from "../lib/api";
import { useApp, wsPath } from "../lib/app";
import { fmtDate, fmtDateTime, fmtFixed, fmtMoney, fmtNumber, fmtPercent } from "../lib/format";
import { Chart } from "../components/charts";
import { Empty, ErrorBox, Icon, Loading, PageHead, go, query, useAction, useLoad } from "../components/ui";
import { N, lang, tr } from "../lib/i18n";

// ---- words --------------------------------------------------------------------------------------------------------
const HEALTH: Record<string, [string, string]> = {
  strong: [N("Strong"), N("Profitable, growing and well funded. Nothing needs urgent attention.")],
  stable: [N("Stable"), N("The business is sound. A few measures deserve a closer look.")],
  watch: [N("Needs attention"), N("Several measures are off track. Management should act this month.")],
  at_risk: [N("At risk"), N("Cash or profit is under pressure. Decisions are needed now.")],
};
const MEASURE: Record<string, string> = {
  revenue: N("Revenue"), expenses: N("Expenses"), net_profit: N("Net profit"), cash: N("Cash"), ocf: N("Operating cash flow"),
  fcf: N("Free cash flow"), receivables: N("Owed by customers"), payables: N("Owed to suppliers"), cogs: N("Cost of sales"),
  gross_profit: N("Gross profit"), payroll: N("Payroll"), opex: N("Other operating expenses"), depreciation: N("Depreciation"),
  operating_profit: N("Operating profit"), other_income: N("Other income"), other_expense: N("Other expenses"),
  interest: N("Interest"), tax: N("Tax on profit"),
};
const EXPLAIN: Record<string, string> = {
  revenue: N("Sales invoiced this month, before any costs."),
  expenses: N("Everything the business spent to earn that revenue, including payroll, depreciation, interest and tax."),
  net_profit: N("What is left of revenue after every cost. The margin is over the last three months."),
  cash: N("Money in the bank at the end of the month."),
  ocf: N("Cash that day-to-day trading brought in (or used) this month, before investments and loans."),
  fcf: N("Operating cash flow less what was spent on equipment and other long-term assets."),
  receivables: N("Invoices customers have not paid yet."),
  payables: N("Supplier bills the business has not paid yet."),
};
const KPI_NAME: Record<string, string> = {
  gross_margin: N("Gross margin"), net_margin: N("Net profit margin"), days_cash: N("Days of cash on hand"),
  cash_runway: N("Cash runway"), dso: N("Days to collect a sale"), ar_overdue: N("Receivables more than 30 days overdue"),
  current_ratio: N("Current ratio"), customer_concentration: N("Revenue from top 5 customers"), plan_variance: N("Revenue against plan"),
  revenue_growth: N("Revenue growth (year on year)"), collection_rate: N("Collection rate"), opex_ratio: N("Operating expenses to revenue"),
  operating_cash_flow: N("Operating cash flow (3 months)"), debt_to_equity: N("Debt to equity"), interest_cover: N("Interest cover"),
  dscr: N("Debt service coverage (DSCR)"), payroll_ratio: N("Payroll to revenue"),
};
const KPI_WHY: Record<string, string> = {
  gross_margin: N("Share of each sale left after the direct cost of delivering it."),
  net_margin: N("Share of revenue that ends up as profit."),
  days_cash: N("How many days the bank balance would cover payments at the recent rate."),
  cash_runway: N("Months until the cash runs out at the current rate of burn."),
  dso: N("Average days between invoicing a customer and being paid."),
  ar_overdue: N("Share of unpaid invoices that are more than a month late."),
  current_ratio: N("What the business owns short term for every unit it owes short term."),
  customer_concentration: N("How much of revenue depends on the five largest customers."),
  plan_variance: N("Revenue this month against the approved plan."),
  revenue_growth: N("This month's revenue against the same month last year."),
  collection_rate: N("Share of what was owed and invoiced that customers actually paid in the last three months."),
  opex_ratio: N("Operating costs for every unit of revenue."),
  operating_cash_flow: N("Cash from trading over the last three months."),
  debt_to_equity: N("Borrowing compared with the owners' money in the business."),
  interest_cover: N("How many times operating profit covers the interest bill."),
  dscr: N("How well operating cash covers loan repayments and interest."),
  payroll_ratio: N("Payroll for every unit of revenue."),
};
const RISK_TEXT: Record<string, string> = {
  roles_unconfirmed: N("{0} of {1} accounts still use the engine's suggested role. Until finance confirms them, every figure here is unverified."),
  unbalanced: N("The ledger does not balance (difference {0}). Ratios cannot be relied on until it is fixed."),
};
const REASON: Record<string, string> = {
  loss_month: N("The month closed at a loss."), cash_thin: N("Only {0} days of cash on hand."), cash_out_month: N("Trading used cash this month."),
};
const INSIGHT: Record<string, string> = {
  revenue_yoy: N("Revenue is {0} against the same month last year ({1} vs {2})."),
  revenue_mom: N("Revenue is {0} against last month ({1} vs {2})."),
  margin_moved: N("Gross margin moved from {1} to {0}."),
  expense_up: N("{0} came to {1}, {2} above the average of the previous three months ({3})."),
  days_cash: N("The bank balance of {1} covers about {0} days of payments."),
  ar_overdue: N("{0} of the {1} owed by customers is more than 30 days overdue."),
  concentration: N("The five largest customers bring {0} of revenue."),
  plan_variance: N("Revenue of {0} is {2} against the plan of {1}."),
};
const DECISION: Record<string, string> = {
  approval: N("Approval requested"), forecast: N("Forecast waiting for sign-off"), close: N("Month-end close open"),
  budget: N("Budget runs out early"), budget_exceeded: N("Budget exceeded"),
};
const PROV: Record<string, string> = {
  actual: N("Actual"), unverified: N("Unverified"), budget: N("Budget"), forecast: N("Forecast"), estimate: N("AI estimate"),
};
const PROV_WHY: Record<string, string> = {
  actual: N("Booked in the ledger, with account roles confirmed by finance."),
  unverified: N("Booked in the ledger, but the account roles are still the engine's suggestion."),
  budget: N("From the approved plan."), forecast: N("From the approved forecast."),
  estimate: N("Written by the model from the figures on this page. Not a financial result."),
};

// ---- helpers ------------------------------------------------------------------------------------------------------
function monthName(m: string): string {
  const d = new Date(`${m}-01T00:00:00`);
  return new Intl.DateTimeFormat(lang() === "en" ? navigator.language || "en" : lang(), { month: "long", year: "numeric" }).format(d);
}
function monthShort(m: string): string {
  const d = new Date(`${m}-01T00:00:00`);
  return new Intl.DateTimeFormat(lang() === "en" ? navigator.language || "en" : lang(), { month: "short", year: "2-digit" }).format(d);
}
function upDown(change: number | null | undefined): string {
  if (change === null || change === undefined) return "";
  const pct = fmtPercent(Math.abs(change), 1);
  return change >= 0 ? tr("up {0}", { 0: pct }) : tr("down {0}", { 0: pct });
}
function withMonth(path: string, month: string | null, extra?: Record<string, string>): string {
  const q = new URLSearchParams(extra);
  if (month) q.set("month", month);
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}
function fmtKpi(v: number | null | undefined, unit: string | undefined, cur: string): string {
  if (v === null || v === undefined) return "—";
  switch (unit) {
    case "percent": return fmtPercent(v, 1);
    case "ratio": return `${fmtFixed(v, 2)}×`;
    case "days": return tr("{0} days", { 0: fmtNumber(v, 0) });
    case "months": return tr("{0} months", { 0: fmtNumber(v, 1) });
    case "money": return fmtMoney(v, cur);
    default: return fmtNumber(v, 2);
  }
}

export function Prov({ kind, small }: { kind: string; small?: boolean }): ReactNode {
  return <span className={`prov ${kind}${small ? " xs" : ""}`} title={tr(PROV_WHY[kind] || "")}>{tr(PROV[kind] || kind)}</span>;
}

function MonthPicker({ months, month, onChange }: { months: string[]; month: string; onChange: (m: string) => void }): ReactNode {
  return (
    <label className="row">
      <span className="sr">{tr("Reporting month")}</span>
      <select className="select" style={{ width: "auto" }} value={month} onChange={(e) => onChange(e.target.value)}>
        {[...months].reverse().map((m) => <option key={m} value={m}>{monthName(m)}</option>)}
      </select>
    </label>
  );
}

function Crumbs({ items }: { items: [string, string | null][] }): ReactNode {
  return (
    <nav className="crumbs" aria-label={tr("You are here")}>
      {items.map(([label, href], i) => (
        <span key={i} className="crumb">
          {i > 0 && <span className="crumb-sep" aria-hidden="true">›</span>}
          {href ? <a href={`#/${href}`}>{label}</a> : <span aria-current="page">{label}</span>}
        </span>
      ))}
    </nav>
  );
}

function Delta({ change, label, good = "up" }: { change: number | null | undefined; label: string; good?: "up" | "down" }): ReactNode {
  if (change === null || change === undefined) return null;
  const positive = good === "up" ? change >= 0 : change <= 0;
  return <span className={`delta ${Math.abs(change) < 0.0005 ? "" : positive ? "pos" : "neg"}`}>{upDown(change)} <span className="muted">{label}</span></span>;
}

function Spark({ values }: { values: (number | null)[] }): ReactNode {
  const pts = values.map((v, i) => [i, v] as [number, number | null]).filter((p): p is [number, number] => p[1] !== null);
  if (pts.length < 2) return null;
  const ys = pts.map((p) => p[1]);
  const lo = Math.min(...ys, 0), hi = Math.max(...ys, 0);
  const w = 120, h = 28;
  const x = (i: number) => (i / (values.length - 1)) * w;
  const y = (v: number) => h - ((v - lo) / (hi - lo || 1)) * (h - 4) - 2;
  const d = pts.map(([i, v], k) => `${k ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      {lo < 0 && <line x1="0" x2={w} y1={y(0)} y2={y(0)} className="zero" />}
      <path d={d} /><circle cx={x(last[0])} cy={y(last[1])} r="2.2" />
    </svg>
  );
}

// ---- page ------------------------------------------------------------------------------------------------------------
export function OverviewPage({ view, arg }: { view?: string; arg?: string }): ReactNode {
  const month = query().get("month");
  switch (view) {
    case "profit": return <Profitability month={month} />;
    case "breakdown": return <Breakdown measure={arg || "opex"} month={month} />;
    case "transactions": return <Transactions measure={arg || "opex"} account={query().get("account") || ""} month={month} from={query().get("from")} />;
    case "entry": return <Entry entry={arg || ""} month={month} measure={query().get("measure")} account={query().get("account")} from={query().get("from")} />;
    default: return <Executive month={month} />;
  }
}

function Executive({ month }: { month: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/overview${month ? `?month=${month}` : ""}`)), [ws.id, month]);
  const [ai, setAi] = useState<any>(null);
  const ask = useAction();
  if (loading && !data) return <Loading />;
  if (error) {
    return <div className="page"><PageHead title={tr("Overview")} />
      {error.includes("no_ledger") || /ledger/i.test(error)
        ? <Empty title={tr("No general ledger yet")} action={<a className="btn primary" href="#/data">{tr("Import a general ledger")}</a>}>
            {tr("Connect QuickBooks, Zoho Books, Xero or Odoo, or import a general-ledger file under Data. The overview is built from it.")}</Empty>
        : <ErrorBox error={error} />}
    </div>;
  }
  const o = data;
  const cur = o.currency;
  const m: string = o.month;
  const h = o.health;
  const prov: string = o.provenance.kind;
  const head = o.headline;
  const sm = (x: string) => monthShort(x);
  const trend = { categories: o.trend.months.map(sm), series: [
    { name: tr("Revenue"), values: head.revenue.series }, { name: tr("Expenses"), values: head.expenses.series },
    { name: tr("Net profit"), values: head.net_profit.series }] };
  const cashTrend = { categories: o.trend.months.map(sm), series: [{ name: tr("Cash"), values: head.cash.series }] };
  const kpi = (id: string) => o.kpis[id] || {};
  const kpiLine = (id: string) => {
    const k = kpi(id);
    return <li key={id} className="exec-kpi">
      <div className="exec-kpi-head"><span>{tr(KPI_NAME[id] || id)}</span>
        <b className="num">{fmtKpi(k.value, k.unit, cur)}</b></div>
      <div className="exec-kpi-foot muted xs">{k.value === null || k.value === undefined ? tr("Not available yet.") : tr(KPI_WHY[id] || "")}</div>
    </li>;
  };
  const insightText = (x: any): ReactNode => {
    const t = INSIGHT[x.kind];
    if (!t) return null;
    const money = (v: number | null) => fmtMoney(v, cur);
    switch (x.kind) {
      case "revenue_yoy": return tr(t, { 0: upDown(x.change), 1: money(x.value), 2: money(x.last_year) });
      case "revenue_mom": return tr(t, { 0: upDown(x.change), 1: money(x.value), 2: money(x.previous) });
      case "margin_moved": return tr(t, { 0: fmtPercent(x.value, 1), 1: fmtPercent(x.previous, 1) });
      case "expense_up": return tr(t, { 0: tr(MEASURE[x.measure] || x.measure), 1: money(x.value), 2: fmtPercent(x.change, 0), 3: money(x.average) });
      case "days_cash": return tr(t, { 0: fmtNumber(x.days, 0), 1: money(x.cash) });
      case "ar_overdue": return tr(t, { 0: fmtPercent(x.share, 0), 1: money(x.receivables) });
      case "concentration": return tr(t, { 0: fmtPercent(x.share, 0) });
      case "plan_variance": return tr(t, { 0: money(x.value), 1: money(x.plan), 2: upDown(x.change) });
      default: return null;
    }
  };
  const tile = (id: string, href: string, good: "up" | "down" = "up") => {
    const v = head[id];
    return (
      <a key={id} className="exec-tile" href={`#/${withMonth(href, m)}`}>
        <div className="exec-tile-label">{tr(MEASURE[id])} <Prov kind={prov} small /></div>
        <div className="exec-tile-value num">{fmtMoney(v.value, cur)}</div>
        <div className="exec-tile-deltas">
          <Delta change={v.change} label={tr("vs last month")} good={good} />
          <Delta change={v.change_year} label={tr("vs last year")} good={good} />
        </div>
        {v.plan !== undefined && v.plan !== null && <div className="xs muted"><Prov kind="budget" small /> {fmtMoney(v.plan, cur)}</div>}
        <Spark values={v.series} />
        <div className="exec-tile-why">{tr(EXPLAIN[id])}</div>
      </a>
    );
  };
  return (
    <div className="page exec">
      <PageHead title={tr("Overview")} sub={tr("{0}, from the general ledger “{1}”. Select any figure to see what is behind it.", { 0: monthName(m), 1: o.ledger.name })}
        actions={<MonthPicker months={o.months} month={m} onChange={(x) => go(withMonth("overview", x))} />} />

      {/* 1. financial health */}
      <section className={`health ${h.label || "none"}`} aria-label={tr("Financial health")}>
        <div className="health-score">
          <div className="health-label">{h.label ? tr(HEALTH[h.label][0]) : tr("Not enough data")}</div>
          {h.score !== null && <div className="health-number num">{h.score}<span className="muted">/100</span></div>}
        </div>
        <div className="health-text">
          <p className="health-lead">{h.label ? tr(HEALTH[h.label][1]) : tr("The ledger needs more history before a score means anything.")}</p>
          <p className="small muted">
            {tr("{0} of {1} measures on track", { 0: h.good, 1: h.kpis_scored })}
            {h.watch > 0 && <>, {tr("{0} to watch", { 0: h.watch })}</>}
            {h.act > 0 && <>, {tr("{0} off track", { 0: h.act })}</>}.{" "}
            {h.targets_set < h.kpis_scored && tr("Judged against reference levels until finance sets targets.")}
          </p>
          {h.reasons.length > 0 && <ul className="health-reasons">
            {h.reasons.map((r: any, i: number) => <li key={i}>
              {r.kind === "kpi_act" ? tr("{0} is off track.", { 0: tr(KPI_NAME[r.kpi] || r.kpi) }) : tr(REASON[r.kind] || r.kind, { 0: r.days })}
            </li>)}
          </ul>}
        </div>
        <div className="health-prov"><Prov kind={prov} /><div className="xs muted">{tr(PROV_WHY[prov])}</div></div>
      </section>

      {/* 2. key figures */}
      <section className="section" aria-label={tr("Key figures")}>
        <div className="exec-tiles">
          {tile("revenue", "overview/profit")}
          {tile("expenses", "overview/profit", "down")}
          {tile("net_profit", "overview/profit")}
          {tile("cash", "analysis/kpis/days_cash")}
        </div>
      </section>

      {/* 3. trend, 4. cash */}
      <div className="exec-cols">
        <section className="panel panel-pad" aria-label={tr("Performance trend")}>
          <div className="spread"><h2 className="h3">{tr("Last twelve months")}</h2><a className="small" href={`#/${withMonth("finance/statements", m)}`}>{tr("Statements")}</a></div>
          <Chart categories={trend.categories} series={trend.series} kind="bar" format="money" currency={cur} />
        </section>
        <section className="panel panel-pad" aria-label={tr("Cash and liquidity")}>
          <div className="spread"><h2 className="h3">{tr("Cash and liquidity")}</h2><a className="small" href={`#/${withMonth("finance/statements/cf", m)}`}>{tr("Cash flow")}</a></div>
          <Chart categories={cashTrend.categories} series={cashTrend.series} kind="area" format="money" currency={cur} />
          <ul className="exec-kpis">
            <li className="exec-kpi"><div className="exec-kpi-head"><span>{tr(MEASURE.ocf)}</span><b className="num">{fmtMoney(head.ocf.value, cur)}</b></div>
              <div className="exec-kpi-foot muted xs">{tr(EXPLAIN.ocf)}</div></li>
            {kpiLine("days_cash")}
            <li className="exec-kpi"><div className="exec-kpi-head"><a href={`#/${withMonth("finance/receivables", m)}`}>{tr(MEASURE.receivables)}</a><b className="num">{fmtMoney(head.receivables.value, cur)}</b></div>
              <div className="exec-kpi-foot muted xs">{tr(EXPLAIN.receivables)}</div></li>
            <li className="exec-kpi"><div className="exec-kpi-head"><a href={`#/${withMonth("finance/payables", m)}`}>{tr(MEASURE.payables)}</a><b className="num">{fmtMoney(head.payables.value, cur)}</b></div>
              <div className="exec-kpi-foot muted xs">{tr(EXPLAIN.payables)}</div></li>
            {kpiLine("dso")}
          </ul>
        </section>
      </div>

      <div className="exec-cols three">
        {/* 5. risks */}
        <section className="panel panel-pad" aria-label={tr("Risks")}>
          <div className="spread"><h2 className="h3">{tr("Risks that need attention")}</h2><a className="small" href={`#/${withMonth("finance/anomalies", m)}`}>{tr("Anomalies")}</a></div>
          {o.risks.length === 0 ? <p className="muted small">{tr("Nothing is off track this month.")}</p> : (
            <ul className="exec-list">
              {o.risks.map((r: any, i: number) => (
                <li key={i} className={`exec-risk ${r.level}`}>
                  <a href={`#/${withMonth(r.link, m)}`}>
                    {r.kind === "kpi" ? <>
                      <b>{tr(KPI_NAME[r.kpi] || r.kpi)}</b> <span className="num">{fmtKpi(r.value, r.unit, cur)}</span>
                      <div className="xs muted">{tr(KPI_WHY[r.kpi] || "")} {r.target !== null && r.target !== undefined
                        ? tr("Target {0}.", { 0: fmtKpi(r.target, r.unit, cur) }) : tr("Off the reference level.")}</div>
                    </> : r.kind === "roles_unconfirmed" ? <>
                      <b>{tr("Account roles not confirmed")}</b>
                      <div className="xs muted">{tr(RISK_TEXT.roles_unconfirmed, { 0: r.accounts - r.confirmed, 1: r.accounts })}</div>
                    </> : <>
                      <b>{tr("Ledger out of balance")}</b>
                      <div className="xs muted">{tr(RISK_TEXT.unbalanced, { 0: fmtMoney(r.difference, cur) })}</div>
                    </>}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* 6. insights */}
        <section className="panel panel-pad" aria-label={tr("Insights")}>
          <div className="spread"><h2 className="h3">{tr("What the numbers say")} <Prov kind={prov} small /></h2>
            <button className="btn small" disabled={ask.busy} onClick={() => ask.run(async () => {
              setAi(await post(wsPath(ws, `/overview/insights?month=${m}&lang=${lang()}`), {}));
            })}>{ask.busy ? tr("Thinking…") : tr("Ask the model")}</button></div>
          <ul className="exec-list">
            {o.insights.map((x: any, i: number) => (
              <li key={i}>{x.link ? <a href={`#/${withMonth(x.link, m)}`}>{insightText(x)}</a> : insightText(x)}</li>
            ))}
            {o.insights.length === 0 && <li className="muted small">{tr("Nothing stands out this month.")}</li>}
          </ul>
          {ai && <div className="ai-box">
            <div className="row xs"><Prov kind="estimate" /><span className="muted">{tr("Written by {0} from the figures on this page.", { 0: ai.model })}</span></div>
            <ul className="exec-list">{ai.items.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul>
          </div>}
        </section>

        {/* 7. decisions */}
        <section className="panel panel-pad" aria-label={tr("Decisions")}>
          <h2 className="h3">{tr("Waiting for a decision")}</h2>
          {o.decisions.length === 0 ? <p className="muted small">{tr("Nothing is waiting for you.")}</p> : (
            <ul className="exec-list">
              {o.decisions.map((d: any, i: number) => (
                <li key={i}><a href={`#/${d.link}`}>
                  <span className="tag amber">{tr(DECISION[d.kind] || d.kind)}</span>{" "}
                  {d.kind === "close" ? tr("{0}: {1} open items", { 0: monthName(d.month), 1: d.open_items ?? "—" })
                    : d.kind === "budget" ? tr("{0}: runs out {1}, planned to {2}", { 0: d.title, 1: fmtDate(d.runout), 2: fmtDate(d.end) })
                    : d.kind === "budget_exceeded" ? tr("{0}: exceeded on {1}", { 0: d.title, 1: fmtDate(d.exceeded_on) })
                    : d.kind === "forecast" ? `${d.title} · ${d.scenario} v${d.version}` : d.title}
                </a></li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

// ---- level 2: profit and loss ------------------------------------------------------------------------------------------
function Profitability({ month }: { month: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/overview/profitability${month ? `?month=${month}` : ""}`)), [ws.id, month]);
  if (loading && !data) return <Loading />;
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  const p = data;
  const cur = p.currency;
  const m: string = p.month;
  const hasPlan = p.lines.some((ln: any) => ln.plan !== null);
  return (
    <div className="page">
      <Crumbs items={[[tr("Overview"), withMonth("overview", m)], [tr("Profitability"), null]]} />
      <PageHead title={tr("Profitability")} sub={tr("Revenue to net profit for {0}. Select a cost line to see the accounts behind it.", { 0: monthName(m) })}
        actions={<MonthPicker months={p.months} month={m} onChange={(x) => go(withMonth("overview/profit", x))} />} />
      <div className="row xs" style={{ marginBottom: 12 }}><Prov kind={p.provenance.kind} /><span className="muted">{tr(PROV_WHY[p.provenance.kind])}</span></div>
      <div className="table-wrap" style={{ maxHeight: "none" }}>
        <table className="ledger pl">
          <thead><tr>
            <th>{tr("Line")}</th><th className="num">{monthShort(m)}</th>
            <th className="num">{p.previous ? monthShort(p.previous) : tr("Last month")}</th>
            <th className="num">{p.last_year ? monthShort(p.last_year) : tr("Last year")}</th>
            <th className="num">{tr("Year to date")}</th>
            {hasPlan && <th className="num">{tr("Plan")} <Prov kind="budget" small /></th>}
            <th className="num">{tr("% of revenue")}</th>
          </tr></thead>
          <tbody>
            {p.lines.map((ln: any) => {
              const rev = p.lines[0].value;
              const share = ln.kind === "subtotal" || ln.kind === "total" ? p.margins[ln.measure] : rev ? ln.value / rev : null;
              const row = <>
                <td>{ln.drillable ? <a href={`#/${withMonth(`overview/breakdown/${ln.measure}`, m)}`}>{tr(MEASURE[ln.measure])}</a> : tr(MEASURE[ln.measure])}
                  {ln.drillable && ln.accounts > 0 && <span className="xs muted"> · {ln.accounts === 1 ? tr("1 account") : tr("{0} accounts", { 0: ln.accounts })}</span>}</td>
                <td className="num">{fmtMoney(ln.value, cur)}</td>
                <td className="num muted">{fmtMoney(ln.previous, cur)}</td>
                <td className="num muted">{fmtMoney(ln.last_year, cur)}</td>
                <td className="num">{fmtMoney(ln.ytd, cur)}</td>
                {hasPlan && <td className="num muted">{fmtMoney(ln.plan, cur)}</td>}
                <td className="num muted">{share === null || share === undefined ? "—" : fmtPercent(share, 1)}</td>
              </>;
              return <tr key={ln.measure} className={`pl-${ln.kind}`}>{row}</tr>;
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small" style={{ marginTop: 12 }}>{tr("Costs are shown as positive amounts. Subtotals cannot be opened; the lines that make them up can.")}</p>
    </div>
  );
}

// ---- level 3: accounts behind a line ----------------------------------------------------------------------------------
function Breakdown({ measure, month }: { measure: string; month: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/overview/breakdown/${measure}${month ? `?month=${month}` : ""}`)), [ws.id, measure, month]);
  if (loading && !data) return <Loading />;
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  const b = data;
  const cur = b.currency;
  const m: string = b.month;
  const name = tr(MEASURE[measure] || measure);
  return (
    <div className="page">
      <Crumbs items={[[tr("Overview"), withMonth("overview", m)], [tr("Profitability"), withMonth("overview/profit", m)], [name, null]]} />
      <PageHead title={name} sub={tr("{0} in {1}, by account. Select an account to see its ledger lines.", { 0: fmtMoney(b.total, cur), 1: monthName(m) })}
        actions={<MonthPicker months={b.months} month={m} onChange={(x) => go(withMonth(`overview/breakdown/${measure}`, x))} />} />
      <div className="row xs" style={{ marginBottom: 12 }}><Prov kind={b.provenance.kind} /><span className="muted">{tr(PROV_WHY[b.provenance.kind])}</span></div>
      {b.items.length === 0 ? <Empty title={tr("Nothing posted to this line in {0}.", { 0: monthName(m) })} /> : (
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table className="ledger">
            <thead><tr><th>{tr("Account")}</th><th className="num">{monthShort(m)}</th><th className="num">{tr("Last month")}</th>
              <th className="num">{tr("Change")}</th><th className="num">{tr("Share")}</th><th className="num">{tr("Lines")}</th></tr></thead>
            <tbody>
              {b.items.map((it: any) => (
                <tr key={it.key} className="clickable" onClick={() => go(withMonth(`overview/transactions/${measure}`, m, { account: it.key }))}>
                  <td><a href={`#/${withMonth(`overview/transactions/${measure}`, m, { account: it.key })}`} onClick={(e) => e.stopPropagation()}>{it.account}</a></td>
                  <td className="num">{fmtMoney(it.value, cur)}</td>
                  <td className="num muted">{fmtMoney(it.previous, cur)}</td>
                  <td className="num"><Delta change={it.change} label="" good="down" /></td>
                  <td className="num muted">{it.share === null ? "—" : fmtPercent(it.share, 1)}</td>
                  <td className="num muted">{it.lines}</td>
                </tr>
              ))}
            </tbody>
            <tfoot><tr><td>{tr("Total")}</td><td className="num">{fmtMoney(b.total, cur)}</td><td className="num muted">{fmtMoney(b.previous_total, cur)}</td><td colSpan={3} /></tr></tfoot>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- level 4: ledger lines of one account ------------------------------------------------------------------------------
function Transactions({ measure, account, month, from }: { measure: string; account: string; month: string | null; from?: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/overview/transactions/${measure}?account=${encodeURIComponent(account)}${month ? `&month=${month}` : ""}`)),
    [ws.id, measure, account, month]);
  if (loading && !data) return <Loading />;
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  const t = data;
  const cur = t.currency;
  const m: string = t.month;
  const line = tr(MEASURE[measure] || measure);
  return (
    <div className="page">
      <Crumbs items={from === "ledger"
        ? [[tr("General ledger"), withMonth("ledger/entries", m)], [tr("Accounts"), withMonth("ledger/accounts", m)], [t.account, null]]
        : [[tr("Overview"), withMonth("overview", m)], [tr("Profitability"), withMonth("overview/profit", m)], [line, withMonth(`overview/breakdown/${measure}`, m)], [t.account, null]]} />
      <PageHead title={t.account} sub={t.count === 1 ? tr("One ledger line in {0}, {1} in total. Select it to open its journal entry.", { 0: monthName(m), 1: fmtMoney(t.total, cur) })
        : tr("{0} ledger lines in {1}, {2} in total. Select a line to open its journal entry.", { 0: t.count, 1: monthName(m), 2: fmtMoney(t.total, cur) })}
        actions={<MonthPicker months={t.months} month={m} onChange={(x) => go(withMonth(`overview/transactions/${measure}`, x, { account }))} />} />
      <div className="row xs" style={{ marginBottom: 12 }}><Prov kind={t.provenance.kind} /><span className="muted">{tr(PROV_WHY[t.provenance.kind])}</span></div>
      {t.rows.length === 0 ? <Empty title={tr("No ledger lines in {0}.", { 0: monthName(m) })} /> : (
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table className="ledger">
            <thead><tr><th>{tr("Date")}</th><th>{tr("Entry")}</th><th>{tr("Description")}</th><th>{tr("Party")}</th>
              <th className="num">{tr("Debit")}</th><th className="num">{tr("Credit")}</th><th className="num">{tr("Amount")}</th></tr></thead>
            <tbody>
              {t.rows.map((r: any) => (
                <tr key={`${r.entry}-${r.row}`} className="clickable" onClick={() => go(withMonth(`overview/entry/${encodeURIComponent(r.entry)}`, m, { measure, account, ...(from ? { from } : {}) }))}>
                  <td className="num" style={{ textAlign: "start" }}>{fmtDate(r.date)}</td>
                  <td><a href={`#/${withMonth(`overview/entry/${encodeURIComponent(r.entry)}`, m, { measure, account, ...(from ? { from } : {}) })}`} onClick={(e) => e.stopPropagation()}>{r.entry}</a>
                    {r.type && <span className="xs muted"> · {r.type}</span>}</td>
                  <td>{r.description || <span className="muted">—</span>}</td>
                  <td>{r.party || <span className="muted">—</span>}</td>
                  <td className="num muted">{r.debit ? fmtMoney(r.debit, cur) : ""}</td>
                  <td className="num muted">{r.credit ? fmtMoney(r.credit, cur) : ""}</td>
                  <td className="num">{fmtMoney(r.amount, cur)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot><tr><td colSpan={6}>{tr("Total")}</td><td className="num">{fmtMoney(t.total, cur)}</td></tr></tfoot>
          </table>
        </div>
      )}
      {t.count > t.rows.length && <p className="muted small" style={{ marginTop: 10 }}>{tr("Showing the first {0} of {1} lines.", { 0: t.rows.length, 1: t.count })}</p>}
    </div>
  );
}

// ---- level 5: one journal entry ---------------------------------------------------------------------------------------
const ROLE_NAMES: Record<string, string> = {
  cash: N("Bank and cash"), receivables: N("Receivables"), inventory: N("Inventory"), other_current_assets: N("Other current assets"),
  fixed_assets: N("Fixed assets"), other_non_current_assets: N("Other non-current assets"), payables: N("Payables"),
  other_current_liabilities: N("Other current liabilities"), short_term_debt: N("Short-term debt"), long_term_debt: N("Long-term debt"),
  other_non_current_liabilities: N("Other non-current liabilities"), equity: N("Equity"), revenue: N("Revenue"),
  other_income: N("Other income"), cogs: N("Cost of sales"), payroll: N("Payroll"), opex: N("Other operating expenses"),
  depreciation: N("Depreciation and amortisation"), interest: N("Interest"), tax: N("Tax on profit"), other_expense: N("Other expenses"),
};
const SOURCE_KIND: Record<string, string> = {
  excel: N("Excel import"), csv: N("CSV import"), quickbooks: "QuickBooks Online", zoho_books: "Zoho Books", xero: "Xero", odoo: "Odoo",
  open_accounting: N("Open Accounting Connector"), gmail: N("Gmail attachment"), google_drive: "Google Drive",
};

function Entry({ entry, month, measure, account, from }: { entry: string; month: string | null; measure: string | null; account: string | null; from?: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/overview/entries/${encodeURIComponent(entry)}${month ? `?month=${month}` : ""}`)), [ws.id, entry, month]);
  if (loading && !data) return <Loading />;
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  const e = data;
  const cur = e.currency;
  const m = month || e.date.slice(0, 7);
  const debit = e.lines.reduce((s: number, l: any) => s + (l.debit || 0), 0);
  const credit = e.lines.reduce((s: number, l: any) => s + (l.credit || 0), 0);
  return (
    <div className="page">
      <Crumbs items={from === "ledger" ? [[tr("General ledger"), withMonth("ledger/entries", m)],
        ...(measure && account ? [[e.lines.find((l: any) => account === `${l.account_code || ""}|${l.account}`)?.account || tr("Accounts"),
          withMonth(`overview/transactions/${measure}`, m, { account, from: "ledger" })] as [string, string | null]] : []), [e.entry, null]]
        : [[tr("Overview"), withMonth("overview", m)], [tr("Profitability"), withMonth("overview/profit", m)],
        ...(measure ? [[tr(MEASURE[measure] || measure), withMonth(`overview/breakdown/${measure}`, m)] as [string, string | null]] : []),
        ...(measure && account ? [[e.lines.find((l: any) => account === `${l.account_code || ""}|${l.account}`)?.account || tr("Ledger lines"),
          withMonth(`overview/transactions/${measure}`, m, { account })] as [string, string | null]] : []),
        [e.entry, null]]} />
      <PageHead title={tr("Journal entry {0}", { 0: e.entry })} sub={<>{fmtDate(e.date)}{e.type ? ` · ${e.type}` : ""} · {e.balanced ? tr("balanced") : tr("does not balance")}</>}
        actions={<button className="btn" onClick={() => history.back()}>{tr("Back")}</button>} />
      <div className="row xs" style={{ marginBottom: 12 }}><Prov kind={e.provenance.kind} /><span className="muted">{tr(PROV_WHY[e.provenance.kind])}</span></div>
      <div className="entry-layout">
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table className="ledger">
            <thead><tr><th>{tr("Account")}</th><th>{tr("Role")}</th><th>{tr("Description")}</th><th>{tr("Party")}</th>
              <th className="num">{tr("Debit")}</th><th className="num">{tr("Credit")}</th></tr></thead>
            <tbody>
              {e.lines.map((l: any) => (
                <tr key={l.row}>
                  <td>{[l.account_code, l.account].filter(Boolean).join(" ")}</td>
                  <td className="muted">{tr(ROLE_NAMES[l.role] || l.role)}</td>
                  <td>{l.description || <span className="muted">—</span>}</td>
                  <td>{l.party || <span className="muted">—</span>}</td>
                  <td className="num">{l.debit ? fmtMoney(l.debit, cur) : ""}</td>
                  <td className="num">{l.credit ? fmtMoney(l.credit, cur) : ""}</td>
                </tr>
              ))}
            </tbody>
            <tfoot><tr><td colSpan={4}>{tr("Total")}</td><td className="num">{fmtMoney(debit, cur)}</td><td className="num">{fmtMoney(credit, cur)}</td></tr></tfoot>
          </table>
        </div>
        <aside className="entry-side">
          <section className="panel panel-pad">
            <h3 className="h3">{tr("Source")}</h3>
            <p className="small"><a href={`#/data/${e.source.dataset_id}`}>{e.source.name}</a></p>
            <p className="small muted">{SOURCE_KIND[e.source.kind] ? tr(SOURCE_KIND[e.source.kind]) : e.source.kind || ""}{e.source.refreshed_at ? ` · ${tr("refreshed {0}", { 0: fmtDateTime(e.source.refreshed_at) })}` : ""}
              {e.source.rows ? ` · ${tr("{0} lines", { 0: fmtNumber(e.source.rows, 0) })}` : ""}</p>
          </section>
          <section className="panel panel-pad">
            <h3 className="h3">{tr("Supporting documents")}</h3>
            {e.documents.length === 0 ? <p className="small muted">{tr("No document is attached to this entry. Attach invoices and receipts under Documents to link them.")}</p>
              : <ul className="plain">{e.documents.map((d: any) => <li key={d.id}><a href={`#/documents/${d.id}`}>{d.title}</a></li>)}</ul>}
          </section>
          <section className="panel panel-pad">
            <h3 className="h3">{tr("Audit history")}</h3>
            {e.audit.length === 0 ? <p className="small muted">{tr("No audit entries for this ledger yet.")}</p> : (
              <ul className="plain small">
                {e.audit.map((a: any, i: number) => <li key={i}><b>{a.action}</b>{a.by ? ` · ${a.by}` : ""}<br /><span className="muted xs">{fmtDateTime(a.at)}</span></li>)}
              </ul>
            )}
          </section>
        </aside>
      </div>
      <p className="xs muted" style={{ marginTop: 12 }}><Icon name="link" size={12} /> {tr("Nothing here is posted to your accounting system; OfinTech Desk reads the ledger only.")}</p>
    </div>
  );
}
