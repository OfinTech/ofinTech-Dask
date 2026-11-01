// The CFO's pages: monthly statements (profit and loss, balance sheet, cash flow), budget against actual by line,
// receivables and payables by party with ageing, and anomalies in what was posted. Every table opens into the same
// drill-down as the executive overview, and the reporting month travels in the address.
import { type ReactNode } from "react";
import { get } from "../lib/api";
import { useApp, wsPath } from "../lib/app";
import { fmtDate, fmtMoney, fmtNumber, fmtPercent } from "../lib/format";
import { Empty, ErrorBox, Loading, PageHead, go, query, useLoad } from "../components/ui";
import { N, lang, tr } from "../lib/i18n";
import { Prov } from "./Overview";

const TABS: [string, string][] = [["statements", N("Statements")], ["budget", N("Budget vs actual")], ["receivables", N("Receivables")],
  ["payables", N("Payables")], ["anomalies", N("Anomalies")]];
const MEASURE: Record<string, string> = {
  revenue: N("Revenue"), cogs: N("Cost of sales"), gross_profit: N("Gross profit"), payroll: N("Payroll"), opex: N("Other operating expenses"),
  depreciation: N("Depreciation"), operating_profit: N("Operating profit"), other_income: N("Other income"), other_expense: N("Other expenses"),
  interest: N("Interest"), tax: N("Tax on profit"), net_profit: N("Net profit"),
  cash: N("Bank and cash"), receivables: N("Receivables"), inventory: N("Inventory"), other_current_assets: N("Other current assets"),
  fixed_assets: N("Fixed assets"), other_non_current_assets: N("Other non-current assets"), payables: N("Payables"),
  other_current_liabilities: N("Other current liabilities"), short_term_debt: N("Short-term debt"), long_term_debt: N("Long-term debt"),
  other_non_current_liabilities: N("Other non-current liabilities"),
};
const SECTION: Record<string, string> = {
  current_assets: N("Current assets"), non_current_assets: N("Non-current assets"), current_liabilities: N("Current liabilities"),
  non_current_liabilities: N("Non-current liabilities"),
};
const ANOMALY: Record<string, string> = {
  unusual_amount: N("Unusual amount"), missing_posting: N("Nothing posted"), new_account: N("New account"), possible_duplicate: N("Possible duplicate"),
};
const PROV_WHY: Record<string, string> = {
  actual: N("Booked in the ledger, with account roles confirmed by finance."),
  unverified: N("Booked in the ledger, but the account roles are still the engine's suggestion."),
};

function monthName(m: string): string {
  return new Intl.DateTimeFormat(lang() === "en" ? navigator.language || "en" : lang(), { month: "long", year: "numeric" }).format(new Date(`${m}-01T00:00:00`));
}
function monthShort(m: string): string {
  return new Intl.DateTimeFormat(lang() === "en" ? navigator.language || "en" : lang(), { month: "short", year: "2-digit" }).format(new Date(`${m}-01T00:00:00`));
}
function withMonth(path: string, month: string | null, extra?: Record<string, string>): string {
  const q = new URLSearchParams(extra);
  if (month) q.set("month", month);
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}
function Money({ v, cur, muted }: { v: number | null | undefined; cur: string; muted?: boolean }): ReactNode {
  return <td className={`num${muted ? " muted" : ""}${(v ?? 0) < 0 ? " neg" : ""}`}>{fmtMoney(v, cur)}</td>;
}

function MonthPicker({ months, month, onChange }: { months: string[]; month: string; onChange: (m: string) => void }): ReactNode {
  return (
    <label className="row"><span className="sr">{tr("Reporting month")}</span>
      <select className="select" style={{ width: "auto" }} value={month} onChange={(e) => onChange(e.target.value)}>
        {[...months].reverse().map((m) => <option key={m} value={m}>{monthName(m)}</option>)}
      </select></label>
  );
}

