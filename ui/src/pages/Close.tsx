// Month-end close: what the ledger is missing, the bank against the statement, what moved, and what is left before
// the month can be closed. Proposals are decided here; nothing is posted to the accounting system.
import { useEffect, useState, type ReactNode } from "react";
import { del, download, get, post, put, upload, patch } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtDate, fmtMoney, fmtNumber, fmtRelative } from "../lib/format";
import { Confirm, Empty, ErrorBox, Field, Icon, Loading, PageHead, go, useAction, useLoad, useToast } from "../components/ui";
import { N, lang, locale, tr } from "../lib/i18n";

const ENTRY_KINDS = ["accrual", "depreciation", "bank_fee", "bank_interest"];
/** Status words shown on items (listed so they are translated). */
export const CLOSE_WORDS = [N("proposed"), N("approved"), N("rejected"), N("resolved"), N("open"), N("explained"),
  N("reviewed"), N("closed"), N("done"), N("check"), N("to do"), N("Explain"), N("Mark reviewed")];

export function fmtMonth(m?: string | null): string {
  if (!m) return "—";
  try {
    return new Date(`${m}-15T00:00:00`).toLocaleDateString(locale().startsWith("en") ? undefined : locale(), { month: "long", year: "numeric" });
  } catch { return m; }
}

export function ClosePage({ id }: { id?: string }): ReactNode {
  return id ? <RunView id={id} /> : <RunList />;
}

// ---- list ---------------------------------------------------------------------------------------------------------
function Score({ value }: { value: number | null | undefined }): ReactNode {
  if (value == null) return <span className="muted">—</span>;
  const tone = value >= 100 ? "ok" : value >= 60 ? "mid" : "low";
  return (
    <span className="score-inline" aria-label={tr("{0}% ready", { 0: value })}>
      <span className={`score-bar ${tone}`}><span style={{ width: `${Math.max(3, value)}%` }} /></span>
      <span className="num">{value}%</span>
    </span>
  );
}

