// Financial analysis: the KPI scorecard set by the finance lead, the account roles behind it, and forecasts.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { del, get, patch, post, put } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtDate, fmtFixed, fmtMoney, fmtNumber, fmtPercent } from "../lib/format";
import { Chart } from "../components/charts";
import { Confirm, Empty, ErrorBox, Field, Icon, Loading, Modal, PageHead, go, useAction, useLoad } from "../components/ui";
import { N, tr } from "../lib/i18n";

const CATEGORY: Record<string, string> = {
  growth_profitability: N("Growth and profitability"), liquidity: N("Liquidity"), working_capital: N("Working capital"),
  leverage: N("Leverage"), cash_flow: N("Cash flow"), cost_control: N("Cost and control"),
};
const KPI_NAMES: Record<string, string> = {
  revenue_growth: N("Revenue growth (year on year)"), gross_margin: N("Gross margin"), ebitda_margin: N("EBITDA margin"),
  operating_margin: N("Operating margin"), net_margin: N("Net profit margin"), roe: N("Return on equity"),
  roa: N("Return on assets"), roce: N("Return on capital employed"), current_ratio: N("Current ratio"),
  quick_ratio: N("Quick ratio"), cash_ratio: N("Cash ratio"), working_capital: N("Working capital"),
  days_cash: N("Days of cash on hand"), dso: N("Days sales outstanding (DSO)"), dpo: N("Days payables outstanding (DPO)"),
  dio: N("Days inventory outstanding (DIO)"), cash_conversion_cycle: N("Cash conversion cycle"),
  ar_overdue: N("Receivables more than 30 days overdue"), ap_overdue: N("Payables more than 30 days overdue"),
  collection_rate: N("Collection rate"), debt_to_equity: N("Debt to equity"), net_debt_to_ebitda: N("Net debt to EBITDA"),
  interest_cover: N("Interest cover"), dscr: N("Debt service coverage (DSCR)"), equity_ratio: N("Equity ratio"),
  operating_cash_flow: N("Operating cash flow (3 months)"), free_cash_flow: N("Free cash flow (3 months)"),
  cash_runway: N("Cash runway"), earnings_quality: N("Cash conversion of profit"),
  opex_ratio: N("Operating expenses to revenue"), payroll_ratio: N("Payroll to revenue"),
  margin_of_safety: N("Margin of safety over break-even"), plan_variance: N("Revenue against plan"),
  forecast_accuracy: N("Forecast accuracy (revenue, 3 months)"), customer_concentration: N("Revenue from top 5 customers"),
};
const MISSING: Record<string, string> = {
  history: N("Needs more months of ledger history."), no_documents: N("Needs invoices and bills synced from a connection."),
  no_plan: N("Needs an approved forecast marked as the plan."), not_burning: N("Cash is not being burned: operating and investing cash flow is positive."), no_customers: N("The ledger's revenue lines do not name customers."),
  zero: N("Not meaningful this month (a zero in the formula)."), formula: N("The formula cannot be read."),
};
const ROLE_NAMES: Record<string, string> = {
  cash: N("Bank and cash"), receivables: N("Receivables"), inventory: N("Inventory"), other_current_assets: N("Other current assets"),
  fixed_assets: N("Fixed assets"), other_non_current_assets: N("Other non-current assets"), payables: N("Payables"),
  other_current_liabilities: N("Other current liabilities"), short_term_debt: N("Short-term debt"), long_term_debt: N("Long-term debt"),
  other_non_current_liabilities: N("Other non-current liabilities"), equity: N("Equity"), revenue: N("Revenue"),
  other_income: N("Other income"), cogs: N("Cost of sales"), payroll: N("Payroll"), opex: N("Other operating expenses"),
  depreciation: N("Depreciation and amortisation"), interest: N("Interest"), tax: N("Tax on profit"), other_expense: N("Other expenses"),
};
const PL_ROLES = ["revenue", "other_income", "cogs", "payroll", "opex", "depreciation", "interest", "tax", "other_expense"];
const PL_CLASSES = ["Income", "Other income", "Cost of sales", "Expense", "Other expense"];
const METHODS: Record<string, string> = {
  growth: N("Monthly growth on the recent average"), yoy: N("Same month last year, plus growth"), pct_of: N("Percentage of another line"),
  average: N("Average of recent months"), trend: N("Straight-line trend"), fixed: N("Fixed amount each month"), manual: N("Month by month"),
};
const STATUS: Record<string, string> = { draft: N("draft"), submitted: N("waiting for approval"), approved: N("approved"), archived: N("archived") };
const RESET = N("Reset");
const UNITS = ["percent", "ratio", "days", "money", "months", "number"];

const kpiName = (k: any) => (KPI_NAMES[k.id] && !k.custom ? tr(KPI_NAMES[k.id]) : k.name);