function Head({ tab, months, month, sub }: { tab: string; months: string[]; month: string; sub: ReactNode }): ReactNode {
  return (
    <>
      <nav className="crumbs" aria-label={tr("You are here")}>
        <span className="crumb"><a href={`#/${withMonth("overview", month)}`}>{tr("Overview")}</a></span>
        <span className="crumb"><span className="crumb-sep" aria-hidden="true">›</span><span aria-current="page">{tr("Statements")}</span></span>
      </nav>
      <PageHead title={tr("Statements")} sub={sub} actions={<MonthPicker months={months} month={month} onChange={(x) => go(withMonth(`finance/${tab}`, x))} />} />
      <div className="seg" role="tablist" style={{ marginBottom: 16 }}>
        {TABS.map(([k, label]) => <button key={k} role="tab" aria-pressed={k === tab} aria-selected={k === tab} onClick={() => go(withMonth(`finance/${k}`, month))}>{tr(label)}</button>)}
      </div>
    </>
  );
}

export function FinancePage({ tab, sub }: { tab?: string; sub?: string }): ReactNode {
  const month = query().get("month");
  switch (tab || "statements") {
    case "budget": return <Budget month={month} />;
    case "receivables": return <Parties kind="receivables" month={month} />;
    case "payables": return <Parties kind="payables" month={month} />;
    case "anomalies": return <Anomalies month={month} />;
    default: return <Statements month={month} which={sub || "pl"} />;
  }
}

function Fail({ error }: { error: string }): ReactNode {
  return <div className="page">
    {/ledger/i.test(error) ? <Empty title={tr("No general ledger yet")} action={<a className="btn primary" href="#/data">{tr("Import a general ledger")}</a>}>
      {tr("Connect QuickBooks, Zoho Books, Xero or Odoo, or import a general-ledger file under Data. The overview is built from it.")}</Empty>
      : <ErrorBox error={error} />}
  </div>;
}

