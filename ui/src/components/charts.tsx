// SVG charts: bars (single or stacked), lines and areas, with a hover tooltip, a legend for two or
// more series and a table view. Colours come from the validated categorical palette (--series-N),
// assigned in fixed order by series identity.
import type React from "react";
import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { fmtCompact, fmtMoney, fmtNumber, formatCell } from "../lib/format";
import { tr } from "../lib/i18n";

export type Column = { key: string; label: string; format?: string };
export type Series = { name: string; values: (number | null)[] };

const H = 260;

/** Width of the chart container in CSS pixels, so the SVG draws 1:1 (text stays 11px at any size). */
function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => setW(Math.max(260, Math.round(entries[0].contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}
const PAD = { l: 56, r: 12, t: 12, b: 34 };
const MAX_SERIES = 8;

export function color(i: number): string {
  return `var(--series-${(i % MAX_SERIES) + 1})`;
}

/** First group column → categories; optional second group column → series; first measure → values. */
export function toSeries(columns: Column[], rows: unknown[][]): { categories: string[]; series: Series[]; format?: string } {
  const groups = columns.filter((c) => c.key.startsWith("g"));
  const measures = columns.filter((c) => c.key.startsWith("m"));
  const gi = columns.indexOf(groups[0]);
  const mi = columns.indexOf(measures[0]);
  if (gi < 0 || mi < 0) return { categories: [], series: [] };
  const categories: string[] = [];
  for (const r of rows) {
    const c = String(r[gi] ?? "—");
    if (!categories.includes(c)) categories.push(c);
  }
  if (groups.length > 1) {
    const si = columns.indexOf(groups[1]);
    const names: string[] = [];
    for (const r of rows) {
      const n = String(r[si] ?? "—");
      if (!names.includes(n)) names.push(n);
    }
    let kept = names;
    if (names.length > MAX_SERIES) kept = names.slice(0, MAX_SERIES - 1);
    const series: Series[] = kept.map((n) => ({ name: n, values: categories.map(() => null) }));
    if (names.length > MAX_SERIES) series.push({ name: "Other", values: categories.map(() => null) });
    for (const r of rows) {
      const name = String(r[si] ?? "—");
      const idx = kept.includes(name) ? kept.indexOf(name) : series.length - 1;
      const ci = categories.indexOf(String(r[gi] ?? "—"));
      const v = Number(r[mi]) || 0;
      series[idx].values[ci] = (series[idx].values[ci] || 0) + v;
    }
    return { categories, series, format: measures[0].format };
  }
  const values = categories.map((c) => {
    const row = rows.find((r) => String(r[gi] ?? "—") === c);
    return row && row[mi] !== null ? Number(row[mi]) : null;
  });
  return { categories, series: [{ name: measures[0].label, values }], format: measures[0].format };
}

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) { max = min + 1; }
  const span = max - min;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || step0;
  const ticks: number[] = [];
  for (let v = Math.floor(min / step) * step; v <= max + step * 0.001; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

function label(c: string): string {
  return c.length > 12 ? c.slice(0, 11) + "…" : c;
}

function valueText(v: number | null, format?: string, currency?: string): string {
  if (v === null) return "—";
  return format === "money" ? fmtMoney(v, currency) : fmtNumber(v);
}

type Props = { categories: string[]; series: Series[]; kind: "bar" | "stacked_bar" | "line" | "area"; format?: string;
  currency?: string; title?: string };

export function Chart(props: Props): ReactNode {
  const { categories, series, kind, format, currency } = props;
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const ref = useRef<SVGSVGElement>(null);
  const [box, W] = useWidth();
  const stacked = kind === "stacked_bar";
  const n = categories.length;

  const { lo, hi, ticks } = useMemo(() => {
    let lo = 0;
    let hi = 0;
    for (let i = 0; i < n; i++) {
      if (stacked) {
        let pos = 0, neg = 0;
        for (const s of series) { const v = s.values[i] || 0; if (v >= 0) pos += v; else neg += v; }
        hi = Math.max(hi, pos); lo = Math.min(lo, neg);
      } else {
        for (const s of series) { const v = s.values[i]; if (v !== null) { hi = Math.max(hi, v); lo = Math.min(lo, v); } }
      }
    }
    const ticks = niceTicks(lo, hi);
    return { lo: Math.min(lo, ticks[0]), hi: Math.max(hi, ticks[ticks.length - 1]), ticks };
  }, [categories, series, stacked, n]);

  if (n === 0) return <p className="muted small">{tr("No data for this selection.")}</p>;
  const pw = W - PAD.l - PAD.r;
  const ph = H - PAD.t - PAD.b;
  const y = (v: number) => PAD.t + ph - ((v - lo) / (hi - lo || 1)) * ph;
  const band = pw / n;
  const x = (i: number) => PAD.l + band * i + band / 2;
  const every = Math.ceil(n / Math.max(2, Math.floor(pw / 70)));

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const svg = ref.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.floor((px - PAD.l) / band);
    setHover(i >= 0 && i < n ? i : null);
  };

  const marks: ReactNode[] = [];
  if (kind === "bar" || kind === "stacked_bar") {
    const bw = Math.max(Math.min(band * (stacked || series.length === 1 ? 0.62 : 0.8) / (stacked ? 1 : series.length), 46), 3);
    for (let i = 0; i < n; i++) {
      let pos = 0, neg = 0;
      series.forEach((s, si) => {
        const v = s.values[i];
        if (v === null || v === 0) return;
        let y0: number, y1: number;
        if (stacked) {
          if (v >= 0) { y0 = y(pos); y1 = y(pos + v); pos += v; } else { y0 = y(neg); y1 = y(neg + v); neg += v; }
        } else { y0 = y(0); y1 = y(v); }
        const left = stacked || series.length === 1 ? x(i) - bw / 2 : x(i) - (bw * series.length) / 2 + si * bw;
        const top = Math.min(y0, y1);
        const h = Math.max(Math.abs(y1 - y0) - (stacked ? 2 : 0), 1);
        const r = Math.min(4, bw / 2, h / 2);
        const up = v >= 0;
        const w = stacked || series.length === 1 ? bw : bw - 2;
        // rounded only on the data end, square on the baseline
        const d = up
          ? `M${left},${top + h} V${top + r} Q${left},${top} ${left + r},${top} H${left + w - r} Q${left + w},${top} ${left + w},${top + r} V${top + h} Z`
          : `M${left},${top} V${top + h - r} Q${left},${top + h} ${left + r},${top + h} H${left + w - r} Q${left + w},${top + h} ${left + w},${top + h - r} V${top} Z`;
        marks.push(<path key={`${si}-${i}`} d={d} fill={color(si)} opacity={hover === null || hover === i ? 1 : 0.55} />);
      });
    }
  } else {
    series.forEach((s, si) => {
      const pts = s.values.map((v, i) => (v === null ? null : [x(i), y(v)] as const));
      let d = "";
      pts.forEach((p, i) => { if (p) d += `${d && pts[i - 1] ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)} `; });
      if (kind === "area" && series.length === 1) {
        const first = pts.findIndex(Boolean);
        const last = pts.length - 1 - [...pts].reverse().findIndex(Boolean);
        if (first >= 0) marks.push(<path key={`a${si}`} d={`${d} L${x(last)},${y(Math.max(lo, 0))} L${x(first)},${y(Math.max(lo, 0))} Z`}
          fill={color(si)} opacity={0.14} />);
      }
      marks.push(<path key={`l${si}`} d={d} fill="none" stroke={color(si)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />);
      if (hover !== null && pts[hover]) {
        marks.push(<circle key={`c${si}`} cx={pts[hover]![0]} cy={pts[hover]![1]} r={4.5} fill={color(si)}
          stroke="var(--panel)" strokeWidth={2} />);
      }
    });
  }

  const tipLeft = hover !== null ? Math.min(Math.max((x(hover) / W) * 100, 8), 78) : 0;
  return (
    <div className="chart-wrap" ref={box}>
      {showTable ? (
        <ChartTable categories={categories} series={series} format={format} currency={currency} />
      ) : (
        <svg ref={ref} className="chart" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={props.title || tr("Chart")}
          onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line className={t === 0 ? "axis" : "grid"} x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} />
              <text x={PAD.l - 8} y={y(t) + 4} textAnchor="end">{fmtCompact(t)}</text>
            </g>
          ))}
          {categories.map((c, i) => (i % every === 0 ? (
            <text key={c + i} x={x(i)} y={H - 12} textAnchor="middle">{label(c)}</text>
          ) : null))}
          {(kind === "line" || kind === "area") && hover !== null && (
            <line className="hover-line" x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={PAD.t + ph} />
          )}
          {marks}
        </svg>
      )}
      {!showTable && hover !== null && (
        <div className="tip" style={{ left: `${tipLeft}%`, top: 8 }}>
          <div className="muted">{categories[hover]}</div>
          {series.map((s, si) => (
            <div key={si} className="row"><i style={{ width: 8, height: 8, background: color(si), borderRadius: 2,
              display: "inline-block" }} />{series.length > 1 ? `${s.name}: ` : ""}<b>{valueText(s.values[hover], format, currency)}</b></div>
          ))}
        </div>
      )}
      <div className="spread">
        {series.length > 1 ? (
          <div className="legend">{series.map((s, i) => <span key={i}><i style={{ background: color(i) }} />{s.name}</span>)}</div>
        ) : <span />}
        <button className="btn ghost small" onClick={() => setShowTable(!showTable)}>{showTable ? tr("Show chart") : tr("Show table")}</button>
      </div>
    </div>
  );
}

