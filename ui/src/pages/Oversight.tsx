// The accounting manager's desk: what is reconciled, what is not matched, what waits for a decision, what is wrong,
// who did what, how the reporting is progressing, and how the operation is running. Read-only; every item opens the
// page where the work is done.
import { type ReactNode } from "react";
import { get } from "../lib/api";
import { useApp, wsPath } from "../lib/app";
import { fmtDate, fmtDateTime, fmtMoney, fmtNumber, fmtPercent, fmtRelative } from "../lib/format";
import { ErrorBox, Loading, PageHead, useLoad } from "../components/ui";
import { N, lang, tr } from "../lib/i18n";

const CLOSE_STATUS: Record<string, string> = { none: N("Not started"), open: N("In progress"), closed: N("Closed") };
const ITEM_KIND: Record<string, string> = {
  accrual: N("accruals to decide"), depreciation: N("depreciation entries to decide"), bank_fee: N("bank charges to decide"),
  bank_interest: N("interest entries to decide"), bank_unknown: N("bank items to review"), variance: N("variances to explain"),
  unbilled: N("customers not invoiced"),
};
const CHECK: Record<string, string> = {
  unbalanced_entries: N("Entries that do not balance"), single_line_entries: N("Single-line entries"), no_account: N("Lines without an account"),
  no_date: N("Lines without a usable date"), future_date: N("Lines dated in the future"), debit_and_credit: N("Lines with both a debit and a credit"),
  zero_lines: N("Zero lines"), suspense_balances: N("Balances on suspense or clearing accounts"), unconfirmed_roles: N("Accounts whose role is still a suggestion"),
  unexpected_balance_sign: N("Balances on the wrong side"), contra_postings: N("Postings on the unusual side"),
};
const ANOMALY: Record<string, string> = {
  unusual_amount: N("Unusual amount"), missing_posting: N("Nothing posted"), new_account: N("New account"), possible_duplicate: N("Possible duplicate"),
};
const ACTION: Record<string, string> = {
  "dataset.import": N("imported a ledger"), "close.prepare": N("prepared a close"), "close.item": N("decided a close item"),
  "close.statement": N("added a bank statement"), "close.closed": N("closed a month"), "close.reopened": N("reopened a month"),
  "account_roles.update": N("confirmed account roles"), "forecast.create": N("created a forecast"), "forecast.plan": N("made a forecast the plan"),
  "document.add": N("added a document"), "file.create": N("created a file"), "file.edit": N("edited a file"), "file.review": N("reviewed a file"),
  "memory.save": N("saved a memory"), "tool.run": N("ran a tool"), "user.login": N("signed in"), "workspace.create": N("created the workspace"),
  "cost_centre.create": N("created a cost centre"), "cost_centre.budget_import": N("read a budget file"), "kpi.update": N("changed a KPI"),
  "connection.sync": N("synced a connection"), "connection.connected": N("connected a system"), "member.add": N("added a member"),
};

function monthName(m: string): string {
  return new Intl.DateTimeFormat(lang() === "en" ? navigator.language || "en" : lang(), { month: "long", year: "numeric" }).format(new Date(`${m}-01T00:00:00`));
}