function fmtKpi(v: number | null | undefined, unit: string, cur: string): string {
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

function fmtDelta(v: number | null, p: number | null, unit: string, cur: string): string {
  if (v === null || p === null) return "";
  const d = v - p;
  const tiny = unit === "percent" ? 0.0005 : unit === "days" ? 0.5 : unit === "months" ? 0.05 : unit === "ratio" ? 0.005 : 0.005;
  if (Math.abs(d) < tiny) return "±0";
  const sign = d > 0 ? "+" : "−";
  if (unit === "percent") return `${sign}${fmtNumber(Math.abs(d) * 100, 1)} ${tr("pts")}`;
  return sign + fmtKpi(Math.abs(d), unit, cur);
}

export function AnalysisPage({ tab, id }: { tab?: string; id?: string }): ReactNode {
  const t = tab || "kpis";
  return (
    <div className="page" style={{ maxWidth: 1240 }}>
      <PageHead title={tr("Financial analysis")}
        sub={tr("The company's performance from the general ledger, on the targets set by the finance lead, and forecasts the finance lead approves.")} />
      <div className="seg" role="tablist" style={{ marginBottom: 16 }}>
        {[["kpis", N("KPIs")], ["forecasts", N("Forecasts")], ["roles", N("Account roles")]].map(([k, l]) => (
          <button key={k} role="tab" aria-pressed={t === k} onClick={() => go(k === "kpis" ? "analysis" : `analysis/${k}`)}>{tr(l)}</button>))}
      </div>
      {t === "forecasts" ? (id ? <ForecastView id={id} /> : <ForecastList />) : t === "roles" ? <RolesView /> : <Scorecard />}
    </div>
  );
}

// ---- scorecard ------------------------------------------------------------------------------------
function Spark({ values }: { values: (number | null)[] }): ReactNode {
  const pts = values.map((v, i) => [i, v] as const).filter(([, v]) => v !== null) as [number, number][];
  if (pts.length < 2) return <svg className="spark" aria-hidden="true" />;
  const lo = Math.min(...pts.map((p) => p[1])), hi = Math.max(...pts.map((p) => p[1]));
  const w = 120, h = 28, n = values.length - 1 || 1;
  const y = (v: number) => (hi === lo ? h / 2 : h - 2 - ((v - lo) / (hi - lo)) * (h - 4));
  const d = pts.map(([i, v], j) => `${j ? "L" : "M"}${(i / n) * w},${y(v)}`).join(" ");
  const [li, lv] = pts[pts.length - 1];
  return <svg className="spark" viewBox={`0 0 ${w} ${h}`} aria-hidden="true"><path d={d} /><circle cx={(li / n) * w} cy={y(lv)} r={2.5} /></svg>;
}

function DataNotes({ d }: { d: any }): ReactNode {
  const unconfirmed = d.roles.accounts - d.roles.confirmed;
  return (
    <div className="row wrap small muted" style={{ gap: 14, marginBottom: 14 }}>
      <span>{tr("Ledger “{0}”, {1} to {2}", { 0: d.ledger.name, 1: fmtDate(d.ledger.first_date), 2: fmtDate(d.ledger.last_date) })}</span>
      <a href="#/analysis/roles">{unconfirmed ? tr("{0} of {1} account roles not yet confirmed", { 0: unconfirmed, 1: d.roles.accounts })
        : tr("All account roles confirmed")}</a>
      {d.plan ? <span>{tr("Plan: {0}", { 0: `${d.plan.name} · ${d.plan.scenario} v${d.plan.version}` })}</span> : null}
      {!d.balance.balanced && <span className="tag red">{tr("The ledger does not balance ({0})", { 0: fmtNumber(d.balance.difference) })}</span>}
      {d.opening?.source === "balance_sheet" && d.opening.unmatched_accounts > 0 &&
        <span className="tag amber">{tr("{0} balance-sheet accounts have no opening balance", { 0: d.opening.unmatched_accounts })}</span>}
    </div>
  );
}

function Scorecard(): ReactNode {
  const { ws } = useApp();
  const [month, setMonth] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const data = useLoad(() => get<any>(wsPath(ws, `/analysis${month ? `?month=${month}` : ""}`)), [ws.id, month]);
  const d = data.data;
  const cur = ws.base_currency;
  if (data.error) {
    return <div className="panel"><Empty title={tr("No general ledger yet")}>{tr("Financial analysis reads journal lines. Sync QuickBooks Online, Xero or Odoo under Connections, push journal lines from another system through the Open Accounting Connector, or import an Excel or CSV file with the general-ledger columns under Data.")}</Empty></div>;
  }
  if (!d) return <Loading />;
  return (
    <>
      <div className="spread" style={{ marginBottom: 6 }}>
        <DataNotes d={d} />
        <div className="row">
          {d.ledgers.length > 1 && can(ws, "admin") && <select className="select" style={{ width: 240 }} aria-label={tr("General ledger")} value={d.ledger.id}
            onChange={async (e) => { await put(wsPath(ws, "/analysis/ledger"), { dataset_id: e.target.value }); setMonth(""); data.reload(); }}>
            {d.ledgers.map((l: any) => <option key={l.id} value={l.id}>{l.name}</option>)}</select>}
          <select className="select" style={{ width: 150 }} aria-label={tr("Month")} value={month || d.month || ""} onChange={(e) => setMonth(e.target.value)}>
            {[...d.months].reverse().map((m: string) => <option key={m} value={m}>{m}</option>)}</select>
          {can(ws, "admin") && <button className="btn" onClick={() => setAdding(true)}><Icon name="plus" />{tr("Add a KPI")}</button>}
        </div>
      </div>
      {d.categories.map((cat: string) => {
        const cards = d.kpis.filter((k: any) => k.category === cat);
        if (!cards.length) return null;
        return (
          <section key={cat} className="kpi-group">
            <h2 className="h3">{tr(CATEGORY[cat] || cat)}</h2>
            <div className="kpi-grid">
              {cards.map((k: any) => (
                <button key={k.id} className={`kpi-card ${k.status || ""}`} onClick={() => setOpen(k.id)}>
                  <span className="kpi-name">{kpiName(k)}</span>
                  <span className="kpi-value">{fmtKpi(k.value, k.unit, cur)}</span>
                  {k.value === null ? <span className="xs muted">{tr(MISSING[k.missing] || MISSING.zero)}</span> : (
                    <span className="row xs" style={{ gap: 8 }}>
                      <span className="muted kpi-delta">{fmtDelta(k.value, k.previous, k.unit, cur)}</span>
                      {k.status && <span className={`kpi-status ${k.status}`}>{k.status === "good" ? tr("on target") : k.status === "watch" ? tr("watch") : tr("act")}</span>}
                      {k.target != null && <span className="muted">{tr("target")} {k.direction === "up" ? "≥" : "≤"} {fmtKpi(k.target, k.unit, cur)}</span>}
                    </span>)}
                  <Spark values={k.spark} />
                </button>))}
            </div>
          </section>
        );
      })}
      {open && <KpiModal id={open} month={month || d.month} cur={cur} onClose={() => setOpen(null)} onSaved={data.reload} />}
      {adding && <KpiForm onClose={() => setAdding(false)} onSaved={() => { setAdding(false); data.reload(); }} />}
    </>
  );
}

function KpiModal({ id, month, cur, onClose, onSaved }: { id: string; month: string; cur: string; onClose: () => void; onSaved: () => void }): ReactNode {
  const { ws } = useApp();
  const d = useLoad(() => get<any>(wsPath(ws, `/analysis/kpis/${id}?month=${month}`)), [id, month]);
  const [f, setF] = useState<any>(null);
  const [confirm, setConfirm] = useState(false);
  const { busy, run } = useAction();
  const k = d.data?.kpi;
  useEffect(() => { if (k) setF({ target: k.target ?? "", warn: k.warn ?? "", formula: k.formula }); }, [k?.id, k?.formula, k?.target, k?.warn]);
  const admin = can(ws, "admin");
  const num = (v: any) => (v === "" || v === null || v === undefined ? null : Number(v));
  async function save() {
    const body: any = { target: num(f.target), warn: num(f.warn) };
    if (f.formula !== k.formula) body.formula = f.formula;
    if (await run(() => put(wsPath(ws, `/analysis/kpis/${id}`), body), tr("Saved"))) { onSaved(); onClose(); }
  }
  return (
    <Modal title={k ? kpiName(k) : tr("KPI")} onClose={onClose} wide
      footer={admin && f ? <>
        {(k?.changed || k?.custom || k?.target != null) && <button className="btn ghost" onClick={() => setConfirm(true)}>{k?.custom ? tr("Delete") : tr("Back to the standard")}</button>}
        <span className="grow" />
        <button className="btn" onClick={onClose}>{tr("Close")}</button>
        <button className="btn primary" disabled={busy} onClick={save}>{tr("Save")}</button></> : undefined}>
      <ErrorBox error={d.error} />
      {!d.data || !k ? <Loading /> : <div className="stack">
        <div className="row" style={{ gap: 18, alignItems: "baseline" }}>
          <span className="kpi-value big">{fmtKpi(d.data.value, k.unit, cur)}</span>
          <span className="muted small">{d.data.month}</span>
        </div>
        <Chart kind="line" categories={d.data.series.map((s: any) => s.month)}
          series={[{ name: kpiName(k), values: d.data.series.map((s: any) => (s.value === null ? null : k.unit === "percent" ? s.value * 100 : s.value)) }]}
          format={k.unit === "money" ? "money" : undefined} currency={cur} />
        <div>
          <div className="small"><strong>{tr("Formula")}</strong>{" "}<code dir="ltr">{k.formula}</code></div>
          {k.changed && <div className="xs muted">{tr("Standard formula")}: <code dir="ltr">{k.standard_formula}</code></div>}
        </div>
        <table className="ledger">
          <thead><tr><th>{tr("Part")}</th><th className="num">{tr("Value in {0}", { 0: d.data.month })}</th><th>{tr("Largest accounts")}</th></tr></thead>
          <tbody>{d.data.parts.map((p: any) => (
            <tr key={p.name}><td><code dir="ltr">{p.name}</code><div className="xs muted">{p.kind === "kpi" ? kpiName({ id: p.name, name: p.description }) : p.description}</div></td>
              <td className="num">{p.value === null ? "—" : fmtNumber(p.value)}</td>
              <td className="small">{p.accounts.slice(0, 5).map((a: any) => <div key={a.key} className="spread"><span>{a.account}</span><span className="num">{fmtNumber(a.amount)}</span></div>)}</td></tr>))}</tbody>
        </table>
        {admin && f && <div className="grid2">
          <Field label={k.direction === "up" ? tr("Target (at least)") : tr("Target (at most)")} hint={k.unit === "percent" ? tr("As a fraction: 0.25 for 25%.") : undefined}>
            <input className="input" type="number" step="any" value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} /></Field>
          <Field label={tr("Warning level")} hint={tr("Between the target and this level the KPI shows “watch”; beyond it, “act”.")}>
            <input className="input" type="number" step="any" value={f.warn} onChange={(e) => setF({ ...f, warn: e.target.value })} /></Field>
          <div style={{ gridColumn: "1 / -1" }}><Field label={tr("Formula")} hint={tr("Measures such as revenue, cash or receivables, other KPI ids, and SUMN(x, n), AVGN(x, n), PREV(x, n), AVGBAL(x), IFPOS(x), ABS, MIN, MAX.")}>
            <input className="input" dir="ltr" value={f.formula} onChange={(e) => setF({ ...f, formula: e.target.value })} /></Field></div>
        </div>}
      </div>}
      {confirm && <Confirm text={k?.custom ? tr("Delete this KPI?") : tr("Go back to the standard formula and remove the target?")} yes={k?.custom ? "Delete" : RESET}
        onClose={() => setConfirm(false)} onYes={() => run(() => del(wsPath(ws, `/analysis/kpis/${id}`))).then(() => { onSaved(); onClose(); })} />}
    </Modal>
  );
}

function KpiForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }): ReactNode {
  const { ws } = useApp();
  const [f, setF] = useState({ id: "", name: "", category: "cost_control", unit: "percent", direction: "up", formula: "" });
  const { busy, run } = useAction();
  const id = (f.id || f.name).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || "custom_kpi";
  return (
    <Modal title={tr("Add a KPI")} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{tr("Cancel")}</button>
        <button className="btn primary" disabled={busy || !f.name || !f.formula} onClick={async () => {
          if (await run(() => put(wsPath(ws, `/analysis/kpis/${id}`), { name: f.name, category: f.category, unit: f.unit, direction: f.direction, formula: f.formula }), tr("Saved"))) onSaved();
        }}>{tr("Save")}</button></>}>
      <div className="grid2">
        <Field label={tr("Name")}><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label={tr("Id in formulas")}><input className="input" dir="ltr" value={f.id} placeholder={id} onChange={(e) => setF({ ...f, id: e.target.value })} /></Field>
        <Field label={tr("Group")}><select className="select" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
          {Object.entries(CATEGORY).map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}</select></Field>
        <Field label={tr("Shown as")}><select className="select" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })}>
          {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}</select></Field>
        <Field label={tr("Better when")}><select className="select" value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })}>
          <option value="up">{tr("higher")}</option><option value="down">{tr("lower")}</option></select></Field>
        <Field label={tr("Formula")} hint={tr("For example SUMN(revenue, 3) / SUMN(payroll, 3)")}>
          <input className="input" dir="ltr" value={f.formula} onChange={(e) => setF({ ...f, formula: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}

// ---- account roles ----------------------------------------------------------------------------------
function RolesView(): ReactNode {
  const { ws } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, "/analysis/roles")), [ws.id]);
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [onlyOpen, setOnlyOpen] = useState(false);
  const { busy, run } = useAction();
  const d = data.data;
  if (data.error) return <ErrorBox error={data.error} />;
  if (!d) return <Loading />;
  const editor = can(ws, "editor");
  const rows = d.accounts.filter((a: any) => !onlyOpen || !a.confirmed);
  async function save(items: { key: string; role: string }[]) {
    if (!items.length) return;
    if (await run(() => put(wsPath(ws, "/analysis/roles"), { dataset_id: d.dataset_id, items }), tr("Saved"))) { setChanges({}); data.reload(); }
  }
  const open = d.accounts.filter((a: any) => !a.confirmed);
  return (
    <>
      <p className="small" style={{ color: "var(--ink-2)", maxWidth: "80ch" }}>
        {tr("Each ledger account has a role, so the ratios work on any chart of accounts. Roles are suggested from the account type and words in its name; confirm them or choose another.")}</p>
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <label className="check small"><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /><span>{tr("Only roles not yet confirmed")} ({open.length})</span></label>
        <span className="grow" />
        {d.opening?.source === "balance_sheet" ? <span className="xs muted">{tr("Opening balances from the balance sheet of {0}: {1} accounts matched.", { 0: fmtDate(d.opening.as_of), 1: d.opening.matched_accounts })}</span>
          : <span className="xs muted">{tr("No balance sheet synced with this ledger: balances start at zero on its first date, which suits a ledger that starts with an opening entry.")}</span>}
        {editor && open.length > 0 && <button className="btn" disabled={busy} onClick={() => save(open.map((a: any) => ({ key: a.key, role: changes[a.key] || a.role })))}>{tr("Confirm all suggestions")}</button>}
        {editor && Object.keys(changes).length > 0 && <button className="btn primary" disabled={busy} onClick={() => save(Object.entries(changes).map(([key, role]) => ({ key, role })))}>{tr("Save changes")}</button>}
      </div>
      <div className="table-wrap" style={{ maxHeight: 620 }}>
        <table className="ledger">
          <thead><tr><th>{tr("Account")}</th><th>{tr("Type in the ledger")}</th><th>{tr("Role")}</th><th>{tr("Status")}</th>
            <th className="num">{tr("Debit")}</th><th className="num">{tr("Credit")}</th></tr></thead>
          <tbody>{rows.map((a: any) => {
            const pl = PL_CLASSES.includes(a.class);
            const options = Object.keys(ROLE_NAMES).filter((r) => PL_ROLES.includes(r) === pl);
            const value = changes[a.key] || a.role;
            return (
              <tr key={a.key}><td>{a.code ? `${a.code} ` : ""}{a.name}</td><td className="small">{a.class}</td>
                <td>{editor ? <select className="select" style={{ minWidth: 210 }} value={value} aria-label={tr("Role of {0}", { 0: a.name })}
                  onChange={(e) => setChanges({ ...changes, [a.key]: e.target.value })}>
                  {options.map((r) => <option key={r} value={r}>{tr(ROLE_NAMES[r])}{r === a.suggested ? ` (${tr("suggested")})` : ""}</option>)}</select> : tr(ROLE_NAMES[value])}</td>
                <td className="small">{a.confirmed ? <span className="tag green">{tr("confirmed")}</span> : <span className="tag amber">{tr("suggested")}</span>}
                  {a.confirmed_by ? <span className="xs muted"> {a.confirmed_by}</span> : null}</td>
                <td className="num">{fmtNumber(a.debit)}</td><td className="num">{fmtNumber(a.credit)}</td></tr>);
          })}</tbody>
        </table>
      </div>
    </>
  );
}

