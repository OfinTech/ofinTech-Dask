// Renders typed tool output blocks. Only known block types are drawn; anything else is ignored.
import type { ReactNode } from "react";
import { fmtMoney, fmtNumber, fmtPercent } from "../lib/format";
import { DataTable } from "./charts";
import { openExternal } from "../lib/api";
import { go } from "./ui";
import { MemoryProposal } from "../pages/Memory";
import { tr } from "../lib/i18n";

export type Block = { type: string; [k: string]: any };

function kpiValue(v: unknown, format: string, currency?: string): string {
  if (format === "percent") return fmtPercent(v, 2);
  if (format === "money") return fmtMoney(v, currency);
  return fmtNumber(v);
}

export function Blocks({ blocks, currency }: { blocks: Block[]; currency?: string }): ReactNode {
  return (
    <div className="stack">
      {blocks.map((b, i) => {
        if (b.type === "kpis" && Array.isArray(b.items)) {
          return (
            <div className="kpis" key={i}>
              {b.items.map((it: any, j: number) => (
                <div className="kpi" key={j}>
                  <div className="label">{String(it.label)}</div>
                  <div className={"value" + (typeof it.value === "number" && it.value < 0 ? " neg" : "")}>
                    {kpiValue(it.value, it.format, currency)}</div>
                </div>
              ))}
            </div>
          );
        }
        if (b.type === "table" && Array.isArray(b.columns) && Array.isArray(b.rows)) {
          return (
            <div key={i}>
              {b.title && <div className="table-title">{String(b.title)}</div>}
              <DataTable columns={b.columns} rows={b.rows} currency={currency} maxHeight={340} />
              {b.truncated && <p className="xs muted">{tr("Showing 500 of")}{" "}{b.total_rows}{" "}{tr("rows.")}</p>}
            </div>
          );
        }
        if (b.type === "link") {
          const target = b.kind === "dashboard" ? `dashboards/${b.id}` : b.kind === "dataset" ? `data/${b.id}`
            : b.kind === "document" ? `documents/${b.id}` : b.kind === "file" ? `files/${b.id}`
            : b.kind === "workflow_run" ? `workflows/${b.workflow_id}?run=${b.id}` : null;
          return target ? (
            <button key={i} className="btn small" onClick={() => go(target)}>{tr("Open")}{" "}{String(b.label)}</button>
          ) : null;
        }
        if (b.type === "sources" && Array.isArray(b.items)) {
          return (
            <ol className="sources" key={i}>
              {b.items.map((it: any) => (
                <li key={it.n} value={it.n}>
                  <button className="linklike" onClick={() => it.document_id
                    ? go(`documents/${it.document_id}${it.page ? `?page=${it.page}` : ""}`)
                    : it.link && openExternal(it.link)}>
                    {String(it.name)}{it.page ? `, page ${it.page}` : ""}</button>
                  <div className="xs muted">{String(it.snippet || "").slice(0, 220)}…</div>
                </li>
              ))}
            </ol>
          );
        }
        if (b.type === "memory" && b.memory) {
          return <MemoryProposal key={i} memory={b.memory} />;
        }
        if (b.type === "skill") {
          return <span key={i} className="tag blue">{tr("Skill loaded:")}{" "}{String(b.name)}</span>;
        }
        return null;
      })}
    </div>
  );
}