// ---- statements ----------------------------------------------------------------------------------------------------
function Statements({ month, which }: { month: string | null; which: string }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/overview/statements?periods=12${month ? `&month=${month}` : ""}`)), [ws.id, month]);
  if (loading && !data) return <Loading />;
  if (error) return <Fail error={error} />;
  const s = data;
  const cur = s.currency;
  const m: string = s.month;
  const cols: string[] = s.columns;
  const prov = s.provenance.kind;
  const hasPlan = s.profit_and_loss.lines.some((ln: any) => ln.plan);
  const sub = { pl: N("Profit and loss"), bs: N("Balance sheet"), cf: N("Cash flow") } as Record<string, string>;
  return (
    <div className="page wide">
      <Head tab="statements" months={s.months} month={m} sub={tr("Monthly statements from the general ledger “{0}”, to {1}.", { 0: s.ledger.name, 1: monthName(m) })} />
      <div className="spread" style={{ marginBottom: 12 }}>
        <div className="seg">
          {Object.entries(sub).map(([k, label]) => <button key={k} aria-pressed={which === k} onClick={() => go(withMonth(`finance/statements/${k}`, m))}>{tr(label)}</button>)}
        </div>
        <div className="row xs"><Prov kind={prov} /><span className="muted">{tr(PROV_WHY[prov])}</span>{hasPlan && <> · <Prov kind="budget" small /> {tr("Plan shown in grey under each month.")}</>}</div>
      </div>
      <div className="table-wrap" style={{ maxHeight: "none" }}>
        {which === "bs" ? (
          <table className="ledger pl fc-table">
            <thead><tr><th>{tr("Balance sheet")}</th>{cols.map((c) => <th key={c} className="num">{monthShort(c)}</th>)}</tr></thead>
            <tbody>
              {s.balance_sheet.sections.map((sec: any) => <>
                <tr key={sec.section} className="pl-section"><td colSpan={cols.length + 1}>{tr(SECTION[sec.section])}</td></tr>
                {sec.rows.map((r: any) => <tr key={r.measure}>
                  <td>{tr(MEASURE[r.measure] || r.measure)}{r.measure === "receivables" && <> · <a href={`#/${withMonth("finance/receivables", m)}`}>{tr("by customer")}</a></>}
                    {r.measure === "payables" && <> · <a href={`#/${withMonth("finance/payables", m)}`}>{tr("by supplier")}</a></>}</td>
                  {r.values.map((v: number, i: number) => <Money key={i} v={v} cur={cur} />)}
                </tr>)}
                <tr key={`${sec.section}-t`} className="pl-subtotal"><td>{tr("Total {0}", { 0: tr(SECTION[sec.section]).toLowerCase() })}</td>{sec.total.map((v: number, i: number) => <Money key={i} v={v} cur={cur} />)}</tr>
              </>)}
              <tr className="pl-total"><td>{tr("Total assets")}</td>{s.balance_sheet.totals.total_assets.map((v: number, i: number) => <Money key={i} v={v} cur={cur} />)}</tr>
              <tr className="pl-total"><td>{tr("Total liabilities")}</td>{s.balance_sheet.totals.total_liabilities.map((v: number, i: number) => <Money key={i} v={v} cur={cur} />)}</tr>
              <tr className="pl-total"><td>{tr("Equity (assets less liabilities)")}</td>{s.balance_sheet.totals.equity.map((v: number, i: number) => <Money key={i} v={v} cur={cur} />)}</tr>
              <tr><td className="muted">{tr("Working capital")}</td>{s.balance_sheet.totals.working_capital.map((v: number, i: number) => <Money key={i} v={v} cur={cur} muted />)}</tr>
              <tr><td className="muted">{tr("Net debt")}</td>{s.balance_sheet.totals.net_debt.map((v: number, i: number) => <Money key={i} v={v} cur={cur} muted />)}</tr>
            </tbody>
          </table>
        ) : which === "cf" ? (
          <table className="ledger pl fc-table">
            <thead><tr><th>{tr("Cash flow")}</th>{cols.map((c) => <th key={c} className="num">{monthShort(c)}</th>)}</tr></thead>
            <tbody>
              {([["opening", N("Opening cash")], ["operating", N("Operating activities")], ["investing", N("Investing activities")],
                ["financing", N("Financing activities")], ["net", N("Net change in cash")], ["closing", N("Closing cash")],
                ["free", N("Free cash flow")], ["cash_in", N("Money in")], ["cash_out", N("Money out")]] as [string, string][]).map(([k, label]) => (
                <tr key={k} className={k === "closing" ? "pl-total" : k === "net" ? "pl-subtotal" : ""}>
                  <td className={k === "free" || k === "cash_in" || k === "cash_out" ? "muted" : ""}>{tr(label)}</td>
                  {s.cash_flow.map((c: any) => <Money key={c.month} v={c[k]} cur={cur} muted={k === "free" || k === "cash_in" || k === "cash_out"} />)}
                </tr>
              ))}
              {s.cash_flow.some((c: any) => Math.abs(c.unexplained) >= 0.01) && <tr><td className="muted">{tr("Not explained by the classification")}</td>
                {s.cash_flow.map((c: any) => <Money key={c.month} v={c.unexplained} cur={cur} muted />)}</tr>}
            </tbody>
          </table>
        ) : (
          <table className="ledger pl fc-table">
            <thead><tr><th>{tr("Profit and loss")}</th>{cols.map((c) => <th key={c} className="num">{monthShort(c)}</th>)}<th className="num">{tr("Year to date")}</th></tr></thead>
            <tbody>
              {s.profit_and_loss.lines.map((ln: any) => <>
                <tr key={ln.measure} className={`pl-${ln.kind}`}>
                  <td>{ln.drillable ? <a href={`#/${withMonth(`overview/breakdown/${ln.measure}`, m)}`}>{tr(MEASURE[ln.measure])}</a> : tr(MEASURE[ln.measure])}</td>
                  {ln.values.map((v: number, i: number) => <Money key={i} v={v} cur={cur} />)}
                  <Money v={ln.ytd} cur={cur} />
                </tr>
                {ln.plan && <tr key={`${ln.measure}-plan`} className="pl-plan"><td className="xs muted">{tr("Plan")}</td>
                  {ln.plan.map((v: number | null, i: number) => <td key={i} className="num xs muted">{v === null ? "" : fmtMoney(v, cur)}</td>)}<td /></tr>}
                {(ln.kind === "subtotal" || ln.kind === "total") && <tr key={`${ln.measure}-m`} className="pl-margin"><td className="xs muted">{tr("% of revenue")}</td>
                  {s.profit_and_loss.margins[ln.measure].map((v: number | null, i: number) => <td key={i} className="num xs muted">{v === null ? "—" : fmtPercent(v, 1)}</td>)}<td /></tr>}
              </>)}
            </tbody>
          </table>
        )}
      </div>
      <p className="muted small" style={{ marginTop: 12 }}>
        {which === "bs" ? tr("Month-end balances. Equity is assets less liabilities, so it includes the profit not yet closed to reserves.")
          : which === "cf" ? tr("Bank movements classed by what each entry touches: financing when it touches debt or equity, investing when it touches fixed assets (a supplier payment is split by that supplier's bills), operating otherwise.")
          : tr("Costs are shown as positive amounts. Select a line to see the accounts behind it in the selected month.")}
      </p>
    </div>
  );
}

