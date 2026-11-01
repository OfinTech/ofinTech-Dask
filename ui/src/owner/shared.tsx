// Pieces every Owner Control Center page uses: the date-range picker, KPI tiles, paging, money, a generic
// records table and a tiny form helper. Everything renders with the app's own design system (styles.css).
import { useState, type ReactNode } from "react";
import { Chart } from "../components/charts";
import { Icon } from "../components/ui";
import { fmtCompact, fmtDateTime, fmtNumber, fmtRelative } from "../lib/format";
import { N, tr } from "../lib/i18n";

export type Range = { period?: string; from?: number; to?: number };

export const PERIODS: [string, string][] = [["today", N("Today")], ["yesterday", N("Yesterday")], ["7d", N("Last 7 days")],
  ["30d", N("Last 30 days")], ["90d", N("Last 90 days")]];

export function rangeQuery(r: Range): string {
  if (r.from && r.to) return `from=${r.from}&to=${r.to}`;
  return `period=${r.period || "30d"}`;
}

export function RangePicker({ value, onChange }: { value: Range; onChange: (r: Range) => void }): ReactNode {
  const [custom, setCustom] = useState(Boolean(value.from));
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  return (
    <div className="row wrap">
      <div className="seg" role="tablist" aria-label={tr("Period")}>
        {PERIODS.map(([k, label]) => <button key={k} role="tab" aria-pressed={!custom && value.period === k}
          onClick={() => { setCustom(false); onChange({ period: k }); }}>{tr(label)}</button>)}
        <button role="tab" aria-pressed={custom} onClick={() => setCustom(true)}>{tr("Custom")}</button>
      </div>
      {custom && <form className="row" onSubmit={(e) => { e.preventDefault(); if (from && to) onChange({ from: Date.parse(from) / 1000, to: Date.parse(to) / 1000 + 86400 }); }}>
        <input className="input" type="date" aria-label={tr("From")} value={from} onChange={(e) => setFrom(e.target.value)} style={{ width: 150 }} />
        <input className="input" type="date" aria-label={tr("To")} value={to} onChange={(e) => setTo(e.target.value)} style={{ width: 150 }} />
        <button className="btn small">{tr("Apply")}</button>
      </form>}
    </div>
  );
}

export function money(minor: number | null | undefined, currency = "USD"): string {
  if (minor === null || minor === undefined) return "—";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 2 }).format(minor / 100);
  } catch {
    return `${(minor / 100).toFixed(2)} ${currency}`;
  }
}

export function moneyMap(m: Record<string, number> | undefined): string {
  if (!m || !Object.keys(m).length) return "—";
  return Object.entries(m).map(([c, v]) => money(v, c)).join(" · ");
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

type Metric = number | { value: number; previous?: number } | null | undefined;

export function Kpi({ label, value, format = "number", hint, tone }: { label: string; value: Metric | string; format?: "number" | "percent" | "bytes" | "text";
  hint?: string; tone?: "good" | "watch" | "act" }): ReactNode {
  let shown: string;
  let delta: ReactNode = null;
  if (typeof value === "string") shown = value;
  else if (value === null || value === undefined) shown = tr("n/a");
  else {
    const v = typeof value === "number" ? value : value.value;
    shown = format === "percent" ? `${fmtNumber(v, 1)}%` : format === "bytes" ? bytes(v) : v >= 10000 ? fmtCompact(v) : fmtNumber(v, 0);
    if (typeof value === "object" && value.previous !== undefined) {
      const d = v - value.previous;
      delta = <span className={`small ${d > 0 ? "up" : d < 0 ? "down" : "muted"}`}>{d > 0 ? "▲" : d < 0 ? "▼" : "•"} {fmtNumber(Math.abs(d), 0)} {tr("vs previous period")}</span>;
    }
  }
  return (
    <div className={`kpi-card ${tone || ""}`} title={hint}>
      <span className="kpi-name">{label}</span>
      <span className="kpi-value">{shown}</span>
      {delta}
    </div>
  );
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (o: number) => void }): ReactNode {
  if (total <= limit) return null;
  return (
    <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
      <span className="small muted">{tr("{0}–{1} of {2}", { 0: offset + 1, 1: Math.min(total, offset + limit), 2: total })}</span>
      <div className="row">
        <button className="btn small" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>{tr("Previous")}</button>
        <button className="btn small" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>{tr("Next")}</button>
      </div>
    </div>
  );
}

