// Cost centres: a project or department followed through the general ledger, the journal and the bank.
// The page states each figure's method and shows the ledger lines behind it.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { del, get, patch, post, put, upload } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtDate, fmtMoney, fmtNumber, fmtPercent } from "../lib/format";
import { Chart } from "../components/charts";
import { Confirm, Empty, ErrorBox, Field, Icon, Loading, Modal, PageHead, go, useAction, useLoad } from "../components/ui";
import { tr } from "../lib/i18n";

type Cond = { tag?: string; accounts?: string[]; parties?: string[]; keywords?: string[]; date_from?: string; date_to?: string };
type Rules = { include: Cond[]; exclude?: Cond[] };

export function CostCentresPage({ id }: { id?: string }): ReactNode {
  return id ? <CostCentreView id={id} /> : <CostCentreList />;
}

// ---- list -----------------------------------------------------------------------------------------
function runsOut(c: any): ReactNode {
  if (c.exceeded_on) return <span className="tag red">{tr("Exceeded on {0}", { 0: fmtDate(c.exceeded_on) })}</span>;
  if (c.run_out_date) return <span className={c.runout_status === "before_end" ? "tag amber" : ""}>{fmtDate(c.run_out_date)}</span>;
  if (c.runout_status === "no_budget") return <span className="muted">{tr("No budget")}</span>;
  return <span className="muted">—</span>;
}