// ---- budget against actual -------------------------------------------------------------------------------------------
function Budget({ month }: { month: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/overview/budget${month ? `?month=${month}` : ""}`)), [ws.id, month]);
  if (loading && !data) return <Loading />;
  if (error) return <Fail error={error} />;
  const b = data;
  const cur = b.currency;
  const m: string = b.month;
  const Var = ({ v }: { v: any }) => v.amount === null ? <td className="num muted">—</td>
    : <td className={`num ${v.favourable ? "pos" : "neg"}`}>{fmtMoney(v.amount, cur)}{v.pct !== null && <span className="xs"> ({v.pct >= 0 ? "+" : "−"}{fmtPercent(Math.abs(v.pct), 1)})</span>}</td>;
  return (
    <div className="page wide">
      <Head tab="budget" months={b.months} month={m} sub={b.has_plan
        ? tr("Actual against the approved plan “{0}” for {1} and the year to date.", { 0: b.plan.name, 1: monthName(m) })
        : tr("Actual for {0}. There is no plan yet.", { 0: monthName(m) })} />
      {!b.has_plan && <div className="notice info" style={{ marginBottom: 16 }}>
        {tr("Budget against actual needs an approved forecast marked as the plan. Build one under Financial analysis → Forecasts, submit it, approve it and choose “Make this the plan”.")}{" "}
        <a href="#/analysis/forecasts">{tr("Open forecasts")}</a>
      </div>}
      <div className="row xs" style={{ marginBottom: 12 }}><Prov kind={b.provenance.kind} /><span className="muted">{tr(PROV_WHY[b.provenance.kind])}</span>{b.has_plan && <> · <Prov kind="budget" /></>}</div>
      <div className="table-wrap" style={{ maxHeight: "none" }}>
        <table className="ledger pl">
          <thead><tr><th>{tr("Line")}</th><th className="num">{tr("Actual")}</th><th className="num">{tr("Plan")}</th><th className="num">{tr("Variance")}</th>
            <th className="num">{tr("Actual, year to date")}</th><th className="num">{tr("Plan, year to date")}</th><th className="num">{tr("Variance, year to date")}</th></tr></thead>
          <tbody>
            {b.lines.map((ln: any) => <tr key={ln.measure} className={`pl-${ln.kind}`}>
              <td>{ln.drillable ? <a href={`#/${withMonth(`overview/breakdown/${ln.measure}`, m)}`}>{tr(MEASURE[ln.measure])}</a> : tr(MEASURE[ln.measure])}</td>
              <Money v={ln.actual} cur={cur} /><Money v={ln.plan} cur={cur} muted /><Var v={ln.variance} />
              <Money v={ln.actual_ytd} cur={cur} /><Money v={ln.plan_ytd} cur={cur} muted /><Var v={ln.variance_ytd} />
            </tr>)}
          </tbody>
        </table>
      </div>
      {b.drivers.length > 0 && <section className="section">
        <h2 className="h3">{tr("What drove the overspend")}</h2>
        <div className="grid2">
          {b.drivers.map((d: any) => <div key={d.measure} className="panel panel-pad">
            <div className="spread"><b>{tr(MEASURE[d.measure])}</b><span className="num neg">{tr("over by {0}", { 0: fmtMoney(d.over_by, cur) })}</span></div>
            <ul className="exec-list" style={{ marginTop: 8 }}>
              {d.accounts.map((a: any) => <li key={a.key}><a href={`#/${withMonth(`overview/transactions/${d.measure}`, m, { account: a.key })}`}>
                <span className="spread"><span>{a.account}</span><span className="num">{fmtMoney(a.value, cur)}<span className="xs muted"> {tr("last month {0}", { 0: fmtMoney(a.previous, cur) })}</span></span></span></a></li>)}
            </ul>
          </div>)}
        </div>
      </section>}
      <p className="muted small" style={{ marginTop: 12 }}>{tr("A variance is favourable when income is above plan or a cost is below it. Costs are shown as positive amounts.")}</p>
    </div>
  );
}

