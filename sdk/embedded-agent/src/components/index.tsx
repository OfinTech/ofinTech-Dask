/**
 * The ten generative-UI components. Every value is rendered as a React text node; there is no HTML injection point
 * and no outbound link except through the host's `onOpenSource` handler. Charts are inline SVG built from numbers.
 */

import type { ReactNode } from "react";
import { formatNumber, t } from "../i18n";
import type { ComponentProps } from "../registry";
import type { Locale } from "../types";

type Cell = string | number | boolean | null;
interface Column { key: string; label: string; format?: string }

function Title({ children }: { children?: ReactNode }): ReactNode {
  return children ? <div className="ofe-title">{children}</div> : null;
}

function cell(locale: Locale, value: Cell, format: string | undefined, currency?: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return formatNumber(locale, value, format, format === "money" ? currency : undefined);
  if (typeof value === "boolean") return value ? "✓" : "—";
  return String(value);
}

function Table({ columns, rows, locale, currency, caption }: { columns: Column[]; rows: Cell[][]; locale: Locale; currency?: string; caption?: string }): ReactNode {
  return (
    <div className="ofe-table-wrap" role="region" aria-label={caption}>
      <table className="ofe-table">
        {caption ? <caption>{caption}</caption> : null}
        <thead>
          <tr>{columns.map((c) => <th key={c.key} scope="col" className={c.format && c.format !== "text" && c.format !== "date" ? "num" : undefined}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c, j) => (
                <td key={c.key} className={typeof r[j] === "number" ? "num" + ((r[j] as number) < 0 ? " neg" : "") : undefined}>
                  {cell(locale, r[j] ?? null, c.format, currency)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// -- 1. FinancialKpiCard ---------------------------------------------------------------------------------------------
export function FinancialKpiCard({ props, locale }: ComponentProps<{ label: string; value: number | null; format?: string; currency?: string; period?: string; change?: number | null; tone?: string }>): ReactNode {
  const tone = props.tone ?? "neutral";
  return (
    <div className={`ofe-kpi ofe-tone-${tone}`}>
      <div className="ofe-kpi-label">{props.label}</div>
      <div className={"ofe-kpi-value" + ((props.value ?? 0) < 0 ? " neg" : "")}>{formatNumber(locale, props.value, props.format, props.currency)}</div>
      {(props.period || props.change !== undefined) && (
        <div className="ofe-kpi-meta">
          {props.period ? <span>{props.period}</span> : null}
          {props.change !== undefined && props.change !== null ? <span className={props.change < 0 ? "neg" : "pos"}>{formatNumber(locale, props.change, "percent")}</span> : null}
        </div>
      )}
    </div>
  );
}

// -- 2/3. charts ------------------------------------------------------------------------------------------------------
interface Series { name: string; values: (number | null)[]; cls: string }

function LineChart({ periods, series, locale, title, currency }: { periods: string[]; series: Series[]; locale: Locale; title?: string; currency?: string }): ReactNode {
  const w = 480, h = 180, padL = 8, padR = 8, padT = 10, padB = 24;
  const all = series.flatMap((s) => s.values.filter((v): v is number => typeof v === "number"));
  if (!all.length) return <div className="ofe-empty">{t(locale, "empty")}</div>;
  const min = Math.min(0, ...all), max = Math.max(0, ...all) || 1;
  const x = (i: number) => padL + (periods.length > 1 ? (i * (w - padL - padR)) / (periods.length - 1) : (w - padL - padR) / 2);
  const y = (v: number) => padT + ((max - v) * (h - padT - padB)) / (max - min || 1);
  const zero = y(0);
  const summary = series.map((s) => `${s.name}: ${s.values.map((v) => formatNumber(locale, v, "number")).join(", ")}`).join("; ");
  return (
    <figure className="ofe-chart">
      <Title>{title}</Title>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${title ?? ""} ${summary}`.trim()}>
        <line x1={padL} x2={w - padR} y1={zero} y2={zero} className="ofe-axis" />
        {series.map((s) => (
          <polyline key={s.name} className={`ofe-line ${s.cls}`} fill="none" points={s.values.map((v, i) => (typeof v === "number" ? `${x(i)},${y(v)}` : "")).filter(Boolean).join(" ")} />
        ))}
        {periods.map((p, i) => (periods.length <= 12 || i % Math.ceil(periods.length / 12) === 0) && (
          <text key={p + i} x={x(i)} y={h - 6} className="ofe-tick" textAnchor="middle">{p.length > 7 ? p.slice(0, 7) : p}</text>
        ))}
      </svg>
      <figcaption className="ofe-legend">
        {series.map((s) => <span key={s.name} className={`ofe-legend-item ${s.cls}`}>{s.name}{currency ? ` (${currency})` : ""}</span>)}
      </figcaption>
    </figure>
  );
}

export function RevenueExpenseChart({ props, locale }: ComponentProps<{ title?: string; currency?: string; points: { period: string; revenue?: number | null; expenses?: number | null; profit?: number | null }[] }>): ReactNode {
  const periods = props.points.map((p) => p.period);
  const series: Series[] = [
    { name: locale === "ar" ? "الإيرادات" : "Revenue", values: props.points.map((p) => p.revenue ?? null), cls: "s1" },
    { name: locale === "ar" ? "المصروفات" : "Expenses", values: props.points.map((p) => p.expenses ?? null), cls: "s2" },
  ];
  if (props.points.some((p) => typeof p.profit === "number")) series.push({ name: locale === "ar" ? "الربح" : "Profit", values: props.points.map((p) => p.profit ?? null), cls: "s3" });
  return <LineChart periods={periods} series={series} locale={locale} title={props.title} currency={props.currency} />;
}

export function CashFlowChart({ props, locale }: ComponentProps<{ title?: string; currency?: string; points: { period: string; inflow?: number | null; outflow?: number | null; net?: number | null; balance?: number | null }[] }>): ReactNode {
  const periods = props.points.map((p) => p.period);
  const series: Series[] = [];
  const has = (k: "inflow" | "outflow" | "net" | "balance") => props.points.some((p) => typeof p[k] === "number");
  const names = locale === "ar" ? { inflow: "التدفقات الداخلة", outflow: "التدفقات الخارجة", net: "الصافي", balance: "الرصيد" } : { inflow: "Inflow", outflow: "Outflow", net: "Net", balance: "Balance" };
  (["inflow", "outflow", "net", "balance"] as const).forEach((k, i) => { if (has(k)) series.push({ name: names[k], values: props.points.map((p) => p[k] ?? null), cls: `s${i + 1}` }); });
  return <LineChart periods={periods} series={series} locale={locale} title={props.title} currency={props.currency} />;
}

// -- 4. FinancialDataTable --------------------------------------------------------------------------------------------
export function FinancialDataTable({ props, locale }: ComponentProps<{ title?: string; currency?: string; columns: Column[]; rows: Cell[][]; truncated?: boolean; total_rows?: number }>): ReactNode {
  return (
    <div className="ofe-block">
      <Table columns={props.columns} rows={props.rows} locale={locale} currency={props.currency} caption={props.title} />
      {props.truncated ? <div className="ofe-muted">{t(locale, "showing")} {formatNumber(locale, props.rows.length)} {t(locale, "of")} {formatNumber(locale, props.total_rows ?? props.rows.length)} {t(locale, "rows")}</div> : null}
    </div>
  );
}

// -- 5. BankBalanceSummary --------------------------------------------------------------------------------------------
export function BankBalanceSummary({ props, locale }: ComponentProps<{ as_of?: string; currency?: string; accounts: { name: string; balance: number | null; currency?: string }[]; total?: number | null }>): ReactNode {
  return (
    <div className="ofe-block ofe-bank">
      <Title>{props.as_of}</Title>
      <ul className="ofe-list">
        {props.accounts.map((a, i) => (
          <li key={i}><span>{a.name}</span><span className={"num" + ((a.balance ?? 0) < 0 ? " neg" : "")}>{formatNumber(locale, a.balance, "money", a.currency ?? props.currency)}</span></li>
        ))}
      </ul>
      {props.total !== undefined && props.total !== null ? <div className="ofe-total"><span>{t(locale, "total")}</span><span className="num">{formatNumber(locale, props.total, "money", props.currency)}</span></div> : null}
    </div>
  );
}

// -- 6. ReconciliationTable --------------------------------------------------------------------------------------------
export function ReconciliationTable({ props, locale }: ComponentProps<{ title?: string; currency?: string; columns: Column[]; rows: Cell[][]; matched?: number; unmatched?: number }>): ReactNode {
  return (
    <div className="ofe-block">
      {(props.matched !== undefined || props.unmatched !== undefined) && (
        <div className="ofe-badges">
          <span className="ofe-badge ok">{t(locale, "matched")}: {formatNumber(locale, props.matched ?? 0)}</span>
          <span className="ofe-badge warn">{t(locale, "unmatched")}: {formatNumber(locale, props.unmatched ?? 0)}</span>
        </div>
      )}
      <Table columns={props.columns} rows={props.rows} locale={locale} currency={props.currency} caption={props.title} />
    </div>
  );
}

// -- 7. UnmatchedTransactions ------------------------------------------------------------------------------------------
export function UnmatchedTransactions({ props, locale }: ComponentProps<{ title?: string; currency?: string; items: { reference: string; party?: string; date?: string; amount: number | null; currency?: string; days_overdue?: number | null; status?: string }[] }>): ReactNode {
  return (
    <div className="ofe-block">
      <Title>{props.title}</Title>
      <ul className="ofe-list ofe-items">
        {props.items.map((it, i) => (
          <li key={i}>
            <div className="ofe-item-main"><span className="ofe-ref">{it.reference}</span>{it.party ? <span className="ofe-muted">{it.party}</span> : null}</div>
            <div className="ofe-item-side">
              <span className={"num" + ((it.amount ?? 0) < 0 ? " neg" : "")}>{formatNumber(locale, it.amount, "money", it.currency ?? props.currency)}</span>
              {typeof it.days_overdue === "number" && it.days_overdue > 0 ? <span className="ofe-badge warn">{formatNumber(locale, it.days_overdue)} {t(locale, "overdueDays")}</span> : null}
              {it.date ? <span className="ofe-muted">{it.date}</span> : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// -- 8. FinancialSourceReference ---------------------------------------------------------------------------------------
export function FinancialSourceReference({ props, locale, onOpenSource }: ComponentProps<{ items: { n: number; name: string; page?: number | null; snippet?: string; source?: string; document_id?: string }[] }>): ReactNode {
  return (
    <div className="ofe-block ofe-sources">
      <Title>{t(locale, "sources")}</Title>
      <ol>
        {props.items.map((it) => (
          <li key={it.n} value={it.n}>
            {onOpenSource ? (
              <button type="button" className="ofe-linklike" onClick={() => onOpenSource({ document_id: it.document_id, page: it.page ?? null, name: it.name })}>{it.name}{it.page ? `, ${t(locale, "page")} ${it.page}` : ""}</button>
            ) : (
              <span className="ofe-source-name">{it.name}{it.page ? `, ${t(locale, "page")} ${it.page}` : ""}</span>
            )}
            {it.snippet ? <div className="ofe-muted ofe-snippet">{it.snippet}</div> : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

// -- 9. ApprovalRequestCard --------------------------------------------------------------------------------------------
export function ApprovalRequestCard({ props, locale }: ComponentProps<{ approval_id: string; tool: string; summary: string; expires_at?: string; decided_in: "desk"; approval_url?: string }>): ReactNode {
  return (
    <div className="ofe-block ofe-approval" role="status">
      <div className="ofe-title">{t(locale, "approvalTitle")}</div>
      <div className="ofe-approval-summary">{props.summary}</div>
      <div className="ofe-muted">{t(locale, "approvalBody")}</div>
      {props.expires_at ? <div className="ofe-muted">{t(locale, "expired")}: {props.expires_at}</div> : null}
      {props.approval_url && /^https:\/\//.test(props.approval_url) ? (
        <a className="ofe-btn" href={props.approval_url} target="_blank" rel="noopener noreferrer">{t(locale, "openDesk")}</a>
      ) : null}
    </div>
  );
}

// -- 10. FinancialRiskIndicator ---------------------------------------------------------------------------------------
export function FinancialRiskIndicator({ props }: ComponentProps<{ level: "info" | "warn" | "high"; title: string; detail?: string; figures?: string[] }>): ReactNode {
  return (
    <div className={`ofe-block ofe-risk ofe-risk-${props.level}`} role="note">
      <div className="ofe-title">{props.title}</div>
      {props.detail ? <div>{props.detail}</div> : null}
      {props.figures?.length ? <div className="ofe-figures">{props.figures.map((f, i) => <span key={i} className="ofe-badge">{f}</span>)}</div> : null}
    </div>
  );
}

export const RENDERERS = {
  FinancialKpiCard,
  CashFlowChart,
  RevenueExpenseChart,
  FinancialDataTable,
  BankBalanceSummary,
  ReconciliationTable,
  UnmatchedTransactions,
  FinancialSourceReference,
  ApprovalRequestCard,
  FinancialRiskIndicator,
} as const;