// ---- forecasts --------------------------------------------------------------------------------------
function ForecastList(): ReactNode {
  const { ws } = useApp();
  const data = useLoad(() => get<any[]>(wsPath(ws, "/forecasts")), [ws.id]);
  const [creating, setCreating] = useState(false);
  const [f, setF] = useState({ name: "", scenario: "Base", start_month: "", months: 12 });
  const { busy, run } = useAction();
  return (
    <>
      <div className="spread" style={{ marginBottom: 12 }}>
        <p className="small" style={{ color: "var(--ink-2)", maxWidth: "75ch", margin: 0 }}>
          {tr("The finance team sets a method and an assumption for every line; the forecast projects profit, working capital and cash. A draft is submitted, and the finance lead approves it and can make it the plan the KPIs compare against.")}</p>
        {can(ws, "editor") && <button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" />{tr("New forecast")}</button>}
      </div>
      <ErrorBox error={data.error} />
      {creating && <Modal title={tr("New forecast")} onClose={() => setCreating(false)}
        footer={<><button className="btn" onClick={() => setCreating(false)}>{tr("Cancel")}</button>
          <button className="btn primary" disabled={busy || !f.name.trim()} onClick={async () => {
            const r: any = await run(() => post(wsPath(ws, "/forecasts"), { ...f, start_month: f.start_month || null, months: Number(f.months) }), tr("Forecast created"));
            if (r) go(`analysis/forecasts/${r.id}`);
          }}>{tr("Create")}</button></>}>
        <div className="grid2">
          <Field label={tr("Name")}><input className="input" value={f.name} placeholder={tr("e.g. Rolling forecast 2027")} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label={tr("Scenario")}><input className="input" value={f.scenario} onChange={(e) => setF({ ...f, scenario: e.target.value })} /></Field>
          <Field label={tr("First month")} hint={tr("Leave empty to start after the last month of the ledger.")}>
            <input className="input" type="month" value={f.start_month} onChange={(e) => setF({ ...f, start_month: e.target.value })} /></Field>
          <Field label={tr("Months")}><input className="input" type="number" min={1} max={36} value={f.months} onChange={(e) => setF({ ...f, months: Number(e.target.value) })} /></Field>
        </div>
        <p className="xs muted">{tr("It starts from assumptions taken from your own history; change them on the next page.")}</p>
      </Modal>}
      {data.loading && !data.data ? <Loading /> : (data.data || []).length === 0 ? (
        <div className="panel"><Empty title={tr("No forecasts yet")}>{tr("Create one to project revenue, profit and cash month by month.")}</Empty></div>
      ) : (
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table className="ledger">
            <thead><tr><th>{tr("Forecast")}</th><th>{tr("Scenario")}</th><th>{tr("Status")}</th><th>{tr("Period")}</th><th>{tr("Approved by")}</th></tr></thead>
            <tbody>{(data.data || []).map((x) => (
              <tr key={x.id} className="clickable" onClick={() => go(`analysis/forecasts/${x.id}`)}>
                <td><a href={`#/analysis/forecasts/${x.id}`} onClick={(e) => e.stopPropagation()}>{x.name}</a> <span className="muted">v{x.version}</span>
                  {x.is_plan ? <> <span className="tag blue">{tr("plan")}</span></> : null}</td>
                <td>{x.scenario}</td><td><StatusTagF s={x.status} /></td>
                <td className="small">{x.start_month} · {tr("{0} months", { 0: x.months })}</td><td className="small">{x.approved_by_name || "—"}</td></tr>))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}

function StatusTagF({ s }: { s: string }): ReactNode {
  const tone = s === "approved" ? "green" : s === "submitted" ? "amber" : "";
  return <span className={`tag ${tone}`}>{tr(STATUS[s] || s)}</span>;
}

function Assumption({ line, x, onChange, lines }: { line: string; x: any; onChange: (x: any) => void; lines: string[] }): ReactNode {
  const ofOptions = [...lines.slice(0, lines.indexOf(line)), ...(line === "tax" ? ["profit_before_tax"] : [])];
  const set = (k: string, v: any) => onChange({ ...x, [k]: v });
  return (
    <tr>
      <td>{tr(ROLE_NAMES[line] || line)}</td>
      <td><select className="select" value={x.method} aria-label={tr("Method for {0}", { 0: tr(ROLE_NAMES[line] || line) })}
        onChange={(e) => {
          const m = e.target.value;
          onChange(m === "pct_of" ? { method: m, of: ofOptions[0], value: x.value ?? 0 } : m === "average" || m === "trend" ? { method: m, n: m === "trend" ? 12 : 3 }
            : m === "manual" ? { method: m, values: [] } : { method: m, value: x.value ?? 0, n: m === "growth" ? 3 : undefined });
        }}>
        {Object.entries(METHODS).filter(([m]) => m !== "pct_of" || ofOptions.length).map(([m, l]) => <option key={m} value={m}>{tr(l)}</option>)}</select></td>
      <td>
        {(x.method === "growth" || x.method === "yoy") && <span className="row"><input className="input num-in" type="number" step="0.1" value={x.value ?? 0} onChange={(e) => set("value", Number(e.target.value))} /><span>{x.method === "growth" ? tr("% a month, on the average of the last") : tr("% on the same month last year")}</span>
          {x.method === "growth" && <><input className="input num-in" type="number" min={1} max={24} value={x.n ?? 3} onChange={(e) => set("n", Number(e.target.value))} /><span>{tr("months")}</span></>}</span>}
        {x.method === "pct_of" && <span className="row"><input className="input num-in" type="number" step="0.1" value={x.value ?? 0} onChange={(e) => set("value", Number(e.target.value))} /><span>{tr("% of")}</span>
          <select className="select" style={{ width: 200 }} value={x.of} onChange={(e) => set("of", e.target.value)}>
            {ofOptions.map((o) => <option key={o} value={o}>{o === "profit_before_tax" ? tr("Profit before tax") : tr(ROLE_NAMES[o] || o)}</option>)}</select></span>}
        {(x.method === "average" || x.method === "trend") && <span className="row"><span>{tr("last")}</span><input className="input num-in" type="number" min={1} max={24} value={x.n ?? 3} onChange={(e) => set("n", Number(e.target.value))} /><span>{tr("months")}</span></span>}
        {x.method === "fixed" && <input className="input num-in wide" type="number" step="any" value={x.value ?? 0} onChange={(e) => set("value", Number(e.target.value))} />}
        {x.method === "manual" && <input className="input" dir="ltr" placeholder="12000, 12500, 13000" value={(x.values || []).join(", ")}
          onChange={(e) => set("values", e.target.value.split(/[,;\s]+/).filter(Boolean).map(Number).filter((n) => Number.isFinite(n)))} />}
      </td>
    </tr>
  );
}

function ForecastView({ id }: { id: string }): ReactNode {
  const { ws } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, `/forecasts/${id}`)), [ws.id, id]);
  const [a, setA] = useState<any>(null);
  const [view, setView] = useState<any>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [scenario, setScenario] = useState<string | null>(null);
  const cmp = useLoad(() => get<any[]>(wsPath(ws, `/forecasts/${id}/compare`)), [id, data.data?.forecast?.updated_at]);
  const { busy, run } = useAction();
  const f = data.data?.forecast;
  useEffect(() => { if (f) { setA(f.assumptions); setView(data.data); } }, [f?.id, f?.updated_at, f?.status]);
  const draft = f?.status === "draft" && can(ws, "editor");
  const dirty = !!(f && a && JSON.stringify(a) !== JSON.stringify(f.assumptions));
  useEffect(() => {
    if (!dirty) { if (data.data) setView(data.data); return undefined; }
    const t = setTimeout(() => {
      post(wsPath(ws, `/forecasts/${id}/preview`), { assumptions: a }).then((v) => { setView(v); setPreviewError(null); })
        .catch((e) => setPreviewError(e?.message || String(e)));
    }, 350);
    return () => clearTimeout(t);
  }, [JSON.stringify(a)]);
  const cur = ws.base_currency;
  const v = view;
  const res = v?.result;
  const chart = useMemo(() => {
    if (!res || !v) return null;
    const am = v.actual.months, fm = res.months;
    const pad = (n: number) => Array(n).fill(null);
    return {
      cats: [...am, ...fm],
      rev: [{ name: tr("Actual"), values: [...v.actual.revenue, ...pad(fm.length)] },
        { name: tr("Forecast"), values: [...pad(am.length), ...res.lines.revenue] },
        ...(res.reference ? [{ name: tr("Statistical reference"), values: [...pad(am.length), ...res.reference.values] }] : [])],
      cash: [{ name: tr("Actual"), values: [...v.actual.cash, ...pad(fm.length)] }, { name: tr("Forecast"), values: [...pad(am.length), ...res.balances.cash] }],
    };
  }, [res, v]);
  async function act(status: string) {
    if (dirty && !(await save())) return;
    if (await run(() => post(wsPath(ws, `/forecasts/${id}/status`), { status }), tr("Saved"))) data.reload();
  }
  async function save(): Promise<boolean> {
    const r = await run(() => patch(wsPath(ws, `/forecasts/${id}`), { assumptions: a }), tr("Saved"));
    if (r) data.reload();
    return !!r;
  }
  async function copy(scenario?: string) {
    const r: any = await run(() => post(wsPath(ws, `/forecasts/${id}/copy`), scenario ? { scenario } : {}), tr("Forecast created"));
    if (r) go(`analysis/forecasts/${r.id}`);
  }
  if (data.error) return <ErrorBox error={data.error} />;
  if (!f || !a || !res) return <Loading />;
  const s = res.summary;
  const rows: [string, number[]][] = [
    ...v.lines.map((l: string) => [tr(ROLE_NAMES[l] || l), res.lines[l]] as [string, number[]]),
    [tr("Gross profit"), res.gross_profit], [tr("EBITDA"), res.ebitda], [tr("Net profit"), res.net_profit],
    [tr("Receivables"), res.balances.receivables], [tr("Inventory"), res.balances.inventory], [tr("Payables"), res.balances.payables],
    [tr("Bank and cash"), res.balances.cash],
  ];
  return (
    <div className="stack">
      <p className="small"><a href="#/analysis/forecasts">{tr("Forecasts")}</a></p>
      <div className="spread">
        <div>
          <h2>{f.name} <span className="muted">· {f.scenario} v{f.version}</span></h2>
          <div className="row small" style={{ gap: 8, marginTop: 4 }}><StatusTagF s={f.status} />{f.is_plan && <span className="tag blue">{tr("plan")}</span>}
            <span className="muted">{res.months[0]} – {res.months[res.months.length - 1]}</span>
            {f.approved_by_name && <span className="muted">{tr("Approved by {0}", { 0: f.approved_by_name })}</span>}</div>
        </div>
        <div className="row wrap">
          {draft && dirty && <button className="btn" disabled={busy} onClick={save}>{tr("Save")}</button>}
          {draft && <button className="btn primary" disabled={busy} onClick={() => act("submitted")}>{tr("Submit for approval")}</button>}
          {f.status === "submitted" && can(ws, "editor") && <button className="btn" disabled={busy} onClick={() => act("draft")}>{tr("Return to draft")}</button>}
          {f.status === "submitted" && can(ws, "admin") && <button className="btn primary" disabled={busy} onClick={() => act("approved")}>{tr("Approve")}</button>}
          {f.status === "approved" && !f.is_plan && can(ws, "admin") && <button className="btn primary" disabled={busy}
            onClick={() => run(() => post(wsPath(ws, `/forecasts/${id}/plan`)), tr("This forecast is now the plan")).then(data.reload)}>{tr("Make it the plan")}</button>}
          {f.status !== "draft" && can(ws, "editor") && <button className="btn" disabled={busy} onClick={() => copy()}>{tr("New version")}</button>}
          {can(ws, "editor") && <button className="btn" disabled={busy} onClick={() => setScenario(tr("Downside"))}>{tr("New scenario")}</button>}
          {f.status === "approved" && can(ws, "admin") && <button className="btn ghost" disabled={busy} onClick={() => act("archived")}>{tr("Archive")}</button>}
          {f.status !== "approved" && !f.is_plan && can(ws, "editor") && <button className="btn ghost" aria-label={tr("Delete")} onClick={() => setConfirm(true)}><Icon name="trash" /></button>}
        </div>
      </div>
      {scenario !== null && <Modal title={tr("New scenario")} onClose={() => setScenario(null)}
        footer={<><button className="btn" onClick={() => setScenario(null)}>{tr("Cancel")}</button>
          <button className="btn primary" disabled={busy || !scenario.trim()} onClick={() => { void copy(scenario.trim()); setScenario(null); }}>{tr("Create")}</button></>}>
        <Field label={tr("Name of the new scenario")} hint={tr("It starts as a copy of these assumptions.")}>
          <input className="input" value={scenario} onChange={(e) => setScenario(e.target.value)} /></Field>
      </Modal>}
      {confirm && <Confirm text={tr("Delete this forecast?")} onClose={() => setConfirm(false)} onYes={() => run(() => del(wsPath(ws, `/forecasts/${id}`))).then(() => go("analysis/forecasts"))} />}
      <ErrorBox error={previewError} />
      {s.cash_negative_from && <div className="notice error">{tr("Cash goes below zero in {0}; the lowest point is {1} in {2}.", { 0: s.cash_negative_from, 1: fmtMoney(s.lowest_cash, cur), 2: s.lowest_cash_month })}</div>}
      <div className="kpis">
        <div className="kpi"><div className="label">{tr("Revenue")}</div><div className="value">{fmtMoney(s.revenue, cur)}</div></div>
        <div className="kpi"><div className="label">{tr("Net profit")}</div><div className={`value ${s.net_profit < 0 ? "neg" : ""}`}>{fmtMoney(s.net_profit, cur)}</div></div>
        <div className="kpi"><div className="label">{tr("Net margin")}</div><div className="value">{s.net_margin == null ? "—" : fmtPercent(s.net_margin, 1)}</div></div>
        <div className="kpi"><div className="label">{tr("Cash at the end")}</div><div className={`value ${s.ending_cash < 0 ? "neg" : ""}`}>{fmtMoney(s.ending_cash, cur)}</div></div>
        <div className="kpi"><div className="label">{tr("Lowest cash")}</div><div className={`value ${s.lowest_cash < 0 ? "neg" : ""}`}>{fmtMoney(s.lowest_cash, cur)}</div><div className="xs muted">{s.lowest_cash_month}</div></div>
      </div>
      {chart && <div className="cc-cols">
        <div><h3 className="h3">{tr("Revenue")}</h3><Chart kind="line" categories={chart.cats} series={chart.rev} format="money" currency={cur} /></div>
        <div><h3 className="h3">{tr("Bank and cash")}</h3><Chart kind="line" categories={chart.cats} series={chart.cash} format="money" currency={cur} /></div>
      </div>}
      {res.reference && <p className="xs muted">{tr("Statistical reference: {0}. On the last {1} months of history its error was {2}. It is shown as a check and does not replace your assumptions.",
        { 0: res.reference.method, 1: res.reference.backtest_months, 2: res.reference.backtest_mape == null ? "—" : fmtPercent(res.reference.backtest_mape, 1) })}</p>}
      <section className="panel panel-pad">
        <h3 className="h3">{tr("Assumptions")}</h3>
        {!draft && <p className="xs muted">{tr("Only a draft can be changed. Make a new version to change these assumptions.")}</p>}
        <fieldset disabled={!draft} style={{ border: 0, padding: 0, margin: 0 }}>
          <table className="ledger assumptions"><tbody>
            {v.lines.map((l: string) => <Assumption key={l} line={l} x={a.lines[l]} lines={v.lines} onChange={(x) => setA({ ...a, lines: { ...a.lines, [l]: x } })} />)}
          </tbody></table>
          <div className="grid2" style={{ marginTop: 12, gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
            {([["dso", N("Days sales outstanding (DSO)")], ["dio", N("Days inventory outstanding (DIO)")], ["dpo", N("Days payables outstanding (DPO)")],
              ["capex", N("Capital spending a month")], ["debt_repayment", N("Debt repayment a month")]] as [string, string][]).map(([k, l]) => (
              <Field key={k} label={tr(l)}><input className="input" type="number" step="any" min={0} value={a[k] ?? 0} onChange={(e) => setA({ ...a, [k]: Number(e.target.value) })} /></Field>))}
          </div>
        </fieldset>
        <p className="xs muted" style={{ marginTop: 8 }}>{tr("Receivables, inventory and payables follow the days above, on the last three months, as in the KPIs. Purchases are taken as cost of sales plus other operating expenses. Other balances stay as they are.")}</p>
      </section>
      <div className="table-wrap">
        <table className="ledger fc-table">
          <thead><tr><th />{res.months.map((m: string) => <th key={m} className="num">{m}</th>)}<th className="num">{tr("Total")}</th></tr></thead>
          <tbody>{rows.map(([label, vals], i) => (
            <tr key={label} className={i >= v.lines.length + 3 ? "balance-row" : ""}><td>{label}</td>
              {vals.map((x, j) => <td key={j} className={`num ${x < 0 ? "neg" : ""}`}>{fmtNumber(x, 0)}</td>)}
              <td className="num">{i < v.lines.length + 3 ? fmtNumber(vals.reduce((p, q) => p + q, 0), 0) : ""}</td></tr>))}</tbody>
        </table>
      </div>
      {v.accuracy && v.accuracy.months.length > 0 && <section className="panel panel-pad">
        <h3 className="h3">{tr("Forecast against actual")}</h3>
        <p className="small">{tr("Revenue error {0} over {1} closed months; on average the forecast was {2} a month.", {
          0: v.accuracy.revenue_mape == null ? "—" : fmtPercent(v.accuracy.revenue_mape, 1), 1: v.accuracy.months.length,
          2: v.accuracy.revenue_bias >= 0 ? tr("{0} above", { 0: fmtMoney(v.accuracy.revenue_bias, cur) }) : tr("{0} below", { 0: fmtMoney(-v.accuracy.revenue_bias, cur) }) })}</p>
        <table className="ledger"><thead><tr><th>{tr("Month")}</th><th className="num">{tr("Forecast revenue")}</th><th className="num">{tr("Actual revenue")}</th>
          <th className="num">{tr("Forecast net profit")}</th><th className="num">{tr("Actual net profit")}</th></tr></thead>
          <tbody>{v.accuracy.months.map((r: any) => <tr key={r.month}><td>{r.month}</td><td className="num">{fmtNumber(r.revenue, 0)}</td><td className="num">{fmtNumber(r.actual_revenue, 0)}</td>
            <td className="num">{fmtNumber(r.net_profit, 0)}</td><td className="num">{fmtNumber(r.actual_net_profit, 0)}</td></tr>)}</tbody></table>
      </section>}
      {(cmp.data || []).length > 1 && <section className="panel panel-pad">
        <h3 className="h3">{tr("Scenarios and versions")}</h3>
        <table className="ledger"><thead><tr><th>{tr("Forecast")}</th><th>{tr("Status")}</th><th className="num">{tr("Revenue")}</th><th className="num">{tr("Net profit")}</th>
          <th className="num">{tr("Cash at the end")}</th><th className="num">{tr("Lowest cash")}</th></tr></thead>
          <tbody>{(cmp.data || []).map((c: any) => <tr key={c.id} className="clickable" onClick={() => go(`analysis/forecasts/${c.id}`)}>
            <td>{c.scenario} v{c.version}{c.id === id ? " ●" : ""}{c.is_plan ? <> <span className="tag blue">{tr("plan")}</span></> : null}</td><td><StatusTagF s={c.status} /></td>
            <td className="num">{fmtMoney(c.revenue, cur)}</td><td className="num">{fmtMoney(c.net_profit, cur)}</td>
            <td className="num">{fmtMoney(c.ending_cash, cur)}</td><td className="num">{fmtMoney(c.lowest_cash, cur)} <span className="xs muted">{c.lowest_cash_month}</span></td></tr>)}</tbody></table>
      </section>}
    </div>
  );
}