function CostCentreList(): ReactNode {
  const { ws } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, "/cost-centres")), [ws.id]);
  const [creating, setCreating] = useState(false);
  const ledgers: any[] = data.data?.ledgers || [];
  const items: any[] = data.data?.cost_centres || [];
  const cur = ws.base_currency;
  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <PageHead title={tr("Cost centres")}
        sub={tr("Follow a project or department through the general ledger, the journal and the bank: what it cost, what was actually paid, and when its budget runs out at the current pace.")}
        actions={can(ws, "editor") && ledgers.length > 0 &&
          <button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" />{tr("New cost centre")}</button>} />
      <ErrorBox error={data.error} />
      {creating && <Editor ledgers={ledgers} onClose={() => setCreating(false)} onSaved={(cc) => go(`costcentres/${cc.id}`)} />}
      {data.loading && !data.data ? <Loading /> : ledgers.length === 0 ? (
        <div className="panel"><Empty title={tr("No general ledger yet")}>
          {tr("Cost centres read journal lines. Sync QuickBooks Online, Xero or Odoo under Connections, push journal lines from another system through the Open Accounting Connector, or import an Excel or CSV file with the general-ledger columns under Data.")}
        </Empty></div>
      ) : items.length === 0 ? (
        <div className="panel"><Empty title={tr("No cost centres yet")}>
          {tr("A cost centre is a set of rules: a tag in the ledger, accounts, suppliers or words in the description. It works even when the ledger has no cost-centre field.")}
        </Empty></div>
      ) : (
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table className="ledger">
            <thead><tr>
              <th>{tr("Cost centre")}</th><th className="num">{tr("Cost to date")}</th><th className="num">{tr("Budget")}</th>
              <th className="num">{tr("Used")}</th><th className="num">{tr("Cash paid")}</th><th className="num">{tr("Unpaid to suppliers")}</th>
              <th>{tr("Budget runs out")}</th><th className="num">{tr("Untagged")}</th>
            </tr></thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => go(`costcentres/${c.id}`)}>
                  <td><a href={`#/costcentres/${c.id}`} onClick={(e) => e.stopPropagation()}>{c.name}</a>
                    {c.code ? <span className="muted"> · {c.code}</span> : null}
                    {c.status === "closed" ? <> <span className="tag">{tr("closed")}</span></> : null}
                    {c.error ? <div className="xs neg">{c.error}</div> : null}</td>
                  <td className="num">{fmtMoney(c.cost, cur)}</td>
                  <td className="num">{fmtMoney(c.budget, cur)}</td>
                  <td className="num">{c.used_pct == null ? "—" : fmtPercent(c.used_pct / 100, 0)}</td>
                  <td className="num">{fmtMoney(c.cash_paid, cur)}</td>
                  <td className="num">{fmtMoney(c.unpaid, cur)}</td>
                  <td>{runsOut(c)}</td>
                  <td className="num">{c.untagged_share == null ? "—" : fmtPercent(c.untagged_share / 100, 0)}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- one cost centre ------------------------------------------------------------------------------
function CostCentreView({ id }: { id: string }): ReactNode {
  const { ws } = useApp();
  const [asOf, setAsOf] = useState("");
  const cc = useLoad(() => get<any>(wsPath(ws, `/cost-centres/${id}`)), [ws.id, id]);
  const rep = useLoad(() => get<any>(wsPath(ws, `/cost-centres/${id}/report${asOf ? `?as_of=${asOf}` : ""}`)), [ws.id, id, asOf, cc.data?.updated_at]);
  const ledgers = useLoad(() => get<any>(wsPath(ws, "/cost-centres")).then((r) => r.ledgers), [ws.id]);
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [lines, setLines] = useState<string | null>(null);
  const { busy, run } = useAction();
  const c = cc.data;
  const r = rep.data;
  const cur = ws.base_currency;
  const editor = can(ws, "editor");
  async function importBudget(file: File | undefined) {
    if (!file) return;
    const up: any = await run(() => upload(wsPath(ws, "/files/upload"), file));
    if (!up?.file) return;
    const res: any = await run(() => post(wsPath(ws, `/cost-centres/${id}/budget/import`), { file_id: up.file.id }),
      tr("Budget read from {0}", { 0: file.name }));
    if (res) cc.reload();
  }
  if (cc.error) return <div className="page"><ErrorBox error={cc.error} /></div>;
  if (!c) return <div className="page"><Loading /></div>;
  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <p className="small"><a href="#/costcentres">{tr("Cost centres")}</a></p>
      <PageHead title={c.name}
        sub={<>{c.code ? <>{c.code} · </> : null}{c.start_date || c.end_date ? <>{fmtDate(c.start_date)} – {fmtDate(c.end_date)} · </> : null}
          {r ? tr("Ledger “{0}”, figures as of {1}", { 0: r.ledger.name, 1: fmtDate(r.as_of) }) : null}
          {c.status === "closed" ? <> · <span className="tag">{tr("closed")}</span></> : null}</>}
        actions={<>
          <label className="row small" style={{ gap: 6 }}>{tr("As of")}
            <input type="date" className="input" style={{ width: 160 }} value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
          {editor && <label className={`btn ${busy ? "disabled" : ""}`}><Icon name="upload" />{tr("Read budget from a file")}
            <input type="file" hidden accept=".xlsx,.docx" disabled={busy} onChange={(e) => { void importBudget(e.target.files?.[0]); e.target.value = ""; }} /></label>}
          {editor && <button className="btn" onClick={() => setEditing(true)}><Icon name="edit" />{tr("Edit")}</button>}
          {editor && <button className="btn ghost" aria-label={tr("Delete")} onClick={() => setConfirm(true)}><Icon name="trash" /></button>}
        </>} />
      {editing && <Editor ledgers={ledgers.data || []} cc={c} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); cc.reload(); }} />}
      {confirm && <Confirm text={tr("Delete this cost centre? The ledger is not changed.")} onClose={() => setConfirm(false)}
        onYes={() => run(() => del(wsPath(ws, `/cost-centres/${id}`))).then(() => go("costcentres"))} />}
      <ErrorBox error={rep.error} />
      {!r ? <Loading /> : (
        <div className="stack" style={{ gap: 0 }}>
          <div className="kpis">
            <Kpi label={tr("Cost to date")} value={fmtMoney(r.totals.cost, cur)} />
            <Kpi label={tr("Budget")} value={fmtMoney(r.totals.budget, cur)} />
            <Kpi label={tr("Remaining")} value={fmtMoney(r.totals.remaining, cur)} neg={(r.totals.remaining ?? 0) < 0} />
            <Kpi label={tr("Cash paid")} value={fmtMoney(r.cash.cash_paid, cur)} />
            <Kpi label={tr("Unpaid to suppliers")} value={fmtMoney(r.cash.unpaid, cur)} />
            {r.totals.revenue ? <Kpi label={tr("Margin")} value={fmtMoney(r.totals.margin, cur)} neg={r.totals.margin < 0} /> : null}
          </div>
          <RunOut r={r} cur={cur} />
          <Cash r={r} cur={cur} onLines={() => setLines("unassigned_bank")} />
          <BudgetLines c={c} r={r} cur={cur} editor={editor} onSaved={cc.reload} />
          <Tagging c={c} r={r} cur={cur} onLines={setLines} />
        </div>
      )}
      {lines && <LinesModal id={id} kind={lines} asOf={asOf} cur={cur} onClose={() => setLines(null)} />}
    </div>
  );
}

function Kpi({ label, value, neg }: { label: string; value: string; neg?: boolean }): ReactNode {
  return <div className="kpi"><div className="label">{label}</div><div className={`value ${neg ? "neg" : ""}`}>{value}</div></div>;
}

