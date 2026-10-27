import { useRef, useState, type ReactNode } from "react";
import { del, download, get, patch, post, upload } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtNumber, fmtRelative } from "../lib/format";
import { DataTable } from "../components/charts";
import { Comments } from "../components/comments";
import { Confirm, Empty, ErrorBox, Field, Icon, Loading, Modal, PageHead, go, useAction, useLoad } from "../components/ui";
import { tr } from "../lib/i18n";

const SOURCE: Record<string, string> = { excel: "Excel", csv: "CSV", quickbooks: "QuickBooks Online", zoho_books: "Zoho Books",
  xero: "Xero", gmail: "Gmail attachment", google_drive: "Google Drive" };

export function DataPage({ id }: { id?: string }): ReactNode {
  return id ? <DatasetView id={id} /> : <DatasetList />;
}

function DatasetList(): ReactNode {
  const { ws } = useApp();
  const list = useLoad(() => get<any[]>(wsPath(ws, "/datasets")), [ws.id]);
  const [importing, setImporting] = useState(false);
  return (
    <div className="page">
      <PageHead title={tr("Data")} sub={tr("Spreadsheets you imported and data synced from accounting systems. The assistant and dashboards read from here; your files stay in this workspace.")}
        actions={can(ws, "editor") && <button className="btn primary" onClick={() => setImporting(true)}><Icon name="upload" />{tr("Import Excel or CSV")}</button>} />
      <ErrorBox error={list.error} />
      {list.loading ? <Loading /> : (list.data || []).length === 0 ? (
        <div className="panel"><Empty title={tr("No data yet")} action={can(ws, "editor") &&
          <button className="btn primary" onClick={() => setImporting(true)}>{tr("Import a spreadsheet")}</button>}>
          {tr("Import an .xlsx or .csv file (a ledger export, a budget, a bank statement), or connect QuickBooks Online, Zoho Books or Xero under Connections to sync profit and loss, balance sheet, invoices and bills.")}</Empty></div>
      ) : (
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table className="ledger">
            <thead><tr><th>{tr("Name")}</th><th>{tr("Source")}</th><th className="num">{tr("Rows")}</th><th>{tr("Columns")}</th><th>{tr("Refreshed")}</th></tr></thead>
            <tbody>
              {(list.data || []).map((d) => (
                <tr key={d.id}>
                  <td><a href={`#/data/${d.id}`}>{d.name}</a></td>
                  <td>{SOURCE[d.source] || d.source}</td>
                  <td className="num">{fmtNumber(d.row_count, 0)}</td>
                  <td className="small muted">{d.columns.map((c: any) => c.name).join(", ").slice(0, 90)}</td>
                  <td className="small">{fmtRelative(d.refreshed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {importing && <ImportWizard onClose={() => setImporting(false)} onDone={(id) => go(`data/${id}`)} />}
    </div>
  );
}

function ImportWizard({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }): ReactNode {
  const { ws } = useApp();
  const file = useRef<HTMLInputElement>(null);
  const [up, setUp] = useState<any>(null);
  const [sheet, setSheet] = useState<string>("");
  const [preview, setPreview] = useState<any>(null);
  const [opts, setOpts] = useState({ header_row: "", decimal_comma: "auto", date_order: "auto", name: "" });
  const [unpivot, setUnpivot] = useState<{ on: boolean; ids: string[]; value: string; variable: string }>({ on: false, ids: [],
    value: "Amount", variable: "Period" });
  const { busy, run } = useAction();

  function shape(extra?: any) {
    return {
      sheet: sheet || undefined,
      header_row: opts.header_row === "" ? undefined : Number(opts.header_row) - 1,
      decimal_comma: opts.decimal_comma === "auto" ? undefined : opts.decimal_comma === "yes",
      date_order: opts.date_order,
      unpivot: unpivot.on && unpivot.ids.length ? { id_columns: unpivot.ids, value_name: unpivot.value, variable_name: unpivot.variable } : undefined,
      ...extra,
    };
  }

  async function pick(f: File) {
    const r = await run(() => upload(wsPath(ws, "/imports"), f));
    if (!r) return;
    setUp(r);
    const first = r.sheets.find((s: any) => !s.error) || r.sheets[0];
    setSheet(first.sheet);
    setPreview(first);
    setOpts((o) => ({ ...o, name: `${f.name.replace(/\.[^.]+$/, "")}${r.sheets.length > 1 ? " · " + first.sheet : ""}` }));
    if (first.period_columns?.length >= 2) {
      setUnpivot({ on: true, ids: first.columns.map((c: any) => c.name).filter((n: string) => !first.period_columns.includes(n)),
        value: "Amount", variable: "Period" });
    }
  }

  async function refresh(next?: any) {
    const p = await run(() => post(wsPath(ws, `/imports/${up.upload_id}/preview`), shape(next)));
    if (p) setPreview(p);
  }

  async function commit() {
    const ds = await run(() => post(wsPath(ws, `/imports/${up.upload_id}/commit`), shape({ name: opts.name })), tr("Imported"));
    if (ds) onDone((ds as any).id);
  }

  return (
    <Modal wide title={tr("Import a spreadsheet")} onClose={onClose} footer={up && <>
      <button className="btn" onClick={onClose}>{tr("Cancel")}</button>
      <button className="btn" disabled={busy} onClick={() => refresh()}>{tr("Update preview")}</button>
      <button className="btn primary" disabled={busy || !preview || preview.error} onClick={commit}>{tr("Import")}{" "}{preview ? fmtNumber(preview.row_count, 0) : ""}{" "}{tr("rows")}</button></>}>
      {!up ? (
        <div className="stack">
          <p>{tr("Choose an .xlsx, .xlsm or .csv file up to the upload limit. Formulas are read as their last calculated values.")}</p>
          <input ref={file} type="file" accept=".xlsx,.xlsm,.csv,.txt,.tsv" onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
          {busy && <Loading />}
        </div>
      ) : (
        <div className="stack">
          <div className="grid3">
            <Field label={tr("Sheet")}>
              <select className="select" value={sheet} onChange={(e) => {
                setSheet(e.target.value);
                const s = up.sheets.find((x: any) => x.sheet === e.target.value);
                setPreview(s);
              }}>{up.sheets.map((s: any) => <option key={s.sheet}>{s.sheet}</option>)}</select>
            </Field>
            <Field label={tr("Header row")} hint={preview ? `Detected: row ${preview.header_row + 1}` : undefined}>
              <input className="input" type="number" min={1} placeholder={tr("auto")} value={opts.header_row}
                onChange={(e) => setOpts({ ...opts, header_row: e.target.value })} /></Field>
            <Field label={tr("Dataset name")}><input className="input" value={opts.name} onChange={(e) => setOpts({ ...opts, name: e.target.value })} /></Field>
            <Field label={tr("Decimal separator")}>
              <select className="select" value={opts.decimal_comma} onChange={(e) => setOpts({ ...opts, decimal_comma: e.target.value })}>
                <option value="auto">{tr("Detect")}</option><option value="no">1,234.56</option><option value="yes">1.234,56</option>
              </select>
            </Field>
            <Field label={tr("Dates like 03/04/2026 mean")}>
              <select className="select" value={opts.date_order} onChange={(e) => setOpts({ ...opts, date_order: e.target.value })}>
                <option value="auto">{tr("Detect")}</option><option value="DMY">{tr("3 April (day first)")}</option><option value="MDY">{tr("March 4 (month first)")}</option>
              </select>
            </Field>
          </div>
          {preview && !preview.error && (
            <div className="panel panel-pad">
              <label className="check"><input type="checkbox" checked={unpivot.on} onChange={(e) => setUnpivot({ ...unpivot, on: e.target.checked })} />
                <span><strong>{tr("Periods are in columns")}</strong>{" "}{tr("(e.g. Jan, Feb, Mar across the top). Turn them into rows so dashboards can group by date.")}{preview.period_columns?.length ? ` Detected: ${preview.period_columns.join(", ")}.` : ""}</span></label>
              {unpivot.on && (
                <div className="stack" style={{ marginTop: 10 }}>
                  <div className="small">{tr("Keep these columns as they are:")}</div>
                  <div className="row wrap">{preview.columns.map((c: any) => (
                    <label key={c.key} className="check small"><input type="checkbox" checked={unpivot.ids.includes(c.name)}
                      onChange={(e) => setUnpivot({ ...unpivot, ids: e.target.checked ? [...unpivot.ids, c.name] : unpivot.ids.filter((x) => x !== c.name) })} />{c.name}</label>
                  ))}</div>
                  <div className="grid2">
                    <Field label={tr("Name for the period column")}><input className="input" value={unpivot.variable} onChange={(e) => setUnpivot({ ...unpivot, variable: e.target.value })} /></Field>
                    <Field label={tr("Name for the values column")}><input className="input" value={unpivot.value} onChange={(e) => setUnpivot({ ...unpivot, value: e.target.value })} /></Field>
                  </div>
                </div>
              )}
            </div>
          )}
          {preview?.error ? <ErrorBox error={preview.error} /> : preview && (
            <>
              <div className="small muted">{fmtNumber(preview.row_count, 0)}{" "}{tr("rows. Column types:")}{" "}
                {preview.columns.map((c: any) => `${c.name} (${c.type})`).join(", ")}</div>
              <DataTable columns={preview.columns.map((c: any) => ({ key: c.key, label: c.name, format: c.type === "number" ? "number" : c.type }))}
                rows={preview.rows} maxHeight={300} />
            </>
          )}
        </div>
      )}
    </Modal>
  );
}

function DatasetView({ id }: { id: string }): ReactNode {
  const { ws } = useApp();
  const [offset, setOffset] = useState(0);
  const d = useLoad(() => get<any>(wsPath(ws, `/datasets/${id}?offset=${offset}`)), [id, offset]);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const { run } = useAction();
  if (d.loading && !d.data) return <div className="page"><Loading /></div>;
  if (d.error) return <div className="page"><ErrorBox error={d.error} /></div>;
  const ds = d.data.dataset;
  const cols = ds.columns.map((c: any) => ({ key: c.key, label: c.name, format: c.type === "number" ? "number" : c.type }));
  return (
    <div className="page" style={{ maxWidth: 1400 }}>
      <PageHead title={ds.name} sub={`${SOURCE[ds.source] || ds.source}, ${fmtNumber(ds.row_count, 0)} rows, refreshed ${fmtRelative(ds.refreshed_at)}`}
        actions={<>
          <button className="btn" onClick={() => download(wsPath(ws, `/datasets/${id}/export.csv`), `${ds.name}.csv`)}>{tr("Export CSV")}</button>
          {can(ws, "editor") && <button className="btn" onClick={() => setRenaming(ds.name)}>{tr("Rename")}</button>}
          {can(ws, "editor") && <button className="btn danger" onClick={() => setConfirm(true)}><Icon name="trash" /></button>}
        </>} />
      <div className="kpis" style={{ marginBottom: 16 }}>
        {d.data.summary.columns.filter((c: any) => c.type === "number").slice(0, 4).map((c: any) => (
          <div className="kpi" key={c.name}><div className="label">{tr("Total")}{" "}{c.name}</div><div className="value">{fmtNumber(c.total)}</div></div>
        ))}
        {d.data.summary.columns.filter((c: any) => c.type === "date").slice(0, 1).map((c: any) => (
          <div className="kpi" key={c.name}><div className="label">{c.name}{" "}{tr("range")}</div><div className="value" style={{ fontSize: 16 }}>{c.mn}{" "}{tr("to")}{" "}{c.mx}</div></div>
        ))}
      </div>
      <DataTable columns={cols} rows={d.data.rows} maxHeight={560} />
      <div className="spread" style={{ marginTop: 8 }}>
        <span className="small muted">{tr("Rows")}{" "}{offset + 1}–{offset + d.data.rows.length}{" "}{tr("of")}{" "}{fmtNumber(ds.row_count, 0)}</span>
        <span className="row">
          <button className="btn small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 200))}>{tr("Previous")}</button>
          <button className="btn small" disabled={offset + 200 >= ds.row_count} onClick={() => setOffset(offset + 200)}>{tr("Next")}</button>
        </span>
      </div>
      <div className="section"><Comments targetType="dataset" targetId={id} /></div>
      {renaming !== null && (
        <Modal title={tr("Rename dataset")} onClose={() => setRenaming(null)} footer={<button className="btn primary" onClick={async () => {
          await run(() => patch(wsPath(ws, `/datasets/${id}`), { name: renaming }));
          setRenaming(null);
          d.reload();
        }}>{tr("Save")}</button>}>
          <input className="input" value={renaming} onChange={(e) => setRenaming(e.target.value)} aria-label={tr("Name")} />
        </Modal>
      )}
      {confirm && <Confirm text={tr("Delete this dataset? Dashboards that use it will show an error.")} onClose={() => setConfirm(false)}
        onYes={async () => { await run(() => del(wsPath(ws, `/datasets/${id}`))); go("data"); }} />}
    </div>
  );
}
