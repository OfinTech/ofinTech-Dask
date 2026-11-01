// The accountant's general ledger: journal entries with filters and search, the chart of accounts with roles and
// balances, and accounting validation. Every row opens the same entry and account pages as the executive drill-down.
import { useState, type ReactNode } from "react";
import { get } from "../lib/api";
import { useApp, wsPath } from "../lib/app";
import { fmtDate, fmtMoney, fmtNumber } from "../lib/format";
import { Empty, ErrorBox, Loading, PageHead, go, query, useLoad } from "../components/ui";
import { N, lang, tr } from "../lib/i18n";
import { Prov } from "./Overview";

const TABS: [string, string][] = [["entries", N("Journal entries")], ["accounts", N("Accounts")], ["validation", N("Validation")]];
const ROLE: Record<string, string> = {
  cash: N("Bank and cash"), receivables: N("Receivables"), inventory: N("Inventory"), other_current_assets: N("Other current assets"),
  fixed_assets: N("Fixed assets"), other_non_current_assets: N("Other non-current assets"), payables: N("Payables"),
  other_current_liabilities: N("Other current liabilities"), short_term_debt: N("Short-term debt"), long_term_debt: N("Long-term debt"),
  other_non_current_liabilities: N("Other non-current liabilities"), equity: N("Equity"), revenue: N("Revenue"),
  other_income: N("Other income"), cogs: N("Cost of sales"), payroll: N("Payroll"), opex: N("Other operating expenses"),
  depreciation: N("Depreciation and amortisation"), interest: N("Interest"), tax: N("Tax on profit"), other_expense: N("Other expenses"),
};
const CHECK: Record<string, [string, string]> = {
  unbalanced_entries: [N("Entries that do not balance"), N("Debits and credits of the entry differ.")],
  single_line_entries: [N("Single-line entries"), N("An entry with one line cannot balance; the other side is missing.")],
  no_account: [N("Lines without an account"), N("The line names no account, so it counts nowhere.")],
  no_date: [N("Lines without a usable date"), N("The line has no date, so it belongs to no month.")],
  future_date: [N("Lines dated in the future"), N("Dated after today.")],
  debit_and_credit: [N("Lines with both a debit and a credit"), N("A line carries an amount on both sides.")],
  zero_lines: [N("Zero lines"), N("Neither a debit nor a credit.")],
  suspense_balances: [N("Balances on suspense or clearing accounts"), N("Money parked on an account that should be cleared before the close.")],
  unconfirmed_roles: [N("Accounts whose role is still a suggestion"), N("Confirm them under Financial analysis → Account roles; until then the figures are unverified.")],
  unexpected_balance_sign: [N("Balances on the wrong side"), N("A bank or receivables account in credit, or a payables account in debit.")],
  contra_postings: [N("Postings on the unusual side"), N("A debit on an income account or a credit on a cost account: a reversal, a credit note or a mistake.")],
};
const LEVEL: Record<string, string> = { high: N("Fix before relying on the figures"), medium: N("Check"), low: N("Note") };
const PROV_WHY: Record<string, string> = {
  actual: N("Booked in the ledger, with account roles confirmed by finance."),
  unverified: N("Booked in the ledger, but the account roles are still the engine's suggestion."),
};

function monthName(m: string): string {
  return new Intl.DateTimeFormat(lang() === "en" ? navigator.language || "en" : lang(), { month: "long", year: "numeric" }).format(new Date(`${m}-01T00:00:00`));
}
function withQ(path: string, params: Record<string, string | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}