// ---- 6. when the budget runs out ------------------------------------------------------------------
function runoutSentence(ro: any): string {
  switch (ro.status) {
    case "exceeded": return tr("The budget was exceeded on {0}.", { 0: fmtDate(ro.exceeded_on) });
    case "before_end": return tr("At this pace the budget runs out on {0}, {1} days before the planned end.", { 0: fmtDate(ro.run_out_date), 1: ro.days_before_end });
    case "after_end": return tr("At this pace the budget lasts until {0}, after the planned end on {1}.", { 0: fmtDate(ro.run_out_date), 1: fmtDate(ro.end_date) });
    case "open_ended": return tr("At this pace the budget runs out on {0}.", { 0: fmtDate(ro.run_out_date) });
    case "no_spend": return tr("Nothing was spent in the measuring window, so there is no run-out date.");
    case "too_early": return tr("Fewer than 14 days of history; the run-out date appears after two weeks of costs.");
    default: return tr("Set a budget, or read one from your Excel or Word file, to see when it runs out.");
  }
}

function RunOut({ r, cur }: { r: any; cur: string }): ReactNode {
  const ro = r.runout;
  const budget = ro.budget || 0;
  const scale = Math.max(budget, ro.spent, ro.projected_at_end || 0) || 1;
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / scale) * 100))}%`;
  const tone = ro.status === "exceeded" ? "red" : ro.status === "before_end" ? "amber" : "";
  return (
    <section className="cc-section">
      <h2 className="h3">{tr("When the budget runs out")}</h2>
      <p className={`runout ${tone}`}>{runoutSentence(ro)}</p>
      {budget > 0 && (
        <div className="meter" role="img" aria-label={tr("{0} of {1} spent", { 0: fmtMoney(ro.spent, cur), 1: fmtMoney(budget, cur) })}>
          <div className="meter-fill" style={{ width: pct(ro.spent) }} />
          {ro.projected_at_end != null && <div className="meter-proj" style={{ insetInlineStart: pct(ro.spent), width: `calc(${pct(ro.projected_at_end)} - ${pct(ro.spent)})` }} />}
          <div className="meter-budget" style={{ insetInlineStart: pct(budget) }} />
        </div>
      )}
      <div className="row wrap small" style={{ gap: 18, marginTop: 10 }}>
        <span><span className="swatch spent" />{tr("Spent")} {fmtMoney(ro.spent, cur)}</span>
        {ro.projected_at_end != null && <span><span className="swatch proj" />{tr("Projected at the planned end")} {fmtMoney(ro.projected_at_end, cur)}</span>}
        {budget > 0 && <span><span className="swatch budget" />{tr("Budget")} {fmtMoney(budget, cur)}</span>}
        <span className="muted">{tr("Pace")} {fmtMoney(ro.per_month, cur)} {tr("a month")}</span>
      </div>
      <p className="xs muted" style={{ marginTop: 8 }}>
        {tr("Method: the average daily cost from {0} to {1} ({2} days, {3}), applied to what is left of the budget.",
          { 0: fmtDate(ro.window_start), 1: fmtDate(ro.window_end), 2: ro.window_days, 3: fmtMoney(ro.spent_in_window, cur) })}</p>
    </section>
  );
}

// ---- 3. accrual against cash -----------------------------------------------------------------------
function Cash({ r, cur, onLines }: { r: any; cur: string; onLines: () => void }): ReactNode {
  const c = r.cash;
  const rows: [string, number, string?][] = [
    [tr("Paid directly from the bank"), c.paid_direct],
    [tr("Supplier bills, paid"), c.paid_through_payables],
    [tr("Supplier bills, settled without cash"), c.settled_other, tr("credit notes and offsets")],
    [tr("Supplier bills, still unpaid"), c.unpaid],
    [tr("Not cash"), c.not_cash, tr("accruals, depreciation and other entries")],
  ];
  const months = r.monthly || [];
  return (
    <section className="cc-section">
      <h2 className="h3">{tr("Cost against cash")}</h2>
      <p className="small" style={{ color: "var(--ink-2)", maxWidth: "75ch" }}>
        {tr("The ledger says this cost centre cost {0}; {1} of it has left the bank.", { 0: fmtMoney(c.accrual_cost, cur), 1: fmtMoney(c.cash_paid, cur) })}</p>
      <div className="cc-cols">
        <div>
          {months.length > 1 && <Chart kind="line" categories={months.map((m: any) => m.month)} currency={cur} format="money"
            series={[{ name: tr("Cost (cumulative)"), values: months.map((m: any) => m.cumulative_cost) },
              { name: tr("Cash paid (cumulative)"), values: months.map((m: any) => m.cumulative_cash) }]} />}
        </div>
        <table className="ledger">
          <tbody>
            {rows.map(([label, v, note]) => (
              <tr key={label}><td>{label}{note ? <div className="xs muted">{note}</div> : null}</td><td className="num">{fmtMoney(v, cur)}</td></tr>))}
          </tbody>
          <tfoot><tr><td>{tr("Cost to date")}</td><td className="num">{fmtMoney(c.accrual_cost, cur)}</td></tr></tfoot>
        </table>
      </div>
      <p className="xs muted" style={{ marginTop: 8 }}>
        {tr("Method: entries that charge this cost centre and pay from a bank account count as paid directly. Supplier bills are settled oldest first by each supplier's later payments.")}
        {c.supplier_missing ? " " + tr("Some payables lines carry no supplier, so those bills were settled in date order across all suppliers.") : ""}</p>
      {r.unassigned_bank.count > 0 && (
        <div className="notice warn" style={{ marginTop: 10 }}>
          {tr("{0} bank payments ({1}) were not charged to any cost centre but are from this cost centre's suppliers or name it.", { 0: r.unassigned_bank.count, 1: fmtMoney(r.unassigned_bank.amount, cur) })}{" "}
          <button className="btn small" onClick={onLines}>{tr("Review")}</button>
        </div>
      )}
    </section>
  );
}

// ---- 9. budget lines from the customer's file ------------------------------------------------------
function BudgetLines({ c, r, cur, editor, onSaved }: { c: any; r: any; cur: string; editor: boolean; onSaved: () => void }): ReactNode {
  const { ws } = useApp();
  const [edit, setEdit] = useState<any[] | null>(null);
  const accounts = useLoad(() => (edit && c.dataset_id ? get<any>(wsPath(ws, `/cost-centres/ledgers/${c.dataset_id}`)) : Promise.resolve(null)), [edit !== null]);
  const { busy, run } = useAction();
  const b = r.budget;
  const byId: Record<string, any> = Object.fromEntries((c.budget_lines || []).map((x: any) => [x.id, x]));
  if (!b.lines.length) {
    return (
      <section className="cc-section">
        <h2 className="h3">{tr("Budget lines")}</h2>
        <p className="small muted">{tr("Read the budget from the Excel or Word file you already use: a table with a label column and an amount column. Each line keeps the cell it came from and is matched to ledger accounts by code or name.")}</p>
      </section>
    );
  }
  const source = (s: any) => s?.cell ? <a href={`#/files/${s.file_id}`}>{s.cell}</a> : s?.was ? <span title={s.was.cell}>{tr("edited")}</span> : <span>{tr("entered")}</span>;
  return (
    <section className="cc-section">
      <div className="spread"><h2 className="h3">{tr("Budget lines")}</h2>
        {editor && !edit && <button className="btn small" onClick={() => setEdit((c.budget_lines || []).map((x: any) => ({ ...x })))}><Icon name="edit" size={14} />{tr("Match accounts")}</button>}</div>
      {c.budget_file_id && <p className="xs muted">{tr("Read from {0}, version {1}.", { 0: (c.budget_lines[0]?.source?.file_name) || tr("your file"), 1: c.budget_file_version })}</p>}
      <div className="table-wrap" style={{ maxHeight: "none" }}>
        <table className="ledger">
          <thead><tr><th>{tr("Budget line")}</th><th>{tr("Ledger accounts")}</th><th className="num">{tr("Budget")}</th><th className="num">{tr("Actual")}</th>
            <th className="num">{tr("Used")}</th><th>{tr("Runs out")}</th><th>{tr("Source")}</th></tr></thead>
          <tbody>
            {b.lines.map((x: any, i: number) => (
              <tr key={x.id || i}>
                <td>{x.label}</td>
                <td>{edit ? (
                  <select className="select" style={{ minWidth: 200 }} value={edit[i]?.accounts?.[0] || ""} aria-label={tr("Ledger account for {0}", { 0: x.label })}
                    onChange={(e) => setEdit(edit.map((y, j) => j === i ? { ...y, accounts: e.target.value ? [e.target.value] : [] } : y))}>
                    <option value="">{tr("Not matched")}</option>
                    {(accounts.data?.accounts || []).map((a: any) => <option key={`${a.code}|${a.name}`} value={a.code || a.name}>{a.code ? `${a.code} ` : ""}{a.name}</option>)}
                  </select>) : x.accounts.length ? x.accounts.join(", ") : <span className="tag amber">{tr("not matched")}</span>}</td>
                <td className="num">{fmtMoney(x.amount, cur)}</td>
                <td className="num">{fmtMoney(x.actual, cur)}</td>
                <td className="num">{x.used_pct == null ? "—" : fmtPercent(x.used_pct / 100, 0)}</td>
                <td>{x.exceeded_on ? <span className="tag red">{tr("Exceeded on {0}", { 0: fmtDate(x.exceeded_on) })}</span> : x.run_out_date ? fmtDate(x.run_out_date) : "—"}</td>
                <td className="small">{source(byId[x.id]?.source || x.source)}</td>
              </tr>))}
          </tbody>
          <tfoot><tr><td>{tr("Total")}</td><td /><td className="num">{fmtMoney(r.totals.budget, cur)}</td><td className="num">{fmtMoney(r.totals.cost, cur)}</td><td /><td /><td /></tr></tfoot>
        </table>
      </div>
      {b.unbudgeted_cost ? <p className="small" style={{ marginTop: 8 }}>{tr("Cost on accounts outside the budget lines: {0}.", { 0: fmtMoney(b.unbudgeted_cost, cur) })}</p> : null}
      {edit && <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" disabled={busy} onClick={async () => {
          if (await run(() => put(wsPath(ws, `/cost-centres/${c.id}/budget/lines`), { lines: edit.map((y) => ({ id: y.id, label: y.label, amount: y.amount, accounts: y.accounts })) }), tr("Saved"))) { setEdit(null); onSaved(); }
        }}>{tr("Save")}</button>
        <button className="btn" onClick={() => setEdit(null)}>{tr("Cancel")}</button></div>}
    </section>
  );
}