function RunList(): ReactNode {
  const { ws } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, "/close")), [ws.id]);
  const [f, setF] = useState<{ dataset_id: string; month: string }>({ dataset_id: "", month: "" });
  const { busy, run } = useAction();
  const editor = can(ws, "editor");
  const ledgers: any[] = data.data?.ledgers || [];
  useEffect(() => {
    if (data.data?.suggested && !f.month) setF({ dataset_id: data.data.suggested.dataset_id, month: data.data.suggested.month || "" });
  }, [data.data]);
  async function prepare() {
    const r: any = await run(() => post(wsPath(ws, "/close"), { month: f.month || null, dataset_id: f.dataset_id || null }),
      tr("Close prepared"));
    if (r) go(`close/${r.id}`);
  }
  async function overnight() {
    const r: any = await run(() => post(wsPath(ws, "/workflows/from-template"), { key: "close_overnight", lang: lang() }),
      tr("Saved as a draft"));
    if (r) go(`workflows/${r.id}`);
  }
  const runs: any[] = data.data?.runs || [];
  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <PageHead title={tr("Month-end close")}
        sub={tr("Prepared from the general ledger: costs that are missing this month, the bank against its statement, what moved more than usual, and what is left before the month can be closed. Nothing is posted to your accounting system.")} />
      <ErrorBox error={data.error} />
      {data.loading && !data.data ? <Loading /> : ledgers.length === 0 ? (
        <div className="panel"><Empty title={tr("No general ledger yet")}>
          {tr("The close reads journal lines. Upload the general ledger template under Cost centres, or sync QuickBooks Online, Xero or Odoo under Connections.")}
        </Empty></div>
      ) : (
        <div className="stack" style={{ gap: 16 }}>
          {editor && (
            <div className="close-start">
              <div className="stack" style={{ gap: 10 }}>
                <h2 className="h3" style={{ margin: 0 }}>{tr("Prepare a month")}</h2>
                <div className="row wrap" style={{ alignItems: "end" }}>
                  {ledgers.length > 1 && <Field label={tr("General ledger")}>
                    <select className="select" value={f.dataset_id} onChange={(e) => setF({ ...f, dataset_id: e.target.value })}>
                      {ledgers.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></Field>}
                  <Field label={tr("Month")}><input type="month" className="input" value={f.month}
                    onChange={(e) => setF({ ...f, month: e.target.value })} /></Field>
                  <button className="btn primary" disabled={busy || !f.month} onClick={prepare}><Icon name="play" />{tr("Prepare the close")}</button>
                </div>
              </div>
              <div className="close-night">
                <h2 className="h3" style={{ margin: 0 }}>{tr("Close while you sleep")}</h2>
                <p className="small" style={{ margin: 0 }}>{tr("A workflow prepares last month's close at 2 a.m. on the 1st and tells the finance team in the morning. It is saved as a draft for you to switch on.")}</p>
                <div><button className="btn small" disabled={busy} onClick={overnight}><Icon name="flow" size={14} />{tr("Set it up")}</button></div>
              </div>
            </div>
          )}
          {runs.length === 0 ? (
            <div className="panel"><Empty title={tr("No month prepared yet")}>{tr("Choose a month and prepare it: the checks take a few seconds.")}</Empty></div>
          ) : (
            <div className="table-wrap" style={{ maxHeight: "none" }}>
              <table className="ledger">
                <thead><tr><th>{tr("Month")}</th><th>{tr("General ledger")}</th><th>{tr("Ready")}</th><th className="num">{tr("To decide")}</th>
                  <th>{tr("Status")}</th><th>{tr("Prepared")}</th></tr></thead>
                <tbody>{runs.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => go(`close/${r.id}`)}>
                    <td><a href={`#/close/${r.id}`} onClick={(e) => e.stopPropagation()}>{fmtMonth(r.month)}</a></td>
                    <td>{r.ledger_name}</td>
                    <td><Score value={r.score} /></td>
                    <td className="num">{r.open_items ?? "—"}</td>
                    <td>{r.status === "closed" ? <span className="tag green">{tr("closed")}</span> : <span className="tag">{tr("open")}</span>}</td>
                    <td className="small">{r.prepared_at ? fmtRelative(r.prepared_at) : "—"}{r.prepared_via === "workflow" ? <> · <span className="tag blue">{tr("overnight")}</span></> : null}</td>
                  </tr>))}</tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- one month -----------------------------------------------------------------------------------------------------
function checkText(c: any): string {
  const d = c.data || {};
  switch (c.key) {
    case "balanced": return d.unbalanced ? tr("{0} entries do not balance: {1}.", { 0: d.unbalanced, 1: (d.examples || []).join(", ") })
      : tr("{0} entries and {1} lines; debits and credits agree.", { 0: fmtNumber(d.entries, 0), 1: fmtNumber(d.lines, 0) });
    case "roles": return d.count ? tr("{0} accounts used this month have a suggested role only.", { 0: d.count }) : tr("Every account used this month has a confirmed role.");
    case "entries": return d.open ? tr("{0} of {1} proposed entries wait for a decision.", { 0: d.open, 1: d.total })
      : d.total ? tr("All {0} proposed entries decided.", { 0: d.total }) : tr("Nothing regular is missing.");
    case "bank":
      switch (d.state) {
        case "none": return tr("Add the month's bank statement to match it to the ledger.");
        case "no_balance": return tr("{0} of {1} statement lines matched. Add the closing balance to finish the reconciliation.", { 0: d.matched, 1: d.lines });
        case "difference": return tr("Unexplained difference of {0} after the reconciling items.", { 0: fmtNumber(d.difference) });
        case "open": return tr("Reconciles; {0} statement items still to decide or review.", { 0: d.open });
        default: return tr("Reconciled: {0} lines matched, {1} in the ledger not yet on the statement.", { 0: d.matched, 1: d.outstanding });
      }
    case "variances": return d.open ? tr("{0} of {1} variances above {2} need an explanation.", { 0: d.open, 1: d.total, 2: fmtNumber(d.materiality) })
      : d.total ? tr("All {0} variances explained.", { 0: d.total }) : tr("No variance above {0}.", { 0: fmtNumber(d.materiality) });
    case "unbilled": return d.open ? tr("{0} regular customers have no invoice this month.", { 0: d.open }) : d.total ? tr("Reviewed.") : tr("Every regular customer was invoiced.");
    default: return c.detail;
  }
}

const CHECK_NAMES: Record<string, string> = {
  balanced: N("Every entry balances"), roles: N("Account roles confirmed"), entries: N("Missing costs and bank items decided"),
  bank: N("Bank reconciled"), variances: N("Large variances explained"), unbilled: N("Regular customers invoiced"),
};

function RunView({ id }: { id: string }): ReactNode {
  const { ws } = useApp();
  const toast = useToast();
  const [v, setV] = useState<any>(null);
  const load = useLoad(() => get<any>(wsPath(ws, `/close/${id}`)).then((r) => { setV(r); return r; }), [ws.id, id]);
  const { busy, run } = useAction();
  const [confirm, setConfirm] = useState<"delete" | "close" | null>(null);
  const editor = can(ws, "editor");
  const admin = can(ws, "admin");
  const cur = ws.base_currency;
  if (load.error) return <div className="page"><ErrorBox error={load.error} /></div>;
  if (!v) return <div className="page"><Loading /></div>;
  const closed = v.status === "closed";
  const canEdit = editor && !closed;
  const items: any[] = v.items;
  const live = items.filter((i) => i.status !== "resolved");
  const entries = live.filter((i) => ENTRY_KINDS.includes(i.kind));
  const variances = live.filter((i) => i.kind === "variance");
  const review = live.filter((i) => i.kind === "unbilled" || i.kind === "bank_unknown");
  const resolved = items.filter((i) => i.status === "resolved");
  const set = (r: any) => { if (r) setV(r); return r; };
  const decide = (item: any, body: any, msg?: string) => run(() => patch(wsPath(ws, `/close/${id}/items/${item.id}`), body), msg).then(set);
  const r = v.readiness;
  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <p className="small"><a href="#/close">{tr("Month-end close")}</a></p>
      <PageHead title={tr("Close for {0}", { 0: fmtMonth(v.month) })}
        sub={<>{tr("Ledger “{0}”", { 0: v.ledger.name })}{" · "}
          {v.prepared_at ? tr("prepared {0}", { 0: fmtRelative(v.prepared_at) }) : null}
          {v.prepared_via === "workflow" ? <> · <span className="tag blue">{tr("overnight")}</span></> : v.prepared_by_name ? <> {tr("by {0}", { 0: v.prepared_by_name })}</> : null}
          {closed ? <> · <span className="tag green">{tr("closed by {0}", { 0: v.closed_by_name || "—" })}</span></> : null}</>}
        actions={<>
          {canEdit && <button className="btn" disabled={busy} onClick={() => run(() => post(wsPath(ws, `/close/${id}/refresh`), {}), tr("Prepared again from the ledger")).then(set)}>
            <Icon name="refresh" />{tr("Refresh from the ledger")}</button>}
          <button className="btn" disabled={busy} onClick={() => run(() => download(wsPath(ws, `/close/${id}/pack.xlsx`), `OfinTech-Desk-close-pack-${v.month}.xlsx`))}>
            <Icon name="sheet" />{tr("Close pack")}</button>
          {admin && !closed && <button className="btn primary" disabled={busy} onClick={() => setConfirm("close")}>{tr("Close the month")}</button>}
          {admin && closed && <button className="btn" disabled={busy} onClick={() => run(() => post(wsPath(ws, `/close/${id}/status`), { status: "open" }), tr("Reopened")).then(set)}>{tr("Reopen")}</button>}
          {admin && <button className="btn ghost" aria-label={tr("Delete")} onClick={() => setConfirm("delete")}><Icon name="trash" /></button>}
        </>} />
      {confirm === "delete" && <Confirm text={tr("Delete this close? Decisions and explanations are removed; the ledger is not changed.")} onClose={() => setConfirm(null)}
        onYes={() => run(() => del(wsPath(ws, `/close/${id}`))).then(() => go("close"))} />}
      {confirm === "close" && <Confirm yes="Close the month" onClose={() => setConfirm(null)}
        text={r.open_items ? tr("{0} items are still open. Close {1} anyway? Nothing can be changed until it is reopened.", { 0: r.open_items, 1: fmtMonth(v.month) })
          : tr("Close {0}? Nothing can be changed until it is reopened.", { 0: fmtMonth(v.month) })}
        onYes={() => run(() => post(wsPath(ws, `/close/${id}/status`), { status: "closed" }), tr("Month closed")).then(set)} />}

      <section className="close-ready">
        <div className="close-score">
          <div className="label">{tr("Ready to close")}</div>
          <div className="value num">{r.score}%</div>
          <div className={`score-bar big ${r.score >= 100 ? "ok" : r.score >= 60 ? "mid" : "low"}`}><span style={{ width: `${Math.max(2, r.score)}%` }} /></div>
          <div className="small muted">{r.open_items ? tr("{0} items to decide", { 0: r.open_items }) : tr("Nothing left to decide")}</div>
        </div>
        <ul className="checks">
          {r.checks.map((c: any) => (
            <li key={c.key} className={`check-row ${c.status}`}>
              <span className="check-mark" aria-hidden="true">{c.status === "ok" ? "✓" : c.status === "warn" ? "!" : "○"}</span>
              <span><strong>{tr(CHECK_NAMES[c.key] || c.label)}</strong><span className="sr-only"> ({tr(c.status === "ok" ? "done" : c.status === "warn" ? "check" : "to do")})</span>
                <span className="small muted"> {checkText(c)}</span>
                {c.key === "roles" && c.status !== "ok" ? <> <a className="small" href="#/analysis/roles">{tr("Confirm roles")}</a></> : null}</span>
            </li>))}
        </ul>
      </section>

      <Entries v={v} entries={entries} canEdit={canEdit} cur={cur} busy={busy} decide={decide} toast={toast} />
      <Bank v={v} canEdit={canEdit} cur={cur} onChange={set} />
      <Variances v={v} items={variances} canEdit={canEdit} cur={cur} busy={busy} decide={decide} />
      {review.length > 0 && <Review v={v} items={review} canEdit={canEdit} cur={cur} busy={busy} decide={decide} />}
      {resolved.length > 0 && (
        <section className="cc-section">
          <h2 className="h3">{tr("No longer needed")}</h2>
          <p className="small muted">{tr("The ledger now has these, so they were taken off the list.")}</p>
          <ul className="list small">{resolved.map((i) => <li key={i.id}>{itemTitle(i, v.month)}</li>)}</ul>
        </section>
      )}
      <p className="xs muted" style={{ marginTop: 12 }}>{tr("Materiality for variances: {0}. Proposals come from the ledger's own history; each one shows the lines behind it.", { 0: fmtMoney(v.materiality, cur) })}</p>
    </div>
  );
}

function itemTitle(i: any, month: string): string {
  const d = i.data;
  const acc = d.lines?.[0]?.account || d.account;
  switch (i.kind) {
    case "accrual": return tr("Accrue {0} for {1}", { 0: acc, 1: fmtMonth(month) });
    case "depreciation": return tr("Charge {0} for {1}", { 0: acc, 1: fmtMonth(month) });
    case "bank_fee": return tr("Bank charge on {0}: {1}", { 0: fmtDate(d.date), 1: d.description || "—" });
    case "bank_interest": return tr("Interest received on {0}: {1}", { 0: fmtDate(d.date), 1: d.description || "—" });
    case "bank_unknown": return tr("On the statement, not in the ledger: {0} {1}", { 0: fmtDate(d.date), 1: d.description || "" });
    case "variance": return d.account;
    case "unbilled": return tr("{0} was not invoiced in {1}", { 0: d.party, 1: fmtMonth(month) });
    default: return d.title;
  }
}

function Evidence({ rows, cur }: { rows: any[]; cur: string }): ReactNode {
  if (!rows?.length) return null;
  return (
    <details className="evidence">
      <summary className="small">{tr("Ledger lines behind it ({0})", { 0: rows.length })}</summary>
      <div className="table-wrap" style={{ maxHeight: 260 }}>
        <table className="ledger">
          <thead><tr><th>{tr("Date")}</th><th>{tr("Entry")}</th><th>{tr("Account")}</th><th>{tr("Party")}</th><th>{tr("Description")}</th>
            <th className="num">{tr("Debit")}</th><th className="num">{tr("Credit")}</th></tr></thead>
          <tbody>{rows.map((e: any, k: number) => (
            <tr key={k}><td>{fmtDate(e.date)}</td><td>{e.entry}</td><td>{e.account_code ? `${e.account_code} ` : ""}{e.account || ""}</td>
              <td>{e.party || "—"}</td><td>{e.description || "—"}</td>
              <td className="num">{e.debit ? fmtMoney(e.debit, cur) : e.amount && e.amount < 0 ? fmtMoney(-e.amount, cur) : ""}</td>
              <td className="num">{e.credit ? fmtMoney(e.credit, cur) : e.amount && e.amount > 0 ? fmtMoney(e.amount, cur) : ""}</td></tr>))}</tbody>
        </table>
      </div>
    </details>
  );
}

function StatusTag({ s }: { s: string }): ReactNode {
  const tone: Record<string, string> = { approved: "green", explained: "green", reviewed: "green", rejected: "", proposed: "amber", open: "amber" };
  return <span className={`tag ${tone[s] ?? ""}`}>{tr(s)}</span>;
}

function NoteBox({ item, canEdit, onSave }: { item: any; canEdit: boolean; onSave: (note: string) => void }): ReactNode {
  const [note, setNote] = useState(item.note || "");
  useEffect(() => setNote(item.note || ""), [item.note]);
  if (!canEdit) return item.note ? <p className="small" style={{ margin: 0 }}><strong>{tr("Note")}:</strong> {item.note}</p> : null;
  return <input className="input small-input" value={note} placeholder={tr("Note (kept in the close pack)")} aria-label={tr("Note")}
    onChange={(e) => setNote(e.target.value)} onBlur={() => note !== (item.note || "") && onSave(note)} />;
}

// ---- proposed entries ----------------------------------------------------------------------------------------------
function Entries({ v, entries, canEdit, cur, busy, decide, toast }: any): ReactNode {
  const { ws } = useApp();
  return (
    <section className="cc-section">
      <div className="spread"><h2 className="h3">{tr("Proposed entries")}</h2>
        {v.journal.entries > 0 && <div className="row">
          <span className="small muted">{tr("{0} approved, {1}", { 0: v.journal.entries, 1: fmtMoney(v.journal.total, cur) })}</span>
          <button className="btn small" onClick={() => download(wsPath(ws, `/close/${v.id}/journal.xlsx`), `OfinTech-Desk-close-journal-${v.month}.xlsx`).catch((e) => toast(String(e)))}>
            <Icon name="sheet" size={14} />{tr("Journal (Excel)")}</button>
          <button className="btn small" onClick={() => download(wsPath(ws, `/close/${v.id}/journal.csv`), `OfinTech-Desk-close-journal-${v.month}.csv`).catch((e) => toast(String(e)))}>
            {tr("CSV")}</button>
        </div>}
      </div>
      <p className="small muted" style={{ maxWidth: "75ch" }}>{tr("Regular costs that have nothing this month, depreciation not charged, and bank items not in the ledger. Approved entries go into a journal file in the general ledger template's columns, for a person to import into the accounting system.")}</p>
      {entries.length === 0 ? <p className="small">{tr("Nothing regular is missing this month.")}</p> : entries.map((i: any) => (
        <EntryCard key={i.id} item={i} month={v.month} canEdit={canEdit} cur={cur} busy={busy} decide={decide} />))}
    </section>
  );
}

function EntryCard({ item, month, canEdit, cur, busy, decide }: any): ReactNode {
  const d = item.data;
  const [rejecting, setRejecting] = useState(false);
  const [why, setWhy] = useState("");
  const [amount, setAmount] = useState<string>(String(d.amount));
  const [accts, setAccts] = useState<Record<number, { account: string; account_code: string }>>({});
  useEffect(() => setAmount(String(d.amount)), [d.amount]);
  const proposed = item.status === "proposed";
  const editable = canEdit && proposed;
  const history = d.history || [];
  const reason = item.kind === "accrual" || item.kind === "depreciation"
    ? tr("Charged every month from {0} to {1} ({2}) and nothing in {3}. Proposed: last month's amount.",
      { 0: fmtMonth(history[0]?.month), 1: fmtMonth(history[history.length - 1]?.month), 2: history.map((h: any) => fmtMoney(h.amount, cur)).join(", "), 3: fmtMonth(month) })
    : tr("On the bank statement ({0}, {1}) and not in the ledger.", { 0: fmtDate(d.date), 1: fmtMoney(d.amount, cur) });
  const missingAccount = d.lines.findIndex((ln: any) => !ln.account_code && d.warning);
  async function saveLines() {
    const lines = Object.entries(accts).map(([index, x]) => ({ index: Number(index), account: x.account, account_code: x.account_code || null }));
    const amt = Number(amount);
    const body: any = {};
    if (lines.length) body.lines = lines;
    if (amt && amt !== d.amount) body.amount = amt;
    if (Object.keys(body).length) await decide(item, body, tr("Saved"));
    setAccts({});
  }
  return (
    <div className={`close-item ${item.status}`}>
      <div className="spread"><strong>{itemTitle(item, month)}</strong><StatusTag s={item.status} /></div>
      <p className="small" style={{ margin: "4px 0 8px", color: "var(--ink-2)" }}>{reason}</p>
      <table className="ledger compact">
        <thead><tr><th>{tr("Account")}</th><th className="num">{tr("Debit")}</th><th className="num">{tr("Credit")}</th></tr></thead>
        <tbody>{d.lines.map((ln: any, k: number) => (
          <tr key={k}>
            <td>{editable && k === missingAccount ? (
              <span className="row" style={{ gap: 6 }}>
                <input className="input small-input" style={{ width: 90 }} placeholder={tr("Code")} aria-label={tr("Account code")}
                  value={accts[k]?.account_code ?? ""} onChange={(e) => setAccts({ ...accts, [k]: { account: accts[k]?.account ?? ln.account, account_code: e.target.value } })} />
                <input className="input small-input" aria-label={tr("Account")} value={accts[k]?.account ?? ln.account}
                  onChange={(e) => setAccts({ ...accts, [k]: { account_code: accts[k]?.account_code ?? "", account: e.target.value } })} />
              </span>) : <>{ln.account_code ? `${ln.account_code} ` : ""}{ln.account}</>}</td>
            <td className="num">{ln.debit ? (editable ? <input className="input small-input num" style={{ width: 120 }} type="number" min={0} step="0.01" aria-label={tr("Amount")}
              value={amount} onChange={(e) => setAmount(e.target.value)} /> : fmtMoney(ln.debit, cur)) : ""}</td>
            <td className="num">{ln.credit ? (editable ? fmtMoney(Number(amount) || 0, cur) : fmtMoney(ln.credit, cur)) : ""}</td>
          </tr>))}</tbody>
      </table>
      {d.warning && <div className="notice warn xs" style={{ marginTop: 8 }}>{tr("“{0}” is not in this ledger's chart of accounts; enter the account you use before importing the journal.", { 0: d.lines[missingAccount]?.account || "" })}</div>}
      <Evidence rows={d.evidence} cur={cur} />
      <div className="row wrap" style={{ marginTop: 8 }}>
        {editable && (Object.keys(accts).length > 0 || Number(amount) !== d.amount) && <button className="btn small" disabled={busy} onClick={saveLines}>{tr("Save changes")}</button>}
        {editable && <button className="btn small primary" disabled={busy || Object.keys(accts).length > 0 || Number(amount) !== d.amount}
          onClick={() => decide(item, { status: "approved" }, tr("Approved"))}>{tr("Approve")}</button>}
        {editable && !rejecting && <button className="btn small" disabled={busy} onClick={() => setRejecting(true)}>{tr("Reject")}</button>}
        {editable && rejecting && <form className="row" onSubmit={(e) => { e.preventDefault(); if (why.trim()) { decide(item, { status: "rejected", note: why.trim() }, tr("Rejected")); setRejecting(false); } }}>
          <input className="input small-input" autoFocus value={why} onChange={(e) => setWhy(e.target.value)} aria-label={tr("Why is this entry not needed?")}
            placeholder={tr("Why is this entry not needed?")} />
          <button className="btn small" disabled={!why.trim()}>{tr("Reject")}</button>
          <button type="button" className="btn small ghost" onClick={() => setRejecting(false)}>{tr("Cancel")}</button></form>}
        {canEdit && !proposed && <button className="btn small ghost" disabled={busy} onClick={() => decide(item, { status: "proposed" })}>{tr("Undo")}</button>}
        <span className="grow" />
        <NoteBox item={item} canEdit={canEdit} onSave={(note) => decide(item, { note })} />
      </div>
      {item.decided_by_name && !proposed && <p className="xs muted" style={{ margin: "6px 0 0" }}>{tr("{0} by {1}, {2}", { 0: tr(item.status), 1: item.decided_by_name, 2: fmtRelative(item.decided_at) })}</p>}
    </div>
  );
}

// ---- bank -----------------------------------------------------------------------------------------------------------
function Bank({ v, canEdit, cur, onChange }: any): ReactNode {
  const { ws } = useApp();
  const { busy, run } = useAction();
  const [account, setAccount] = useState<string>(v.statement?.account_key || "");
  const [closing, setClosing] = useState<string>(v.statement?.closing_balance != null ? String(v.statement.closing_balance) : "");
  const b = v.bank;
  async function pick(file: File | undefined) {
    if (!file) return;
    const fields: Record<string, string> = {};
    if (account) fields.account_key = account;
    if (closing) fields.closing_balance = closing;
    onChange(await run(() => upload(wsPath(ws, `/close/${v.id}/statement`), file, fields), tr("Statement matched")));
  }
  async function saveOptions() {
    onChange(await run(() => put(wsPath(ws, `/close/${v.id}/statement`), { account_key: account || null,
      closing_balance: closing === "" ? null : Number(closing) }), tr("Saved")));
  }
  const accounts: any[] = v.cash_accounts || [];
  return (
    <section className="cc-section">
      <h2 className="h3">{tr("Bank against the statement")}</h2>
      <p className="small muted" style={{ maxWidth: "75ch" }}>{tr("Upload the month's bank statement (Excel or CSV with a date, a description and an amount, or money in and money out). Lines are matched to the ledger by amount and date within five days.")}</p>
      {canEdit && (
        <div className="row wrap" style={{ alignItems: "end", marginBottom: 12 }}>
          {accounts.length > 1 && <Field label={tr("Bank account")}>
            <select className="select" value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">{tr("The busiest bank account")}</option>
              {accounts.map((a) => <option key={a.key} value={a.key}>{a.code ? `${a.code} ` : ""}{a.name}</option>)}</select></Field>}
          <Field label={tr("Closing balance on the statement")} hint={v.statement?.file_closing ? tr("From the file: {0}", { 0: fmtMoney(v.statement.file_closing.amount, cur) }) : tr("Optional when the file has a balance column")}>
            <input className="input" style={{ width: 180 }} inputMode="decimal" value={closing} onChange={(e) => setClosing(e.target.value)} /></Field>
          {v.statement && <button className="btn" disabled={busy} onClick={saveOptions}>{tr("Apply")}</button>}
          <label className={`btn ${v.statement ? "" : "primary"} ${busy ? "disabled" : ""}`}><Icon name="upload" />{v.statement ? tr("Replace statement") : tr("Upload statement")}
            <input type="file" hidden accept=".xlsx,.xlsm,.csv,.txt" disabled={busy} onChange={(e) => { void pick(e.target.files?.[0]); e.target.value = ""; }} /></label>
        </div>
      )}
      {v.statement && <p className="xs muted">{tr("{0}: {1} lines, {2} to {3}, added {4}.", { 0: v.statement.filename, 1: v.statement.lines,
        2: fmtDate(v.statement.first_date), 3: fmtDate(v.statement.last_date), 4: fmtRelative(v.statement.uploaded_at) })}</p>}
      {b && (
        <div className="cc-cols">
          <table className="ledger">
            <tbody>
              <tr><td>{tr("Balance per statement")}</td><td className="num">{fmtMoney(b.closing_balance, cur)}</td></tr>
              <tr><td>{tr("+ in the ledger, not yet on the statement")}</td><td className="num">{fmtMoney(b.ledger_only_total, cur)}</td></tr>
              <tr><td><strong>{tr("Adjusted bank balance")}</strong></td><td className="num"><strong>{fmtMoney(b.adjusted_bank, cur)}</strong></td></tr>
              <tr><td>{tr("Balance per ledger")}</td><td className="num">{fmtMoney(b.book_balance, cur)}</td></tr>
              <tr><td>{tr("+ on the statement, not in the ledger")}</td><td className="num">{fmtMoney(b.statement_only_total, cur)}</td></tr>
              <tr><td><strong>{tr("Adjusted ledger balance")}</strong></td><td className="num"><strong>{fmtMoney(b.adjusted_book, cur)}</strong></td></tr>
            </tbody>
            <tfoot><tr><td>{tr("Unexplained difference")}</td>
              <td className={`num ${b.difference ? "neg" : ""}`}>{b.difference == null ? tr("needs the closing balance") : fmtMoney(b.difference, cur)}</td></tr></tfoot>
          </table>
          <div className="stack" style={{ gap: 10 }}>
            <p className="small" style={{ margin: 0 }}>{tr("{0} of {1} statement lines matched to {2} ({3}).", { 0: b.matched, 1: b.statement_lines,
              2: b.account.name, 3: tr("{0} ledger lines this month", { 0: b.ledger_lines }) })}</p>
            {b.ledger_only.length > 0 && <div>
              <div className="small" style={{ fontWeight: 600 }}>{tr("In the ledger, not yet on the statement")}</div>
              <ul className="list small">{b.ledger_only.map((x: any) => <li key={x.id} className="spread"><span>{fmtDate(x.date)} · {x.entry} · {x.party || x.description || "—"}</span><span className="num">{fmtMoney(x.amount, cur)}</span></li>)}</ul>
              <p className="xs muted" style={{ margin: 0 }}>{tr("Payments not cleared and deposits in transit; they should appear on next month's statement.")}</p>
            </div>}
            {b.statement_only.length > 0 && <div>
              <div className="small" style={{ fontWeight: 600 }}>{tr("On the statement, not in the ledger")}</div>
              <ul className="list small">{b.statement_only.map((x: any) => <li key={x.id} className="spread"><span>{fmtDate(x.date)} · {x.description || "—"}</span><span className="num">{fmtMoney(x.amount, cur)}</span></li>)}</ul>
              <p className="xs muted" style={{ margin: 0 }}>{tr("Bank charges and interest are proposed as entries above; anything else is listed under To review.")}</p>
            </div>}
          </div>
        </div>
      )}
    </section>
  );
}

// ---- variances -----------------------------------------------------------------------------------------------------
function Variances({ v, items, canEdit, cur, busy, decide }: any): ReactNode {
  return (
    <section className="cc-section">
      <h2 className="h3">{tr("What moved")}</h2>
      <p className="small muted" style={{ maxWidth: "75ch" }}>{tr("Accounts whose month differs from the average of the previous three months by at least {0} and 20%. Each needs a short explanation before the month is closed.", { 0: fmtMoney(v.materiality, cur) })}</p>
      {items.length === 0 ? <p className="small">{tr("Nothing moved more than usual.")}</p> : items.map((i: any) => {
        const d = i.data;
        return (
          <div key={i.id} className={`close-item ${i.status}`}>
            <div className="spread"><strong>{d.account_code ? `${d.account_code} ` : ""}{d.account}</strong><StatusTag s={i.status} /></div>
            <div className="row wrap small" style={{ gap: 18, margin: "6px 0" }}>
              <span>{tr("This month")} <strong className="num">{fmtMoney(d.amount, cur)}</strong></span>
              <span>{tr("3-month average")} <span className="num">{fmtMoney(d.average, cur)}</span></span>
              <span className={d.good ? "pos" : "neg"}>{d.difference > 0 ? "+" : ""}{fmtMoney(d.difference, cur)}{d.change_pct != null ? ` (${d.change_pct > 0 ? "+" : ""}${fmtNumber(d.change_pct, 0)}%)` : ""}</span>
              <span className="muted">{tr("Last month")} <span className="num">{fmtMoney(d.previous, cur)}</span></span>
            </div>
            <Evidence rows={d.evidence} cur={cur} />
            <Explain item={i} canEdit={canEdit} busy={busy} onSave={(note: string) => decide(i, { status: "explained", note }, tr("Explained"))}
              onReopen={() => decide(i, { status: "open" })} />
          </div>
        );
      })}
    </section>
  );
}

function Explain({ item, canEdit, busy, onSave, onReopen, done = "explained", label = "Explain", hint }: any): ReactNode {
  const [note, setNote] = useState(item.note || "");
  if (item.status === done || !canEdit) {
    return (
      <div className="row wrap" style={{ marginTop: 6 }}>
        {item.note ? <p className="small" style={{ margin: 0 }}><strong>{tr("Explanation")}:</strong> {item.note}
          {item.decided_by_name ? <span className="xs muted"> · {item.decided_by_name}</span> : null}</p> : null}
        {canEdit && <button className="btn small ghost" disabled={busy} onClick={onReopen}>{tr("Undo")}</button>}
      </div>
    );
  }
  return (
    <form className="row wrap" style={{ marginTop: 6 }} onSubmit={(e) => { e.preventDefault(); if (note.trim()) onSave(note.trim()); }}>
      <input className="input grow" value={note} onChange={(e) => setNote(e.target.value)} aria-label={tr("Explanation")}
        placeholder={hint || tr("What happened, e.g. a one-off audit fee, or a cost booked to the wrong account")} />
      <button className="btn small primary" disabled={busy || !note.trim()}>{tr(label)}</button>
    </form>
  );
}

// ---- review ---------------------------------------------------------------------------------------------------------
function Review({ v, items, canEdit, cur, busy, decide }: any): ReactNode {
  return (
    <section className="cc-section">
      <h2 className="h3">{tr("To review")}</h2>
      {items.map((i: any) => {
        const d = i.data;
        const why = i.kind === "unbilled"
          ? tr("Invoiced every month from {0} to {1} (average {2}) and nothing in {3}.", { 0: fmtMonth(d.history?.[0]?.month), 1: fmtMonth(d.history?.[d.history.length - 1]?.month), 2: fmtMoney(d.amount, cur), 3: fmtMonth(v.month) })
          : d.amount > 0 ? tr("Money in that nobody recorded: find the invoice it pays, or record it.") : tr("Money out that nobody recorded: find the bill or the transfer, or record it.");
        return (
          <div key={i.id} className={`close-item ${i.status}`}>
            <div className="spread"><strong>{itemTitle(i, v.month)}{i.kind === "bank_unknown" ? <> <span className="num">{fmtMoney(d.amount, cur)}</span></> : null}</strong><StatusTag s={i.status} /></div>
            <p className="small" style={{ margin: "4px 0", color: "var(--ink-2)" }}>{why}</p>
            <Explain item={i} canEdit={canEdit} busy={busy} done="reviewed" label="Mark reviewed"
              hint={i.kind === "unbilled" ? tr("What you found, e.g. the contract ended, or the invoice is in draft") : tr("What it is, e.g. a customer deposit for the next job")}
              onSave={(note: string) => decide(i, { status: "reviewed", note }, tr("Reviewed"))} onReopen={() => decide(i, { status: "open" })} />
          </div>
        );
      })}
    </section>
  );
}