function Head({ tab, months, month, sub, allMonths }: { tab: string; months: string[]; month: string | null; sub: ReactNode; allMonths?: boolean }): ReactNode {
  const qs = query();
  const keep = Object.fromEntries([...qs.entries()].filter(([k]) => k !== "month"));
  return (
    <>
      <PageHead title={tr("General ledger")} sub={sub} actions={
        <label className="row"><span className="sr">{tr("Reporting month")}</span>
          <select className="select" style={{ width: "auto" }} value={month || ""} onChange={(e) => go(withQ(`ledger/${tab}`, { ...keep, month: e.target.value || null }))}>
            {allMonths && <option value="">{tr("All months")}</option>}
            {[...months].reverse().map((m) => <option key={m} value={m}>{monthName(m)}</option>)}
          </select></label>} />
      <div className="seg" role="tablist" style={{ marginBottom: 16 }}>
        {TABS.map(([k, label]) => <button key={k} role="tab" aria-pressed={k === tab} aria-selected={k === tab} onClick={() => go(withQ(`ledger/${k}`, { month }))}>{tr(label)}</button>)}
      </div>
    </>
  );
}

function Fail({ error }: { error: string }): ReactNode {
  return <div className="page">
    {/ledger/i.test(error) ? <Empty title={tr("No general ledger yet")} action={<a className="btn primary" href="#/data">{tr("Import a general ledger")}</a>}>
      {tr("Connect QuickBooks, Zoho Books, Xero or Odoo, or import a general-ledger file under Data. The overview is built from it.")}</Empty>
      : <ErrorBox error={error} />}
  </div>;
}

export function LedgerPage({ tab }: { tab?: string }): ReactNode {
  const month = query().get("month");
  switch (tab || "entries") {
    case "accounts": return <Accounts month={month} />;
    case "validation": return <Validation month={month} />;
    default: return <Entries month={month} />;
  }
}