// ---- 1. rules and tagging --------------------------------------------------------------------------
export function describe(cond: Cond): string {
  const bits: string[] = [];
  if (cond.tag) bits.push(tr("tag “{0}”", { 0: cond.tag }));
  if (cond.accounts?.length) bits.push(tr("account {0}", { 0: cond.accounts.join(" / ") }));
  if (cond.parties?.length) bits.push(tr("party {0}", { 0: cond.parties.join(" / ") }));
  if (cond.keywords?.length) bits.push(tr("description contains {0}", { 0: cond.keywords.map((k) => `“${k}”`).join(" / ") }));
  if (cond.date_from || cond.date_to) bits.push(tr("dated {0} to {1}", { 0: cond.date_from ? fmtDate(cond.date_from) : "…", 1: cond.date_to ? fmtDate(cond.date_to) : "…" }));
  return bits.join(tr(" and "));
}

function Tagging({ c, r, cur, onLines }: { c: any; r: any; cur: string; onLines: (k: string) => void }): ReactNode {
  const t = r.totals;
  const tagged = t.cost ? Math.max(0, Math.min(1, t.tagged_cost / t.cost)) : 0;
  return (
    <section className="cc-section">
      <div className="spread"><h2 className="h3">{tr("Rules and tagging")}</h2>
        <button className="btn small" onClick={() => onLines("matched")}>{tr("Ledger lines ({0})", { 0: t.matched_lines })}</button></div>
      {t.cost ? <>
        <div className="split" role="img" aria-label={tr("{0} tagged in the ledger, {1} picked up by rules", { 0: fmtMoney(t.tagged_cost, cur), 1: fmtMoney(t.rule_cost, cur) })}>
          <div className="split-a" style={{ flexBasis: `${tagged * 100}%` }} /><div className="split-b" /></div>
        <div className="row wrap small" style={{ gap: 18, marginTop: 6 }}>
          <span><span className="swatch spent" />{tr("Tagged in the ledger")} {fmtMoney(t.tagged_cost, cur)}</span>
          <span><span className="swatch proj" />{tr("Picked up by account, supplier or keyword rules")} {fmtMoney(t.rule_cost, cur)} ({fmtPercent((t.untagged_share || 0) / 100, 1)})</span>
        </div></> : null}
      <ol className="rules">
        {(c.rules.include || []).map((cond: Cond, i: number) => (
          <li key={i} className="spread"><span>{describe(cond)}</span><span className="num">{fmtMoney(t.by_rule[i]?.cost, cur)}</span></li>))}
      </ol>
      {(c.rules.exclude || []).length > 0 && <p className="small">{tr("Except")}: {(c.rules.exclude || []).map(describe).join("; ")}</p>}
      <p className="xs muted">{tr("Rules version {0}.", { 0: c.rules_version })}{" "}
        {c.versions?.[0]?.changed_by_name ? tr("Last changed by {0}.", { 0: c.versions[0].changed_by_name }) : ""}</p>
      {r.possibly_missing.count > 0 && (
        <div className="notice warn">{tr("{0} cost lines ({1}) are from this cost centre's suppliers or name it, but no cost centre took them.", { 0: r.possibly_missing.count, 1: fmtMoney(r.possibly_missing.amount, cur) })}{" "}
          <button className="btn small" onClick={() => onLines("possibly_missing")}>{tr("Review")}</button></div>)}
      {r.overlaps.count > 0 && (
        <div className="notice error" style={{ marginTop: 8 }}>{tr("{0} lines ({1}) are also counted by: {2}. Narrow one of the rules.", { 0: r.overlaps.count, 1: fmtMoney(r.overlaps.amount, cur), 2: r.overlaps.with.map((w: any) => w.name).join(", ") })}</div>)}
    </section>
  );
}