function ChartTable({ categories, series, format, currency }: { categories: string[]; series: Series[]; format?: string;
  currency?: string }): ReactNode {
  return (
    <div className="table-wrap" style={{ maxHeight: 260 }}>
      <table className="ledger">
        <thead><tr><th /> {series.map((s, i) => <th key={i} className="num">{s.name}</th>)}</tr></thead>
        <tbody>
          {categories.map((c, ci) => (
            <tr key={ci}><td>{c}</td>{series.map((s, si) => (
              <td key={si} className={"num" + ((s.values[ci] ?? 0) < 0 ? " neg" : "")}>{valueText(s.values[ci], format, currency)}</td>
            ))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DataTable({ columns, rows, currency, maxHeight }: { columns: Column[]; rows: unknown[][]; currency?: string;
  maxHeight?: number }): ReactNode {
  const numeric = columns.map((c, i) => ["money", "number", "percent", "ratio", "days"].includes(c.format || "") ||
    (rows.length > 0 && rows.every((r) => r[i] === null || typeof r[i] === "number")));
  return (
    <div className="table-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className="ledger">
        <thead><tr>{columns.map((c, i) => <th key={c.key + i} className={numeric[i] ? "num" : ""}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri}>{columns.map((c, i) => (
              <td key={i} className={(numeric[i] ? "num" : "") + (typeof r[i] === "number" && (r[i] as number) < 0 ? " neg" : "")}>
                {formatCell(r[i], c.format, currency)}
              </td>
            ))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