// ---- journal entries ----------------------------------------------------------------------------------------------
function Entries({ month }: { month: string | null }): ReactNode {
  const { ws } = useApp();
  const qs = query();
  const filters = { q: qs.get("q") || "", type: qs.get("type") || "", account: qs.get("account") || "", party: qs.get("party") || "",
    min: qs.get("min") || "", unbalanced: qs.get("unbalanced") === "1", offset: Number(qs.get("offset") || 0) };
  const [draft, setDraft] = useState(filters);
  const key = JSON.stringify({ month, ...filters });
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/ledger/entries?${new URLSearchParams({
    ...(month ? { month } : {}), ...(filters.q ? { q: filters.q } : {}), ...(filters.type ? { type: filters.type } : {}),
    ...(filters.account ? { account: filters.account } : {}), ...(filters.party ? { party: filters.party } : {}),
    ...(filters.min ? { min: filters.min } : {}), ...(filters.unbalanced ? { unbalanced: "1" } : {}), offset: String(filters.offset), limit: "100" }).toString()}`)), [ws.id, key]);
  const accounts = useLoad<any>(() => get(wsPath(ws, "/ledger/accounts")), [ws.id]);
  if (error) return <Fail error={error} />;
  const e = data;
  const cur = e?.currency;
  const apply = (next: Partial<typeof filters>) => {
    const f = { ...draft, ...next, offset: 0 };
    go(withQ("ledger/entries", { month, q: f.q, type: f.type, account: f.account, party: f.party, min: f.min, unbalanced: f.unbalanced ? "1" : null }));
  };
  return (
    <div className="page wide">
      <Head tab="entries" months={e?.months || []} month={month} allMonths sub={e
        ? tr("{0} journal entries{1}, {2} of debits. Select an entry to see its lines, source and audit history.", { 0: fmtNumber(e.count, 0), 1: month ? ` ${tr("in {0}", { 0: monthName(month) })}` : "", 2: fmtMoney(e.total_debit, cur) })
        : tr("Loading…")} />
      <form className="row wrap" style={{ gap: 8, marginBottom: 12 }} onSubmit={(ev) => { ev.preventDefault(); apply({}); }}>
        <input className="input" style={{ width: 260 }} placeholder={tr("Search entries, descriptions, parties, accounts")} value={draft.q}
          onChange={(ev) => setDraft({ ...draft, q: ev.target.value })} aria-label={tr("Search")} />
        <select className="select" style={{ width: "auto" }} value={draft.type} onChange={(ev) => { setDraft({ ...draft, type: ev.target.value }); apply({ type: ev.target.value }); }} aria-label={tr("Type")}>
          <option value="">{tr("Any type")}</option>{(e?.types || []).map((t: string) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select className="select" style={{ width: "auto", maxWidth: 280 }} value={draft.account} onChange={(ev) => { setDraft({ ...draft, account: ev.target.value }); apply({ account: ev.target.value }); }} aria-label={tr("Account")}>
          <option value="">{tr("Any account")}</option>{(accounts.data?.rows || []).map((a: any) => <option key={a.key} value={a.key}>{a.name}</option>)}
        </select>
        <input className="input" style={{ width: 160 }} placeholder={tr("Party")} value={draft.party} onChange={(ev) => setDraft({ ...draft, party: ev.target.value })} aria-label={tr("Party")} />
        <input className="input num-in" placeholder={tr("Min amount")} inputMode="decimal" value={draft.min} onChange={(ev) => setDraft({ ...draft, min: ev.target.value })} aria-label={tr("Min amount")} />
        <label className="check small"><input type="checkbox" checked={draft.unbalanced} onChange={(ev) => { setDraft({ ...draft, unbalanced: ev.target.checked }); apply({ unbalanced: ev.target.checked }); }} /> {tr("Unbalanced only")}</label>
        <button className="btn" type="submit">{tr("Filter")}</button>
        {(filters.q || filters.type || filters.account || filters.party || filters.min || filters.unbalanced) && <a className="linklike" href={`#/${withQ("ledger/entries", { month })}`}>{tr("Clear")}</a>}
      </form>
      {loading && !e ? <Loading /> : e.rows.length === 0 ? <Empty title={tr("No entries match.")} /> : (
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table className="ledger">
            <thead><tr><th>{tr("Date")}</th><th>{tr("Entry")}</th><th>{tr("Type")}</th><th>{tr("Description")}</th><th>{tr("Party")}</th><th>{tr("Accounts")}</th>
              <th className="num">{tr("Lines")}</th><th className="num">{tr("Debit")}</th><th className="num">{tr("Credit")}</th></tr></thead>
            <tbody>
              {e.rows.map((r: any) => (
                <tr key={r.entry} className={`clickable${r.balanced ? "" : " aged"}`} onClick={() => go(withQ(`overview/entry/${encodeURIComponent(r.entry)}`, { month: month || r.date.slice(0, 7), from: "ledger" }))}>
                  <td className="num" style={{ textAlign: "start" }}>{fmtDate(r.date)}</td>
                  <td className="nowrap"><a href={`#/${withQ(`overview/entry/${encodeURIComponent(r.entry)}`, { month: month || r.date.slice(0, 7), from: "ledger" })}`} onClick={(ev) => ev.stopPropagation()}>{r.entry}</a>
                    {!r.balanced && <span className="tag red" style={{ marginInlineStart: 6 }}>{tr("does not balance")}</span>}</td>
                  <td className="muted">{r.type || "—"}</td>
                  <td>{r.description || <span className="muted">—</span>}</td>
                  <td>{r.party || <span className="muted">—</span>}</td>
                  <td className="small muted">{r.accounts.join(" · ")}{r.lines > 4 ? " …" : ""}</td>
                  <td className="num muted">{r.lines}</td>
                  <td className="num">{fmtMoney(r.debit, cur)}</td>
                  <td className="num">{fmtMoney(r.credit, cur)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {e && e.count > e.limit && <div className="row" style={{ marginTop: 10, gap: 8 }}>
        <button className="btn small" disabled={e.offset === 0} onClick={() => go(withQ("ledger/entries", { month, q: filters.q, type: filters.type, account: filters.account, party: filters.party, min: filters.min, unbalanced: filters.unbalanced ? "1" : null, offset: String(Math.max(0, e.offset - e.limit)) }))}>{tr("Previous")}</button>
        <span className="small muted">{tr("{0}–{1} of {2}", { 0: e.offset + 1, 1: Math.min(e.offset + e.limit, e.count), 2: e.count })}</span>
        <button className="btn small" disabled={e.offset + e.limit >= e.count} onClick={() => go(withQ("ledger/entries", { month, q: filters.q, type: filters.type, account: filters.account, party: filters.party, min: filters.min, unbalanced: filters.unbalanced ? "1" : null, offset: String(e.offset + e.limit) }))}>{tr("Next")}</button>
      </div>}
      {e && <p className="xs muted" style={{ marginTop: 12 }}><Prov kind={e.provenance.kind} small /> {tr(PROV_WHY[e.provenance.kind])} {tr("Nothing here is posted to your accounting system; OfinTech Desk reads the ledger only.")}</p>}
    </div>
  );
}

// ---- accounts -------------------------------------------------------------------------------------------------------
function Accounts({ month }: { month: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/ledger/accounts${month ? `?month=${month}` : ""}`)), [ws.id, month]);
  if (loading && !data) return <Loading />;
  if (error) return <Fail error={error} />;
  const a = data;
  const cur = a.currency;
  const m: string = a.month;
  return (
    <div className="page wide">
      <Head tab="accounts" months={a.months} month={m} sub={tr("{0} accounts, {1} with a confirmed role. Balances at the end of {2}; cost and income lines show the month's amount.", { 0: a.rows.length, 1: a.confirmed, 2: monthName(m) })} />
      <div className="table-wrap" style={{ maxHeight: "none" }}>
        <table className="ledger">
          <thead><tr><th>{tr("Code")}</th><th>{tr("Account")}</th><th>{tr("Class")}</th><th>{tr("Role")}</th><th className="num">{tr("Lines")}</th><th className="num">{tr("This month")}</th>
            <th className="num">{tr("Debit")}</th><th className="num">{tr("Credit")}</th><th className="num">{tr("Balance")}</th></tr></thead>
          <tbody>
            {a.rows.map((r: any) => {
              const href = r.role ? `#/${withQ(`overview/transactions/${r.role}`, { account: r.key, month: m, from: "ledger" })}` : undefined;
              return <tr key={r.key} className={`${href ? "clickable" : ""}${r.suspense && Math.abs(r.balance || 0) > 0.005 ? " aged" : ""}`} onClick={() => href && go(href.slice(2))}>
                <td className="num" style={{ textAlign: "start" }}>{r.code || "—"}</td>
                <td>{href ? <a href={href} onClick={(ev) => ev.stopPropagation()}>{r.name.replace(/^\S+\s/, r.code ? "" : "")}</a> : r.name}
                  {r.suspense && <span className="tag amber" style={{ marginInlineStart: 6 }}>{tr("suspense")}</span>}</td>
                <td className="muted">{r.class || "—"}</td>
                <td>{r.role ? tr(ROLE[r.role] || r.role) : "—"} {r.role && !r.confirmed && <span className="xs muted">({tr("suggested")})</span>}</td>
                <td className="num muted">{fmtNumber(r.lines, 0)}</td>
                <td className="num muted">{fmtNumber(r.lines_month, 0)}</td>
                <td className="num muted">{r.debit_month ? fmtMoney(r.debit_month, cur) : ""}</td>
                <td className="num muted">{r.credit_month ? fmtMoney(r.credit_month, cur) : ""}</td>
                <td className="num">{r.balance === null ? "—" : fmtMoney(r.balance, cur)}</td>
              </tr>;
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small" style={{ marginTop: 12 }}>{tr("Roles decide where an account lands in the statements and KPIs. Confirm or change them under Financial analysis → Account roles.")} <a href="#/analysis/roles">{tr("Account roles")}</a></p>
    </div>
  );
}

// ---- validation ---------------------------------------------------------------------------------------------------
function Validation({ month }: { month: string | null }): ReactNode {
  const { ws } = useApp();
  const { data, error, loading } = useLoad<any>(() => get(wsPath(ws, `/ledger/validation${month ? `?month=${month}` : ""}`)), [ws.id, month]);
  if (loading && !data) return <Loading />;
  if (error) return <Fail error={error} />;
  const v = data;
  const cur = v.currency;
  const sample = (kind: string, s: any): ReactNode => {
    const m = month || (s.date ? s.date.slice(0, 7) : null);
    if (s.entry) return <a href={`#/${withQ(`overview/entry/${encodeURIComponent(s.entry)}`, { month: m, from: "ledger" })}`}>{s.entry}</a>;
    if (s.key) return <a href={`#/${withQ("ledger/accounts", { month: m })}`}>{s.account}</a>;
    return null;
  };
  const detail = (s: any): string => {
    const parts: string[] = [];
    if (s.difference !== undefined) parts.push(tr("difference {0}", { 0: fmtMoney(s.difference, cur) }));
    if (s.balance !== undefined) parts.push(tr("balance {0}", { 0: fmtMoney(s.balance, cur) }));
    if (s.amount !== undefined) parts.push(`${s.side === "debit" ? tr("debit") : tr("credit")} ${fmtMoney(s.amount, cur)}`);
    if (s.date && s.difference === undefined) parts.push(fmtDate(s.date));
    if (s.role) parts.push(tr(ROLE[s.role] || s.role));
    if (s.account && s.key === undefined) parts.push(s.account);
    return parts.join(" · ");
  };
  return (
    <div className="page">
      <Head tab="validation" months={v.months} month={month} allMonths sub={month
        ? tr("{0} lines in {1} journal entries in {2}, checked the way a reviewer would.", { 0: fmtNumber(v.lines, 0), 1: fmtNumber(v.entries, 0), 2: monthName(month) })
        : tr("{0} lines in {1} journal entries across the whole ledger, checked the way a reviewer would.", { 0: fmtNumber(v.lines, 0), 1: fmtNumber(v.entries, 0) })} />
      <section className={`health ${v.clean ? "strong" : v.checks.some((c: any) => c.level === "high" && c.count) ? "at_risk" : "watch"}`} style={{ marginBottom: 16 }}>
        <div className="health-score"><div className="health-label">{v.clean ? tr("Clean") : tr("Issues")}</div><div className="health-number num">{v.issues}</div></div>
        <div className="health-text"><p className="health-lead">{v.clean ? tr("Nothing to fix. The ledger balances and every account has a confirmed role.")
          : tr("Items to look at before the figures are relied on. Each one opens the entry or account behind it.")}</p>
          <p className="small muted">{v.balance?.balanced ? tr("Debits equal credits across the ledger.") : tr("Debits and credits differ by {0} across the ledger.", { 0: fmtMoney(v.balance?.difference, cur) })}</p></div>
        <div className="health-prov"><Prov kind={v.provenance.kind} /><div className="xs muted">{tr(PROV_WHY[v.provenance.kind])}</div></div>
      </section>
      <ul className="exec-list">
        {v.checks.map((c: any) => (
          <li key={c.kind} className={c.count ? `exec-risk ${c.level === "high" ? "high" : ""}` : ""} style={c.count ? {} : { opacity: .55 }}>
            <div className="spread"><b>{tr(CHECK[c.kind]?.[0] || c.kind)}</b><span className="num">{fmtNumber(c.count, 0)}</span></div>
            <div className="xs muted">{tr(CHECK[c.kind]?.[1] || "")} {c.count > 0 && <span className="tag">{tr(LEVEL[c.level])}</span>}</div>
            {c.samples.length > 0 && <ul className="plain small" style={{ marginTop: 6 }}>
              {c.samples.map((s: any, i: number) => <li key={i}>{sample(c.kind, s)} <span className="muted">{detail(s)}</span></li>)}
              {c.count > c.samples.length && <li className="muted xs">{tr("and {0} more", { 0: c.count - c.samples.length })}</li>}
            </ul>}
          </li>
        ))}
      </ul>
    </div>
  );
}