// ---- receivables and payables by party ------------------------------------------------------------------------------
function Parties({ kind, month }: { kind: "receivables" | "payables"; month: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/overview/parties/${kind}${month ? `?month=${month}` : ""}`)), [ws.id, kind, month]);
  if (loading && !data) return <Loading />;
  if (error) return <Fail error={error} />;
  const p = data;
  const cur = p.currency;
  const m: string = p.month;
  const ar = kind === "receivables";
  const who = ar ? tr("Customer") : tr("Supplier");
  const t = p.totals;
  const share = (v: number) => t.closing ? fmtPercent(v / t.closing, 0) : "—";
  return (
    <div className="page wide">
      <Head tab={kind} months={p.months} month={m} sub={ar
        ? tr("What customers owed at {0}, by customer, aged from the invoice date.", { 0: fmtDate(p.as_of) })
        : tr("What was owed to suppliers at {0}, by supplier, aged from the bill date.", { 0: fmtDate(p.as_of) })} />
      <div className="row xs" style={{ marginBottom: 12 }}><Prov kind={p.provenance.kind} /><span className="muted">{tr(PROV_WHY[p.provenance.kind])}</span></div>
      <div className="exec-tiles" style={{ marginBottom: 16 }}>
        <div className="exec-tile"><div className="exec-tile-label">{ar ? tr("Owed by customers") : tr("Owed to suppliers")}</div><div className="exec-tile-value num">{fmtMoney(t.closing, cur)}</div>
          <div className="exec-tile-why">{tr("Agrees with the ledger balance of {0}.", { 0: fmtMoney(p.ledger_balance, cur) })}{Math.abs(p.difference) >= 0.01 && <> {tr("Difference {0} has no document behind it (opening balances or lines without a party).", { 0: fmtMoney(p.difference, cur) })}</>}</div></div>
        <div className="exec-tile"><div className="exec-tile-label">{tr("More than 30 days")}</div><div className="exec-tile-value num">{fmtMoney(t.overdue_30, cur)}</div><div className="exec-tile-why">{share(t.overdue_30)} {tr("of the total")}</div></div>
        <div className="exec-tile"><div className="exec-tile-label">{tr("More than 90 days")}</div><div className="exec-tile-value num">{fmtMoney(t.buckets[3], cur)}</div><div className="exec-tile-why">{share(t.buckets[3])} {tr("of the total")}</div></div>
        <div className="exec-tile"><div className="exec-tile-label">{ar ? tr("Invoiced this month") : tr("Billed this month")}</div><div className="exec-tile-value num">{fmtMoney(t.raised, cur)}</div><div className="exec-tile-why">{tr("Settled this month: {0}", { 0: fmtMoney(t.settled, cur) })}</div></div>
      </div>
      {p.rows.length === 0 ? <Empty title={tr("Nothing open at {0}.", { 0: fmtDate(p.as_of) })} /> : (
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table className="ledger">
            <thead><tr><th>{who}</th><th className="num">{tr("Opening")}</th><th className="num">{ar ? tr("Invoiced") : tr("Billed")}</th><th className="num">{tr("Settled")}</th>
              <th className="num">{tr("Closing")}</th><th className="num">0–30</th><th className="num">31–60</th><th className="num">61–90</th><th className="num">90+</th><th>{tr("Oldest")}</th></tr></thead>
            <tbody>
              {p.rows.map((r: any) => <tr key={r.party} className={r.buckets[3] > 0 ? "aged" : ""}>
                <td>{r.party === "—" ? <span className="muted">{tr("(no name on the line)")}</span> : r.party}{r.unapplied > 0 && <span className="xs muted"> · {tr("{0} unapplied", { 0: fmtMoney(r.unapplied, cur) })}</span>}</td>
                <Money v={r.opening} cur={cur} muted /><Money v={r.raised} cur={cur} muted /><Money v={r.settled} cur={cur} muted /><Money v={r.closing} cur={cur} />
                {r.buckets.map((v: number, i: number) => <td key={i} className={`num${i >= 1 && v > 0 ? " neg" : i === 0 ? "" : " muted"}`}>{v ? fmtMoney(v, cur) : ""}</td>)}
                <td className="muted small">{r.oldest ? fmtDate(r.oldest) : "—"}</td>
              </tr>)}
            </tbody>
            <tfoot><tr><td>{tr("Total")}</td><td /><Money v={t.raised} cur={cur} /><Money v={t.settled} cur={cur} /><Money v={t.closing} cur={cur} />
              {t.buckets.map((v: number, i: number) => <Money key={i} v={v} cur={cur} />)}<td /></tr></tfoot>
          </table>
        </div>
      )}
      {p.count > p.rows.length && <p className="muted small" style={{ marginTop: 10 }}>{tr("Showing the {0} largest of {1}.", { 0: p.rows.length, 1: p.count })}</p>}
      <p className="muted small" style={{ marginTop: 12 }}>{ar
        ? tr("Each payment settles that customer's oldest open invoices first; what is left open is aged from the invoice date. Lines without a customer name are grouped together.")
        : tr("Each payment settles that supplier's oldest open bills first; what is left open is aged from the bill date. Lines without a supplier name are grouped together.")}</p>
    </div>
  );
}

// ---- anomalies ------------------------------------------------------------------------------------------------------
function Anomalies({ month }: { month: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/overview/anomalies${month ? `?month=${month}` : ""}`)), [ws.id, month]);
  if (loading && !data) return <Loading />;
  if (error) return <Fail error={error} />;
  const a = data;
  const cur = a.currency;
  const m: string = a.month;
  const text = (x: any): string => {
    switch (x.kind) {
      case "unusual_amount": return tr("{0} this month against an average of {1} over the previous six months ({2}).",
        { 0: fmtMoney(x.value, cur), 1: fmtMoney(x.average, cur), 2: x.change === null ? "—" : `${x.change >= 0 ? "+" : "−"}${fmtPercent(Math.abs(x.change), 0)}` });
      case "missing_posting": return tr("Posted every month recently (about {0} a month) but nothing this month.", { 0: fmtMoney(x.average, cur) });
      case "new_account": return tr("First posting to this account: {0}.", { 0: fmtMoney(x.value, cur) });
      case "possible_duplicate": return tr("{0} posted twice within a week ({1} and {2}){3}.", { 0: fmtMoney(x.value, cur), 1: x.entries[0], 2: x.entries[1], 3: x.party ? ` · ${x.party}` : "" });
      default: return "";
    }
  };
  return (
    <div className="page">
      <Head tab="anomalies" months={a.months} month={m} sub={tr("Postings in {0} that break the pattern of the previous months. Amounts under {1} are ignored.", { 0: monthName(m), 1: fmtMoney(a.materiality, cur) })} />
      <div className="row xs" style={{ marginBottom: 12 }}><Prov kind={a.provenance.kind} /><span className="muted">{tr(PROV_WHY[a.provenance.kind])}</span></div>
      {a.items.length === 0 ? <Empty title={tr("Nothing unusual in {0}.", { 0: monthName(m) })}>{a.history_months < 4 && tr("Anomalies need at least four earlier months to compare with.")}</Empty> : (
        <ul className="exec-list">
          {a.items.map((x: any, i: number) => <li key={i} className={`exec-risk ${x.level}`}>
            <a href={`#/${withMonth(x.kind === "possible_duplicate" ? `overview/entry/${encodeURIComponent(x.entries[0])}` : `overview/transactions/${x.measure}`, m,
              x.kind === "possible_duplicate" ? { measure: x.measure, account: x.key } : { account: x.key })}`}>
              <span className="tag amber">{tr(ANOMALY[x.kind])}</span> <b>{x.account}</b> <span className="xs muted">· {tr(MEASURE[x.measure] || x.measure)}</span>
              <div className="small muted">{text(x)}</div>
            </a>
          </li>)}
        </ul>
      )}
      <p className="muted small" style={{ marginTop: 12 }}>{tr("Each item opens the ledger lines behind it. {0} found; nothing is changed by looking.", { 0: fmtNumber(a.count, 0) })}</p>
    </div>
  );
}