function LinesModal({ id, kind, asOf, cur, onClose }: { id: string; kind: string; asOf: string; cur: string; onClose: () => void }): ReactNode {
  const { ws } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, `/cost-centres/${id}/lines?kind=${kind}&limit=1000${asOf ? `&as_of=${asOf}` : ""}`)), [id, kind, asOf]);
  const title = kind === "matched" ? tr("Ledger lines in this cost centre") : kind === "possibly_missing" ? tr("Possibly missing") : tr("Bank payments that may belong here");
  const rows: any[] = data.data?.lines || [];
  return (
    <Modal title={title} onClose={onClose} wide>
      <ErrorBox error={data.error} />
      {data.loading && !data.data ? <Loading /> : rows.length === 0 ? <p className="muted">{tr("No lines.")}</p> : (
        <div className="table-wrap" style={{ maxHeight: 520 }}>
          <table className="ledger">
            {kind === "unassigned_bank" ? <>
              <thead><tr><th>{tr("Date")}</th><th>{tr("Entry")}</th><th>{tr("Party")}</th><th>{tr("Description")}</th><th>{tr("Charged to")}</th><th className="num">{tr("Paid out")}</th></tr></thead>
              <tbody>{rows.map((x, i) => <tr key={i}><td>{fmtDate(x.date)}</td><td>{x.entry}</td><td>{x.party || "—"}</td><td>{x.description || "—"}</td><td>{x.accounts.join(", ")}</td><td className="num">{fmtMoney(x.amount, cur)}</td></tr>)}</tbody>
            </> : <>
              <thead><tr><th>{tr("Date")}</th><th>{tr("Entry")}</th><th>{tr("Account")}</th><th>{tr("Party")}</th><th>{tr("Description")}</th>
                <th className="num">{tr("Debit")}</th><th className="num">{tr("Credit")}</th><th>{kind === "matched" ? tr("How") : tr("Why")}</th></tr></thead>
              <tbody>{rows.map((x, i) => (
                <tr key={i}><td>{fmtDate(x.date)}</td><td>{x.entry}</td><td>{x.account_code ? `${x.account_code} ` : ""}{x.account}</td><td>{x.party || "—"}</td>
                  <td>{x.description || "—"}{x.tags ? <div className="xs muted">{x.tags}</div> : null}</td>
                  <td className="num">{x.debit ? fmtNumber(x.debit) : ""}</td><td className="num">{x.credit ? fmtNumber(x.credit) : ""}</td>
                  <td className="small">{kind === "matched" ? <>{x.basis === "tag" ? <span className="tag green">{tr("tag")}</span> : <span className="tag amber">{tr("rule")}</span>}
                    {" "}{tr("rule {0}", { 0: x.rule + 1 })}{x.share < 1 ? ` · ${fmtPercent(x.share, 0)}` : ""}</>
                    : x.reason === "same supplier" ? tr("same supplier") : tr("named in the description")}</td></tr>))}</tbody>
            </>}
          </table>
        </div>
      )}
      {data.data && data.data.total > rows.length && <p className="xs muted">{tr("Showing {0} of {1}.", { 0: rows.length, 1: data.data.total })}</p>}
    </Modal>
  );
}