export type Col<T> = { key: string; label: string; render?: (row: T) => ReactNode; num?: boolean };

export function Records<T extends Record<string, any>>({ cols, rows, onRow, empty }: { cols: Col<T>[]; rows: T[]; onRow?: (r: T) => void; empty?: string }): ReactNode {
  if (!rows.length) return <p className="muted small">{empty || tr("Nothing here yet.")}</p>;
  return (
    <div className="table-wrap">
      <table className="ledger">
        <thead><tr>{cols.map((c) => <th key={c.key} className={c.num ? "num" : ""}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={(r as any).id ?? i} onClick={onRow ? () => onRow(r) : undefined} style={onRow ? { cursor: "pointer" } : undefined}>
              {cols.map((c) => <td key={c.key} className={c.num ? "num" : ""}>{c.render ? c.render(r) : String(r[c.key] ?? "—")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function When({ ts }: { ts: number | null | undefined }): ReactNode {
  return <span title={fmtDateTime(ts)}>{ts ? fmtRelative(ts) : "—"}</span>;
}

export function Tag({ text, tone }: { text: string; tone?: "green" | "red" | "amber" | "blue" | "" }): ReactNode {
  return <span className={`tag ${tone || ""}`}>{text}</span>;
}

export function toneFor(status: string): "green" | "red" | "amber" | "blue" | "" {
  if (["active", "sent", "processed", "ok", "WON", "connected"].includes(status)) return "green";
  if (["canceled", "failed", "dead_letter", "suspended", "LOST", "payment_failed", "refused"].includes(status)) return "red";
  if (["past_due", "trialing", "queued", "pending", "scheduled", "read_only", "NEW", "PROPOSAL", "warning"].includes(status)) return "amber";
  if (["draft", "archived", "CONTACTED", "QUALIFIED", "ignored"].includes(status)) return "blue";
  return "";
}

/** A time series from /api/owner/analytics/series/* drawn with the app's chart. */
export function SeriesChart({ points, kind = "bar", valueKey = "value", title }: { points: Record<string, any>[]; kind?: "bar" | "line" | "area" | "stacked_bar";
  valueKey?: string; title?: string }): ReactNode {
  if (!points.length) return <p className="muted small">{tr("No data for this selection.")}</p>;
  const grouped = points.some((p) => p.name !== undefined && p.bucket !== undefined);
  if (grouped) {
    const buckets = [...new Set(points.map((p) => p.bucket))];
    const names = [...new Set(points.map((p) => p.name))].slice(0, 8);
    const series = names.map((n) => ({ name: String(n), values: buckets.map((b) => points.find((p) => p.bucket === b && p.name === n)?.[valueKey] ?? 0) }));
    return <Chart categories={buckets.map(String)} series={series} kind={kind === "bar" ? "stacked_bar" : kind} title={title} />;
  }
  const cats = points.map((p) => String(p.bucket ?? p.name ?? ""));
  return <Chart categories={cats} series={[{ name: title || tr("Value"), values: points.map((p) => Number(p[valueKey] ?? 0)) }]} kind={kind} title={title} />;
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }): ReactNode {
  return (
    <section className="panel panel-pad stack" style={{ marginBottom: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}><h3 style={{ margin: 0 }}>{title}</h3>{actions}</div>
      {children}
    </section>
  );
}

export function Toggle({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }): ReactNode {
  return <label className="row"><input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /> <span>{label}</span></label>;
}

export function Help({ text }: { text: string }): ReactNode {
  return <span className="muted small" title={text} aria-label={text}><Icon name="search" size={12} /></span>;
}