export function OversightPage(): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, "/oversight")), [ws.id]);
  if (loading && !data) return <Loading />;
  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  const o = data;
  const cur = o.currency;
  const open = o.open_close_items.reduce((s: number, x: any) => s + x.count, 0);
  const todo = o.approvals.length + o.forecasts.length + open + o.workflow_runs.length;
  const vx = o.exceptions.validation;
  const ax = o.exceptions.anomalies;
  const level = todo + (vx?.issues || 0) === 0 ? "strong" : (vx?.checks || []).some((c: any) => c.level === "high") || o.workflow_runs.length ? "at_risk" : "watch";
  return (
    <div className="page exec">
      <PageHead title={tr("Oversight")} sub={o.ledger ? tr("The accounting operation over the ledger “{0}”, {1} lines, refreshed {2}.", { 0: o.ledger.name, 1: fmtNumber(o.ledger.lines, 0), 2: fmtRelative(o.ledger.refreshed_at) })
        : tr("No general ledger yet. Connect a system or import a file under Data.")} />

      {/* what needs attention right now */}
      <section className={`health ${level}`} aria-label={tr("Outstanding work")}>
        <div className="health-score"><div className="health-label">{tr("Outstanding")}</div><div className="health-number num">{todo}</div></div>
        <div className="health-text">
          <p className="health-lead">{todo === 0 ? tr("Nothing is waiting on the team.") : tr("{0} items wait for someone on the team.", { 0: todo })}</p>
          <ul className="health-reasons">
            {o.approvals.length > 0 && <li><a href="#/inbox">{tr("{0} approvals pending", { 0: o.approvals.length })}</a></li>}
            {o.open_close_items.map((x: any) => <li key={`${x.kind}-${x.status}`}><a href="#/close">{x.count} {tr(ITEM_KIND[x.kind] || x.kind)}</a></li>)}
            {o.forecasts.length > 0 && <li><a href="#/analysis/forecasts">{tr("{0} forecasts waiting for sign-off", { 0: o.forecasts.length })}</a></li>}
            {o.workflow_runs.length > 0 && <li><a href="#/workflows">{tr("{0} workflow runs failed or waiting", { 0: o.workflow_runs.length })}</a></li>}
            {vx && vx.issues > 0 && <li><a href="#/ledger/validation">{tr("{0} validation issues in {1}", { 0: vx.issues, 1: monthName(o.month) })}</a></li>}
          </ul>
        </div>
        <div className="health-prov xs muted">{tr("Every line opens the page where the work is done.")}</div>
      </section>

      <div className="exec-cols" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)" }}>
        {/* reconciliation status and reporting progress */}
        <section className="panel panel-pad" aria-label={tr("Reconciliation status")}>
          <div className="spread"><h2 className="h3">{tr("Reconciliation and close, last six months")}</h2><a className="small" href="#/close">{tr("Month-end close")}</a></div>
          <table className="ledger">
            <thead><tr><th>{tr("Month")}</th><th>{tr("Status")}</th><th className="num">{tr("Ready")}</th><th className="num">{tr("Open items")}</th><th>{tr("Bank")}</th><th>{tr("Closed by")}</th></tr></thead>
            <tbody>
              {o.reconciliation.map((r: any) => (
                <tr key={r.month} className={r.run_id ? "clickable" : ""} onClick={() => r.run_id && (location.hash = `#/close/${r.run_id}`)}>
                  <td>{r.run_id ? <a href={`#/close/${r.run_id}`} onClick={(e) => e.stopPropagation()}>{monthName(r.month)}</a> : monthName(r.month)}</td>
                  <td><span className={`tag ${r.status === "closed" ? "green" : r.status === "open" ? "amber" : ""}`}>{tr(CLOSE_STATUS[r.status] || r.status)}</span></td>
                  <td className="num">{r.score === null ? "—" : `${r.score}%`}</td>
                  <td className="num">{r.open_items ?? "—"}</td>
                  <td className="small">{!r.run_id ? "—" : !r.statement ? <span className="muted">{tr("no statement")}</span>
                    : <>{tr("{0} of {1} matched", { 0: r.matched, 1: r.statement_lines })}{(r.statement_only || r.ledger_only) ? <span className="neg"> · {tr("{0} unmatched", { 0: r.statement_only + r.ledger_only })}</span> : ""}
                      {r.unexplained !== null && r.unexplained !== undefined && Math.abs(r.unexplained) >= 0.01 && <span className="neg"> · {tr("{0} unexplained", { 0: fmtMoney(r.unexplained, cur) })}</span>}</>}</td>
                  <td className="small muted">{r.closed_by ? `${r.closed_by} · ${fmtDate(r.closed_at)}` : r.prepared_at ? tr("prepared {0}", { 0: fmtRelative(r.prepared_at) }) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="xs muted" style={{ marginTop: 8 }}>{tr("{0} closed, {1} in progress, {2} not started. Average {3} days after the month end to prepare a close.", {
            0: o.operations.months_closed, 1: o.operations.months_open, 2: o.operations.months_not_started,
            3: o.operations.avg_days_to_prepare === null ? "—" : fmtNumber(o.operations.avg_days_to_prepare, 1) })}</p>
        </section>

        {/* unmatched bank items */}
        <section className="panel panel-pad" aria-label={tr("Unmatched transactions")}>
          <h2 className="h3">{tr("Unmatched bank items")}</h2>
          {!o.unmatched ? <p className="muted small">{tr("No bank statement has been matched yet. Add one to a month-end close.")}</p> : <>
            <p className="small muted">{tr("{0}: {1} of {2} statement lines matched to the ledger.", { 0: monthName(o.unmatched.month), 1: o.unmatched.matched, 2: o.unmatched.statement_lines })}
              {" "}<a href={`#/close/${o.unmatched.run_id}`}>{tr("Open the close")}</a></p>
            {o.unmatched.statement_only.length > 0 && <><div className="small" style={{ marginTop: 8 }}><b>{tr("On the statement, not in the ledger")}</b> <span className="muted">{fmtMoney(o.unmatched.statement_only_total, cur)}</span></div>
              <ul className="plain small">{o.unmatched.statement_only.slice(0, 6).map((s: any, i: number) => <li key={i} className="spread"><span>{fmtDate(s.date)} · {s.description || "—"}</span><span className="num">{fmtMoney(s.amount, cur)}</span></li>)}
                {o.unmatched.statement_only.length > 6 && <li className="muted xs">{tr("and {0} more", { 0: o.unmatched.statement_only.length - 6 })}</li>}</ul></>}
            {o.unmatched.ledger_only.length > 0 && <><div className="small" style={{ marginTop: 8 }}><b>{tr("In the ledger, not on the statement")}</b> <span className="muted">{fmtMoney(o.unmatched.ledger_only_total, cur)}</span></div>
              <ul className="plain small">{o.unmatched.ledger_only.slice(0, 6).map((s: any, i: number) => <li key={i} className="spread"><span>{fmtDate(s.date)} · {s.entry} · {s.description || s.party || "—"}</span><span className="num">{fmtMoney(s.amount, cur)}</span></li>)}
                {o.unmatched.ledger_only.length > 6 && <li className="muted xs">{tr("and {0} more", { 0: o.unmatched.ledger_only.length - 6 })}</li>}</ul></>}
            {o.unmatched.statement_only.length + o.unmatched.ledger_only.length === 0 && <p className="small pos">{tr("Everything on the statement is in the ledger and the other way round.")}</p>}
          </>}
        </section>
      </div>

      <div className="exec-cols three">
        {/* approvals and decisions */}
        <section className="panel panel-pad" aria-label={tr("Pending approvals")}>
          <div className="spread"><h2 className="h3">{tr("Pending approvals")}</h2><a className="small" href="#/inbox">{tr("Inbox")}</a></div>
          {o.approvals.length === 0 ? <p className="muted small">{tr("Nothing waits for approval.")}</p> : (
            <ul className="exec-list">{o.approvals.map((a: any) => <li key={a.id}><a href="#/inbox">{a.summary}<div className="xs muted">{a.by || "—"} · {fmtRelative(a.at)}</div></a></li>)}</ul>)}
          {o.forecasts.length > 0 && <ul className="exec-list" style={{ marginTop: 8 }}>{o.forecasts.map((f: any) => <li key={f.id}><a href={`#/analysis/forecasts/${f.id}`}><span className="tag amber">{tr("forecast")}</span> {f.name} · {f.scenario} v{f.version}</a></li>)}</ul>}
          {o.workflow_runs.length > 0 && <ul className="exec-list" style={{ marginTop: 8 }}>{o.workflow_runs.map((r: any) => <li key={r.id}><a href="#/workflows"><span className="tag red">{r.status}</span> {r.workflow} · {fmtRelative(r.at)}</a></li>)}</ul>}
        </section>

        {/* exceptions */}
        <section className="panel panel-pad" aria-label={tr("Accounting exceptions")}>
          <div className="spread"><h2 className="h3">{tr("Exceptions in {0}", { 0: o.month ? monthName(o.month) : "—" })}</h2><a className="small" href="#/ledger/validation">{tr("Validation")}</a></div>
          {!vx ? <p className="muted small">{tr("No ledger yet.")}</p> : <>
            {vx.checks.length === 0 ? <p className="small pos">{tr("The month validates clean.")}</p> : (
              <ul className="exec-list">{vx.checks.map((c: any) => <li key={c.kind} className={`exec-risk ${c.level === "high" ? "high" : ""}`}><a href={`#/ledger/validation?month=${o.month}`}><span className="spread"><span>{tr(CHECK[c.kind] || c.kind)}</span><b className="num">{c.count}</b></span></a></li>)}</ul>)}
            {ax && ax.count > 0 && <>
              <div className="small" style={{ marginTop: 10 }}><b>{tr("Anomalies")}</b> <span className="muted">{ax.count}</span> · <a href={`#/finance/anomalies?month=${o.month}`}>{tr("See all")}</a></div>
              <ul className="plain small">{ax.items.map((x: any, i: number) => <li key={i}><span className="tag amber">{tr(ANOMALY[x.kind])}</span> {x.account}</li>)}</ul></>}
          </>}
        </section>

        {/* team activity */}
        <section className="panel panel-pad" aria-label={tr("Team activity")}>
          <div className="spread"><h2 className="h3">{tr("Team, last 14 days")}</h2><a className="small" href="#/settings/audit">{tr("Audit log")}</a></div>
          {o.team.length === 0 ? <p className="muted small">{tr("No activity in the last two weeks.")}</p> : (
            <ul className="exec-list">{o.team.map((p: any) => <li key={p.name}><div className="spread"><b>{p.name}</b><span className="num muted">{tr("{0} actions", { 0: p.actions })}</span></div>
              <div className="xs muted">{p.top.map((t: any) => `${t.n}× ${ACTION[t.action] ? tr(ACTION[t.action]) : t.action}`).join(" · ")} · {tr("last {0}", { 0: fmtRelative(p.last_at) })}</div></li>)}</ul>)}
          <div className="small" style={{ marginTop: 10 }}><b>{tr("Latest")}</b></div>
          <ul className="plain xs muted">{o.recent.slice(0, 8).map((r: any, i: number) => <li key={i}>{fmtDateTime(r.at)} · {r.by || "—"} {ACTION[r.action] ? tr(ACTION[r.action]) : r.action}</li>)}</ul>
        </section>
      </div>

      <div className="exec-cols three">
        {/* documents */}
        <section className="panel panel-pad" aria-label={tr("Documents")}>
          <div className="spread"><h2 className="h3">{tr("Supporting documents")}</h2><a className="small" href="#/documents">{tr("Documents")}</a></div>
          <div className="exec-tile-value num">{fmtNumber(o.documents.total, 0)}</div>
          <p className="small muted">{tr("{0} searchable, {1} failed to read.", { 0: fmtNumber(o.documents.ready, 0), 1: fmtNumber(o.documents.failed, 0) })}</p>
          <p className="xs muted">{tr("Documents are not yet linked to journal entries, so every entry counts as missing its evidence until that link exists. Keep invoices, bills and statements under Documents so the assistant and the close can cite them.")}</p>
        </section>

        {/* operational performance */}
        <section className="panel panel-pad" aria-label={tr("Operational performance")}>
          <h2 className="h3">{tr("Operation")}</h2>
          <ul className="exec-kpis" style={{ marginTop: 0 }}>
            <li className="exec-kpi"><div className="exec-kpi-head"><span>{tr("Journal entries last month")}</span><b className="num">{fmtNumber(o.operations.entries_last_month, 0)}</b></div></li>
            <li className="exec-kpi"><div className="exec-kpi-head"><span>{tr("Ledger lines")}</span><b className="num">{o.operations.lines === null || o.operations.lines === undefined ? "—" : fmtNumber(o.operations.lines, 0)}</b></div>
              <div className="exec-kpi-foot muted xs">{o.operations.refreshed_at ? tr("refreshed {0}", { 0: fmtRelative(o.operations.refreshed_at) }) : ""}</div></li>
            <li className="exec-kpi"><div className="exec-kpi-head"><span>{tr("Days to prepare a close")}</span><b className="num">{o.operations.avg_days_to_prepare === null ? "—" : fmtNumber(o.operations.avg_days_to_prepare, 1)}</b></div>
              <div className="exec-kpi-foot muted xs">{tr("Average over the last six months, counted from the month end.")}</div></li>
            <li className="exec-kpi"><div className="exec-kpi-head"><span>{tr("Months closed")}</span><b className="num">{o.operations.months_closed} / 6</b></div></li>
            {ax && <li className="exec-kpi"><div className="exec-kpi-head"><span>{tr("Anomaly rate")}</span><b className="num">{o.operations.entries_last_month ? fmtPercent(ax.count / o.operations.entries_last_month, 1) : "—"}</b></div>
              <div className="exec-kpi-foot muted xs">{tr("Anomalies per journal entry last month.")}</div></li>}
          </ul>
        </section>

        {/* reporting progress */}
        <section className="panel panel-pad" aria-label={tr("Financial reporting progress")}>
          <h2 className="h3">{tr("Reporting")}</h2>
          <ul className="exec-list">
            <li><a href="#/finance/statements">{tr("Statements")}<div className="xs muted">{o.month ? tr("Up to {0}, from the ledger as it stands.", { 0: monthName(o.month) }) : "—"}</div></a></li>
            <li><a href="#/finance/budget">{tr("Budget vs actual")}<div className="xs muted">{tr("Needs an approved plan.")}</div></a></li>
            <li><a href="#/analysis/forecasts">{tr("Forecasts")}<div className="xs muted">{o.forecasts.length ? tr("{0} waiting for sign-off", { 0: o.forecasts.length }) : tr("None waiting.")}</div></a></li>
            <li><a href="#/close">{tr("Close packs")}<div className="xs muted">{tr("{0} of the last six months closed.", { 0: o.operations.months_closed })}</div></a></li>
          </ul>
        </section>
      </div>
    </div>
  );
}