// ---- editor -----------------------------------------------------------------------------------------
function Chips({ values, onChange, options, label, placeholder }: { values: string[]; onChange: (v: string[]) => void; options?: string[];
  label: string; placeholder?: string }): ReactNode {
  const [text, setText] = useState("");
  const listId = useMemo(() => `dl-${Math.random().toString(36).slice(2)}`, []);
  const add = (v: string) => { const s = v.trim(); if (s && !values.includes(s)) onChange([...values, s]); setText(""); };
  return (
    <div className="chips" aria-label={label}>
      {values.map((v) => <span key={v} className="chip">{v}<button type="button" aria-label={tr("Remove {0}", { 0: v })} onClick={() => onChange(values.filter((x) => x !== v))}>×</button></span>)}
      <input className="chip-input" list={options ? listId : undefined} value={text} placeholder={values.length ? "" : placeholder} aria-label={label}
        onChange={(e) => { const v = e.target.value; if (options?.includes(v)) add(v); else setText(v); }}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(text); } else if (e.key === "Backspace" && !text && values.length) onChange(values.slice(0, -1)); }}
        onBlur={() => text && add(text)} />
      {options && <datalist id={listId}>{options.slice(0, 300).map((o) => <option key={o} value={o} />)}</datalist>}
    </div>
  );
}

function CondEditor({ cond, onChange, onRemove, values }: { cond: Cond; onChange: (c: Cond) => void; onRemove?: () => void; values: any }): ReactNode {
  const accounts = (values?.accounts || []).map((a: any) => a.code || a.name);
  return (
    <div className="cond">
      <div className="grid2">
        <Field label={tr("Tag in the ledger")} hint={tr("A class, tracking category, analytic account or department.")}>
          <input className="input" list="cc-tags" value={cond.tag || ""} onChange={(e) => onChange({ ...cond, tag: e.target.value || undefined })} placeholder={tr("e.g. Project: Riyadh tower")} /></Field>
        <Field label={tr("Accounts")} hint={tr("A code also takes its sub-accounts: 51 takes 5100 and 5150.")}>
          <Chips label={tr("Accounts")} values={cond.accounts || []} options={accounts} onChange={(v) => onChange({ ...cond, accounts: v })} /></Field>
        <Field label={tr("Suppliers or customers")}>
          <Chips label={tr("Suppliers or customers")} values={cond.parties || []} options={values?.parties} onChange={(v) => onChange({ ...cond, parties: v })} /></Field>
        <Field label={tr("Words in the description")}>
          <Chips label={tr("Words in the description")} values={cond.keywords || []} onChange={(v) => onChange({ ...cond, keywords: v })} placeholder={tr("type and press Enter")} /></Field>
        <Field label={tr("From")}><input type="date" className="input" value={cond.date_from || ""} onChange={(e) => onChange({ ...cond, date_from: e.target.value || undefined })} /></Field>
        <Field label={tr("To")}><input type="date" className="input" value={cond.date_to || ""} onChange={(e) => onChange({ ...cond, date_to: e.target.value || undefined })} /></Field>
      </div>
      <div className="spread"><span className="xs muted">{tr("All the filled-in parts must match.")}</span>
        {onRemove && <button type="button" className="btn small ghost" onClick={onRemove}><Icon name="trash" size={14} />{tr("Remove")}</button>}</div>
    </div>
  );
}

function clean(c: Cond): Cond {
  const out: Cond = {};
  if (c.tag?.trim()) out.tag = c.tag.trim();
  for (const k of ["accounts", "parties", "keywords"] as const) if (c[k]?.length) out[k] = c[k];
  if (c.date_from) out.date_from = c.date_from;
  if (c.date_to) out.date_to = c.date_to;
  return out;
}
const filled = (c: Cond) => !!(c.tag || c.accounts?.length || c.parties?.length || c.keywords?.length);

function Editor({ ledgers, cc, onClose, onSaved }: { ledgers: any[]; cc?: any; onClose: () => void; onSaved: (cc: any) => void }): ReactNode {
  const { ws } = useApp();
  const [f, setF] = useState(() => ({
    name: cc?.name || "", code: cc?.code || "", dataset_id: cc?.dataset_id || ledgers[0]?.id || "", start_date: cc?.start_date || "",
    end_date: cc?.end_date || "", budget_amount: cc?.budget_amount ?? "", burn_months: cc?.burn_months || 3,
    include_assets: !!cc?.include_assets, status: cc?.status || "active", note: "",
  }));
  const [rules, setRules] = useState<Rules>(() => cc?.rules || { include: [{}], exclude: [] });
  const values = useLoad(() => (f.dataset_id ? get<any>(wsPath(ws, `/cost-centres/ledgers/${f.dataset_id}`)) : Promise.resolve(null)), [f.dataset_id]);
  const [preview, setPreview] = useState<any>(null);
  const { busy, run } = useAction();
  const body = { include: rules.include.filter(filled).map(clean), exclude: (rules.exclude || []).filter(filled).map(clean) };
  const key = JSON.stringify([body, f.dataset_id, f.include_assets, f.start_date]);
  useEffect(() => {
    if (!f.dataset_id || !body.include.length) { setPreview(null); return undefined; }
    const t = setTimeout(() => {
      post(wsPath(ws, "/cost-centres/preview"), { dataset_id: f.dataset_id, rules: body, include_assets: f.include_assets, start_date: f.start_date || null })
        .then(setPreview).catch(() => setPreview(null));
    }, 400);
    return () => clearTimeout(t);
  }, [key]);
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const setCond = (which: "include" | "exclude", i: number, c: Cond | null) => {
    const list = [...(rules[which] || [])];
    if (c) list[i] = c; else list.splice(i, 1);
    setRules({ ...rules, [which]: list });
  };
  async function save() {
    const payload: any = { name: f.name.trim(), code: f.code.trim() || null, dataset_id: f.dataset_id, start_date: f.start_date || null,
      end_date: f.end_date || null, budget_amount: f.budget_amount === "" ? null : Number(f.budget_amount), burn_months: Number(f.burn_months),
      include_assets: f.include_assets, rules: body };
    const r: any = cc ? await run(() => patch(wsPath(ws, `/cost-centres/${cc.id}`), { ...payload, status: f.status, note: f.note || null }), tr("Saved"))
      : await run(() => post(wsPath(ws, "/cost-centres"), payload), tr("Cost centre created"));
    if (r) onSaved(r);
  }
  const cur = ws.base_currency;
  return (
    <Modal title={cc ? tr("Edit cost centre") : tr("New cost centre")} onClose={onClose} wide
      footer={<>
        <span className="grow small">{preview ? tr("These rules pick up {0} ledger lines: cost {1}, {2} of it untagged.",
          { 0: preview.matched_lines, 1: fmtMoney(preview.cost, cur), 2: fmtPercent((preview.untagged_share || 0) / 100, 0) }) : ""}</span>
        <button className="btn" onClick={onClose}>{tr("Cancel")}</button>
        <button className="btn primary" disabled={busy || !f.name.trim() || !f.dataset_id || !body.include.length} onClick={save}>{tr("Save")}</button></>}>
      <datalist id="cc-tags">{(values.data?.tags || []).map((t: string) => <option key={t} value={t} />)}</datalist>
      <div className="grid2">
        <Field label={tr("Name")}><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder={tr("e.g. Riyadh tower")} /></Field>
        <Field label={tr("Code")}><input className="input" value={f.code} onChange={(e) => set("code", e.target.value)} placeholder="PRJ-07" /></Field>
        <Field label={tr("General ledger")}>
          <select className="select" value={f.dataset_id} onChange={(e) => set("dataset_id", e.target.value)}>
            {ledgers.map((l) => <option key={l.id} value={l.id}>{l.name} ({fmtNumber(l.row_count, 0)})</option>)}</select></Field>
        <Field label={tr("Budget")} hint={tr("Leave empty when you read the budget from a file.")}>
          <input className="input" type="number" min={0} step="0.01" value={f.budget_amount} onChange={(e) => set("budget_amount", e.target.value)} /></Field>
        <Field label={tr("Starts")}><input type="date" className="input" value={f.start_date} onChange={(e) => set("start_date", e.target.value)} /></Field>
        <Field label={tr("Planned end")}><input type="date" className="input" value={f.end_date} onChange={(e) => set("end_date", e.target.value)} /></Field>
        <Field label={tr("Pace measured over")}>
          <select className="select" value={f.burn_months} onChange={(e) => set("burn_months", e.target.value)}>
            {[1, 2, 3, 6, 12].map((n) => <option key={n} value={n}>{n === 1 ? tr("the last month") : tr("the last {0} months", { 0: n })}</option>)}</select></Field>
        <div className="stack" style={{ alignSelf: "end" }}>
          <label className="check small"><input type="checkbox" checked={f.include_assets} onChange={(e) => set("include_assets", e.target.checked)} />
            <span>{tr("Count capitalised cost (asset accounts) as cost")}</span></label>
          {cc && <label className="check small"><input type="checkbox" checked={f.status === "closed"} onChange={(e) => set("status", e.target.checked ? "closed" : "active")} />
            <span>{tr("Closed")}</span></label>}
        </div>
      </div>
      <h3 className="h3" style={{ marginTop: 18 }}>{tr("Take a ledger line when any of these match")}</h3>
      {rules.include.map((c, i) => <CondEditor key={i} cond={c} values={values.data} onChange={(x) => setCond("include", i, x)}
        onRemove={rules.include.length > 1 ? () => setCond("include", i, null) : undefined} />)}
      <button type="button" className="btn small" onClick={() => setRules({ ...rules, include: [...rules.include, {}] })}><Icon name="plus" size={14} />{tr("Add a rule")}</button>
      <h3 className="h3" style={{ marginTop: 18 }}>{tr("Except when any of these match")}</h3>
      {(rules.exclude || []).map((c, i) => <CondEditor key={i} cond={c} values={values.data} onChange={(x) => setCond("exclude", i, x)} onRemove={() => setCond("exclude", i, null)} />)}
      <button type="button" className="btn small" onClick={() => setRules({ ...rules, exclude: [...(rules.exclude || []), {}] })}><Icon name="plus" size={14} />{tr("Add an exception")}</button>
      {preview?.by_rule?.length > 1 && <ol className="rules xs">{preview.by_rule.map((x: any, i: number) => <li key={i} className="spread"><span>{tr("Rule {0}", { 0: i + 1 })}</span><span className="num">{fmtMoney(x.cost, cur)}</span></li>)}</ol>}
      {cc && <Field label={tr("What changed (kept in the rules history)")}><input className="input" value={f.note} onChange={(e) => set("note", e.target.value)} /></Field>}
    </Modal>
  );
}
