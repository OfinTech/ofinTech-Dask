import { useEffect, useMemo, useState, type ReactNode } from "react";
import { del, download, get, patch, post, upload } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtDateTime, fmtNumber, fmtPercent, fmtRelative } from "../lib/format";
import { DataTable } from "../components/charts";
import { Comments } from "../components/comments";
import { Confirm, Empty, ErrorBox, Icon, Loading, PageHead, StatusTag, go, query, useAction, useLoad } from "../components/ui";
import { N, tr } from "../lib/i18n";

type Cell = { v?: any; f?: string; st?: string; x?: string; e?: string; s?: string; n?: string; d?: any; a?: string };
type Finding = { id: string; target: string; severity: "high" | "medium" | "low"; title: string; body: string;
  origin: "check" | "reviewer"; status: "open" | "resolved" | "dismissed"; decided_by_name?: string };
type Selection = { kind: "cell"; a: string } | { kind: "source"; s: string } | { kind: "calc"; c: string } | { kind: "para"; id: string };

export function fmtFig(v: any, f?: string): string {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v !== "number") return String(v);
  if (f === "percent") return fmtPercent(v);
  if (f === "integer") return fmtNumber(v, 0);
  // the same display as the Excel number formats: two decimals, negatives in brackets for money
  const two = (x: number) => x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (f === "money" && v < 0) return `(${two(-v)})`;
  return two(v);
}

const SEV: Record<string, string> = { high: "red", medium: "amber", low: "blue" };

export function FilesPage({ id }: { id?: string }): ReactNode {
  return id ? <FileView id={id} /> : <FileList />;
}

function Verification({ s }: { s: any }): ReactNode {
  if (!s) return null;
  if (s.edits !== undefined) return (
    <span className="xs">{s.edits === 0 ? tr("original, not edited yet") : <>
      <span className="fig-key sourced">{s.sourced}{" "}{tr("traced")}</span>{" · "}<span className="fig-key computed">{s.formulas}{" "}{tr("formulas")}</span>
      {s.unverified > 0 && <>{" · "}<span className="fig-key unverified">{s.unverified}{" "}{tr("without source")}</span></>}</>}</span>);
  const inputs = (s.cells_sourced || 0) + (s.cells_unverified || 0);
  return (
    <span className="xs">
      <span className="fig-key sourced">{s.cells_sourced}{" "}{tr("traced")}</span>
      {" · "}<span className="fig-key computed">{s.cells_computed}{" "}{tr("formulas")}</span>
      {s.cells_unverified > 0 && <>{" · "}<span className="fig-key unverified">{s.cells_unverified}{" "}{tr("without source")}</span></>}
      {inputs === 0 && s.cells_computed === 0 ? tr(" · no figures") : ""}
    </span>
  );
}

const KIND_LABEL: Record<string, string> = { report: N("Report: Word and Excel"), workbook: N("Workbook: Excel"),
  user_xlsx: N("Your Excel file"), user_docx: N("Your Word file") };

function FileList(): ReactNode {
  const { ws } = useApp();
  const files = useLoad(() => get<any[]>(wsPath(ws, "/files")), [ws.id]);
  const { busy, run } = useAction();
  async function add(file: File | undefined) {
    if (!file) return;
    const r: any = await run(() => upload(wsPath(ws, "/files/upload"), file), tr("File added"));
    if (r?.file) go(`files/${r.file.id}`);
  }
  useEffect(() => {
    if ((files.data || []).some((f) => f.review_status === "running")) {
      const t = setTimeout(files.reload, 2500);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [files.data]);
  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <PageHead title={tr("Files")} sub={tr("Workbooks and reports the assistant prepared, and your own Excel and Word files it fills in. Every figure is traced to its source or is a live formula, and every change is kept as a version.")}
        actions={can(ws, "editor") && <label className={`btn primary ${busy ? "disabled" : ""}`}>
          <Icon name="upload" />{tr("Add your Excel or Word file")}
          <input type="file" hidden accept=".xlsx,.docx" disabled={busy} onChange={(e) => { void add(e.target.files?.[0]); e.target.value = ""; }} />
        </label>} />
      <ErrorBox error={files.error} />
      {files.loading && !files.data ? <Loading /> : (files.data || []).length === 0 ? (
        <div className="panel"><Empty title={tr("No files yet")}>{tr("Ask the assistant for a workbook or a report, for example “Build a Q1 actual vs budget workbook from the P&L” or “Write the month-end commentary for March”.")}</Empty></div>
      ) : (
        <div className="table-wrap" style={{ maxHeight: "none" }}>
          <table className="ledger">
            <thead><tr><th>{tr("Name")}</th><th>{tr("Figures")}</th><th>{tr("Review")}</th><th className="num">{tr("Version")}</th><th>{tr("Updated")}</th></tr></thead>
            <tbody>{(files.data || []).map((f) => (
              <tr key={f.id}>
                <td><a href={`#/files/${f.id}`}>{f.name}</a>
                  <div className="xs muted">{tr(KIND_LABEL[f.kind] || f.kind)}</div></td>
                <td><Verification s={f.summary} /></td>
                <td>{String(f.kind).startsWith("user_") ? <span className="xs muted">{tr("checked as edited")}</span> : f.review_status === "running" ? <StatusTag status="running" /> : f.open_findings > 0
                  ? <span className="tag amber">{f.open_findings}{" "}{tr("open")}</span>
                  : f.review_status === "done" ? <span className="tag green">{tr("clear")}</span>
                    : f.review_status === "error" ? <StatusTag status="error" /> : <span className="tag">{tr("not reviewed")}</span>}</td>
                <td className="num">{f.current_version}</td>
                <td className="small">{fmtRelative(f.updated_at)}{f.created_by_name ? `, ${f.created_by_name}` : ""}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function FileView({ id }: { id: string }): ReactNode {
  const { ws } = useApp();
  const [version, setVersion] = useState<number | null>(null);
  const f = useLoad(() => get<any>(wsPath(ws, `/files/${id}${version ? `?version=${version}` : ""}`)), [ws.id, id, version]);
  const [tab, setTab] = useState<string>("");
  const [sel, setSel] = useState<Selection | null>(() => {
    const a = query().get("cell");
    return a ? { kind: "cell", a } : null;
  });
  const [confirm, setConfirm] = useState(false);
  const { busy, run } = useAction();
  const editor = can(ws, "editor");
  const data = f.data;
  const model = data?.model;
  const latest = data && data.version === data.file.current_version;

  useEffect(() => {
    if (data?.file.review_status === "running") {
      const t = setTimeout(f.reload, 2000);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [data]);
  useEffect(() => {
    if (model && !tab && model.kind !== "upload") setTab(model.kind === "report" ? "__report" : model.sheets[0]?.name);
  }, [model]);

  const findings: Finding[] = data?.findings || [];
  const openByTarget = useMemo(() => {
    const m: Record<string, Finding[]> = {};
    for (const x of findings) if (x.status === "open") (m[x.target] ||= []).push(x);
    return m;
  }, [findings]);

  if (f.error) return <div className="page"><ErrorBox error={f.error} /><button className="btn" onClick={() => go("files")}>{tr("Back to files")}</button></div>;
  if (!data) return <div className="page"><Loading /></div>;

  function selectTarget(target: string) {
    if (target === "file") { setSel(null); return; }
    if (/^P\d+$/.test(target)) { setTab("__report"); setSel({ kind: "para", id: target }); return; }
    const sheet = target.split("!")[0];
    if (model.sheets.some((s: any) => s.name === sheet)) setTab(sheet);
    setSel({ kind: "cell", a: target });
  }

  const file = data.file;
  if (model.kind === "upload") return <UploadView id={id} data={data} onVersion={(v) => setVersion(v)} reload={f.reload} />;
  return (
    <div className="page file-page">
      <PageHead title={file.name}
        sub={<>
          <button className="linklike" onClick={() => go("files")}>{tr("Files")}</button>{" / "}
          {model.kind === "report" ? tr("Report") : tr("Workbook")}{tr(", version")}{" "}{data.version}{data.note ? ` — ${data.note}` : ""}
        </>}
        actions={<>
          {data.versions.length > 1 && <select className="select" style={{ width: 150 }} aria-label={tr("Version")} value={data.version}
            onChange={(e) => { setVersion(Number(e.target.value)); setSel(null); }}>
            {data.versions.map((v: any) => <option key={v.version} value={v.version}>{tr("Version")}{" "}{v.version}</option>)}</select>}
          <button className="btn primary" onClick={() => run(() => download(wsPath(ws, `/files/${id}/download?format=xlsx&version=${data.version}`),
            `${file.name} v${data.version}.xlsx`))}><Icon name="data" />{tr("Excel")}</button>
          {data.formats.includes("docx") && <button className="btn primary" onClick={() => run(() => download(
            wsPath(ws, `/files/${id}/download?format=docx&version=${data.version}`), `${file.name} v${data.version}.docx`))}>
            <Icon name="docs" />{tr("Word")}</button>}
          {editor && latest && <button className="btn" disabled={busy} title={tr("Run the queries again and save a new version")}
            onClick={() => run(() => post(wsPath(ws, `/files/${id}/refresh`)), tr("New version saved")).then(() => { setVersion(null); f.reload(); })}>
            <Icon name="refresh" />{tr("Refresh data")}</button>}
          {editor && latest && <button className="btn" disabled={busy || file.review_status === "running"}
            onClick={() => run(() => post(wsPath(ws, `/files/${id}/review`)), tr("Review started")).then(f.reload)}>{tr("Review again")}</button>}
          {editor && <button className="btn ghost" aria-label={tr("Delete file")} onClick={() => setConfirm(true)}><Icon name="trash" /></button>}
        </>} />

      <div className="file-layout">
        <div className="stack" style={{ minWidth: 0 }}>
          <div className="seg sheet-tabs" role="tablist" aria-label={tr("Sheets")}>
            {model.kind === "report" && <button role="tab" aria-pressed={tab === "__report"} onClick={() => setTab("__report")}>{tr("Report")}</button>}
            {model.sheets.map((s: any) => <button key={s.name} role="tab" aria-pressed={tab === s.name} onClick={() => setTab(s.name)}>{s.name}</button>)}
          </div>
          {tab === "__report" ? <ReportView model={model} sel={sel} onSelect={setSel} openByTarget={openByTarget} />
            : model.sheets.filter((s: any) => s.name === tab).map((s: any) => (
              <SheetView key={s.name} sheet={s} sel={sel} onSelect={setSel} openByTarget={openByTarget} />))}
          <p className="xs muted legend-line">
            <span className="fig-key sourced">{tr("Blue")}</span>{" "}{tr("figures are inputs traced to a source,")}{" "}
            <span className="fig-key computed">{tr("black")}</span>{" "}{tr("ones are live formulas,")}{" "}
            <span className="fig-key unverified">{tr("shaded")}</span>{" "}{tr("ones have no source. Select any figure to see its proof.")}
          </p>
          <Comments targetType="file" targetId={id} />
        </div>
        <aside className="evidence" aria-label={tr("Evidence")}>
          {sel ? <Evidence id={id} version={data.version} model={model} sel={sel} findings={findings}
            onClose={() => setSel(null)} onDecide={f.reload} editor={editor} />
            : <Overview data={data} findings={findings} onTarget={selectTarget} onDecide={f.reload} editor={editor} />}
        </aside>
      </div>
      {confirm && <Confirm text={tr("Delete {0} and all its versions?", { 0: file.name })} onClose={() => setConfirm(false)}
        onYes={async () => { await run(() => del(wsPath(ws, `/files/${id}`))); go("files"); }} />}
    </div>
  );
}

function cellClass(c: Cell, selected: boolean, flagged: boolean): string {
  return ["fig", c.st || "", selected ? "selected" : "", flagged ? "flagged" : "", c.f && c.f !== "text" ? "num" : ""].join(" ");
}

function SheetView({ sheet, sel, onSelect, openByTarget }: { sheet: any; sel: Selection | null;
  onSelect: (s: Selection) => void; openByTarget: Record<string, Finding[]> }): ReactNode {
  const selected = sel?.kind === "cell" ? sel.a : null;
  return (
    <div className="stack">
      {sheet.blocks.map((b: any, i: number) => {
        if (b.type === "heading") return <h2 key={i} className="sheet-heading">{b.text}</h2>;
        if (b.type === "text") return <p key={i} className="small" style={{ maxWidth: "80ch" }}>{b.text}</p>;
        if (b.type === "note") return <p key={i} className="small muted" style={{ maxWidth: "80ch", fontStyle: "italic" }}>{b.text}</p>;
        if (b.type === "kpis") {
          return (
            <div key={i} className="kpis">
              {b.items.map((it: any) => (
                <button key={it.cell.a} className={"kpi kpi-btn" + (selected === it.cell.a ? " selected" : "")}
                  onClick={() => onSelect({ kind: "cell", a: it.cell.a })}>
                  <div className="label">{it.label}</div>
                  <div className="value">{fmtFig(it.cell.v, it.cell.f) || "—"}</div>
                  <div className="xs muted">{it.cell.a.split("!")[1]}{" "}{tr("· formula")}</div>
                </button>))}
            </div>
          );
        }
        const rows: Cell[][] = [...b.rows, ...(b.total ? [b.total] : [])];
        return (
          <div key={i} className="table-wrap wp" style={{ maxHeight: "none" }}>
            {b.title && <div className="table-title">{b.title}</div>}
            <table className="ledger workpaper">
              <thead><tr><th className="rowno" aria-label={tr("Row")} />
                {b.columns.map((c: any) => <th key={c.key} className={c.format !== "text" ? "num" : ""}>
                  <span className="colletter">{c.letter}</span>{c.label}
                  {c.formula && <span className="colformula" title={c.formula}>ƒ</span>}</th>)}</tr></thead>
              <tbody>{rows.map((cells, ri) => {
                const isTotal = b.total && ri === rows.length - 1;
                const rowNo = cells[0]?.a?.split("!")[1].replace(/^[A-Z]+/, "");
                return (
                  <tr key={ri} className={isTotal ? "total" : ""}>
                    <td className="rowno">{rowNo}</td>
                    {cells.map((c, ci) => {
                      const clickable = c.st && c.st !== "label" && c.st !== "empty";
                      const text = fmtFig(c.v, c.f);
                      const flagged = !!(c.a && openByTarget[c.a]);
                      return (
                        <td key={ci} className={c.f && c.f !== "text" ? "num" : ""}>
                          {clickable ? <button className={cellClass(c, selected === c.a, flagged)} onClick={() => onSelect({ kind: "cell", a: c.a! })}
                            aria-label={`${c.a}: ${text || "blank"}${c.st === "unverified" ? ", no source" : ""}`}>{text || "–"}</button>
                            : <span className={flagged ? "fig flagged" : ""}>{text}</span>}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

function ReportView({ model, sel, onSelect, openByTarget }: { model: any; sel: Selection | null; onSelect: (s: Selection) => void;
  openByTarget: Record<string, Finding[]> }): ReactNode {
  const tables: Record<string, any> = {};
  for (const s of model.sheets) for (const b of s.blocks) if (b.type === "table") tables[b.id] = b;

  function runs(p: any): ReactNode {
    return p.runs.map((r: any, i: number) => {
      if (!r.fig) return <span key={i}>{r.t}</span>;
      const fig = r.fig;
      const marker = fig.st === "sourced" ? fig.s : fig.st === "computed" ? fig.c : null;
      const pick = () => onSelect(fig.addr ? { kind: "cell", a: fig.addr } : fig.s ? { kind: "source", s: fig.s }
        : fig.c ? { kind: "calc", c: fig.c } : { kind: "para", id: p.id });
      return (
        <button key={i} className={`fig inline ${fig.st}`} onClick={pick}>
          {r.t}{marker && <sup>{marker}</sup>}
        </button>
      );
    });
  }

  return (
    <article className="report panel">
      <h1>{model.title}</h1>
      {model.subtitle && <p className="muted">{model.subtitle}</p>}
      {model.report.sections.map((sec: any, si: number) => (
        <section key={si}>
          <h2>{sec.heading}</h2>
          {sec.blocks.map((b: any, bi: number) => {
            if (b.type === "paragraph") {
              return <p key={bi} id={b.id} className={(sel?.kind === "para" && sel.id === b.id ? "para-sel " : "")
                + (openByTarget[b.id] ? "para-flag" : "")} onDoubleClick={() => onSelect({ kind: "para", id: b.id })}>{runs(b)}
                {openByTarget[b.id] && <button className="para-badge" onClick={() => onSelect({ kind: "para", id: b.id })}
                  aria-label={tr("{0} review findings", { 0: openByTarget[b.id].length })}>{openByTarget[b.id].length}</button>}</p>;
            }
            if (b.type === "bullets") {
              return <ul key={bi}>{b.items.map((it: any) => (
                <li key={it.id} className={(sel?.kind === "para" && sel.id === it.id ? "para-sel " : "") + (openByTarget[it.id] ? "para-flag" : "")}>
                  {runs(it)}{openByTarget[it.id] && <button className="para-badge" onClick={() => onSelect({ kind: "para", id: it.id })}>
                    {openByTarget[it.id].length}</button>}</li>))}</ul>;
            }
            const t = tables[b.table];
            return t ? <div key={bi} className="report-table"><SheetView sheet={{ blocks: [t] }} sel={sel} onSelect={onSelect}
              openByTarget={openByTarget} /></div> : null;
          })}
        </section>
      ))}
    </article>
  );
}

function FindingItem({ x, onTarget, onDecide, editor }: { x: Finding; onTarget?: (t: string) => void; onDecide: () => void;
  editor: boolean }): ReactNode {
  const { ws } = useApp();
  const { busy, run } = useAction();
  const fid = location.hash.split("/")[2]?.split("?")[0];
  const decide = (status: string) => run(() => patch(wsPath(ws, `/files/${fid}/findings/${x.id}`), { status })).then(onDecide);
  return (
    <li className={`finding ${x.status !== "open" ? "closed" : ""}`}>
      <div className="spread">
        <span><span className={`tag ${SEV[x.severity]}`}>{x.severity}</span>{" "}
          <span className="xs muted">{x.origin === "reviewer" ? tr("Reviewer") : tr("Automatic check")}</span></span>
        {onTarget && x.target !== "file" ? <button className="linklike xs" onClick={() => onTarget(x.target)}>{x.target}</button>
          : <span className="xs muted">{x.target === "file" ? tr("Whole file") : x.target}</span>}
      </div>
      <div className="small finding-title">{x.title}</div>
      {x.body && <div className="xs muted">{x.body}</div>}
      {editor && (x.status === "open" ? (
        <div className="row" style={{ marginTop: 6 }}>
          <button className="btn small" disabled={busy} onClick={() => decide("resolved")}>{tr("Resolved")}</button>
          <button className="btn small ghost" disabled={busy} onClick={() => decide("dismissed")}>{tr("Dismiss")}</button>
        </div>
      ) : <div className="xs muted">{x.status}{x.decided_by_name ? ` by ${x.decided_by_name}` : ""} ·{" "}
        <button className="linklike xs" disabled={busy} onClick={() => decide("open")}>{tr("Reopen")}</button></div>)}
    </li>
  );
}

function Overview({ data, findings, onTarget, onDecide, editor }: { data: any; findings: Finding[]; onTarget: (t: string) => void;
  onDecide: () => void; editor: boolean }): ReactNode {
  const s = data.model.summary;
  const open = findings.filter((x) => x.status === "open");
  const closed = findings.filter((x) => x.status !== "open");
  const file = data.file;
  return (
    <div className="stack">
      <div>
        <h3>{tr("Figures")}</h3>
        <dl className="tally">
          <div><dt>{tr("Traced to a source")}</dt><dd className="fig-key sourced">{s.cells_sourced + s.text_sourced}</dd></div>
          <div><dt>{tr("Live formulas")}</dt><dd className="fig-key computed">{s.cells_computed + s.text_computed}</dd></div>
          <div><dt>{tr("Without a source")}</dt><dd className={s.cells_unverified + s.text_unverified ? "fig-key unverified" : ""}>
            {s.cells_unverified + s.text_unverified}</dd></div>
        </dl>
      </div>
      <div>
        <div className="spread"><h3>{tr("Review")}</h3>
          {file.review_status === "running" ? <StatusTag status="running" /> : file.review_status === "error"
            ? <StatusTag status="error" /> : null}</div>
        {file.review_status === "running" && <p className="xs muted">{tr("An independent reviewer is checking this file. It sees only the file and its sources, not the conversation.")}</p>}
        {file.review_error && <div className="notice error xs">{file.review_error}</div>}
        {open.length === 0 && file.review_status === "done" && <p className="small">{tr("No open findings.")}</p>}
        {file.review_status === "none" && open.length === 0 && <p className="small muted">{tr("Not reviewed yet.")}</p>}
        <ul className="findings">{open.map((x) => <FindingItem key={x.id} x={x} onTarget={onTarget} onDecide={onDecide} editor={editor} />)}</ul>
        {closed.length > 0 && <details><summary className="xs muted">{closed.length}{" "}{tr("closed")}</summary>
          <ul className="findings">{closed.map((x) => <FindingItem key={x.id} x={x} onTarget={onTarget} onDecide={onDecide} editor={editor} />)}</ul>
        </details>}
      </div>
      {data.changes && (
        <div>
          <h3>{tr("Changed since version")}{" "}{data.version - 1}</h3>
          {data.changes.length === 0 ? <p className="small muted">{tr("No figure changed.")}</p> : (
            <ul className="changes">{data.changes.slice(0, 40).map((c: any) => (
              <li key={c.a}><button className="linklike xs" onClick={() => onTarget(c.a)}>{c.a}</button>
                <span className="xs"> {fmtFig(c.before, c.f)} → <strong>{fmtFig(c.after, c.f)}</strong></span></li>))}</ul>
          )}
        </div>
      )}
      <div>
        <h3>{tr("Sources")}</h3>
        <ol className="sources">{data.model.sources.map((src: any) => (
          <li key={src.id} value={Number(src.id.slice(1))}>
            {src.link ? <button className="linklike small" onClick={() => go(src.link)}>{src.title}</button> : <span className="small">{src.title}</span>}
            <div className="xs muted">{src.system}{src.at ? `, ${fmtDateTime(src.at)}` : ""}</div>
          </li>))}</ol>
      </div>
      <p className="xs muted">{tr("Versions:")}{" "}{data.versions.map((v: any) => `v${v.version} ${fmtRelative(v.created_at)}`).join(" · ")}</p>
    </div>
  );
}

function Evidence({ id, version, model, sel, findings, onClose, onDecide, editor }: { id: string; version: number; model: any;
  sel: Selection; findings: Finding[]; onClose: () => void; onDecide: () => void; editor: boolean }): ReactNode {
  const { ws } = useApp();
  const addr = sel.kind === "cell" ? sel.a : null;
  const cell = useLoad(() => addr ? get<any>(wsPath(ws, `/files/${id}/cell?a=${encodeURIComponent(addr)}&version=${version}`))
    : Promise.resolve(null), [addr, version]);
  const head = (title: string, sub?: string) => (
    <div className="spread"><div><div className="xs muted">{sub}</div><h3 style={{ margin: 0 }}>{title}</h3></div>
      <button className="btn ghost small" onClick={onClose} aria-label={tr("Close")}><Icon name="x" /></button></div>
  );

  if (sel.kind === "source") {
    const src = model.sources.find((s: any) => s.id === sel.s);
    return <div className="stack">{head(sel.s, "Source")}{src && <SourceCard src={src} />}</div>;
  }
  if (sel.kind === "calc") {
    const c = (model.calculations || []).find((x: any) => x.id === sel.c);
    return <div className="stack">{head(fmtFig(c?.v, c?.f) || "—", `Calculation ${sel.c}`)}
      {c && <><div className="formula">{c.e}</div><code className="excel">{c.x}</code>
        <p className="xs muted">{tr("Calculated from the workbook's cells; the Sources sheet of the Excel file holds the same formula.")}</p></>}</div>;
  }
  if (sel.kind === "para") {
    const fs = findings.filter((x) => x.target === sel.id);
    return <div className="stack">{head(sel.id, "Paragraph")}
      {fs.length === 0 ? <p className="small muted">{tr("No findings on this paragraph.")}</p>
        : <ul className="findings">{fs.map((x) => <FindingItem key={x.id} x={x} onDecide={onDecide} editor={editor} />)}</ul>}</div>;
  }
  if (cell.loading && !cell.data) return <div className="stack">{head(sel.a, "Cell")}<Loading /></div>;
  if (cell.error) return <div className="stack">{head(sel.a, "Cell")}<ErrorBox error={cell.error} /></div>;
  const d = cell.data;
  const c: Cell = d.cell;
  const status = c.st === "sourced" ? <span className="tag blue">{tr("Input · traced to")}{" "}{c.s}</span>
    : c.st === "computed" ? <span className="tag">{tr("Formula")}</span>
      : c.st === "unverified" ? <span className="tag amber">{tr("No source found")}</span> : null;
  return (
    <div className="stack">
      {head(d.display || "—", c.a)}
      <div>{status}</div>
      {c.st === "computed" && <>
        <div className="formula">{c.e}</div>
        <code className="excel">{c.x}</code>
      </>}
      {c.st === "unverified" && <p className="small">{tr("This figure was typed into the file and does not appear in any tool result or message of the conversation. Check it against the books, or ask the assistant to take it from a query.")}</p>}
      {c.n && c.st === "sourced" && <div className="quote small">{c.n}</div>}
      {d.source && <SourceCard src={d.source} />}
      {d.rows && (
        <div className="stack">
          <h3 style={{ margin: 0 }}>{tr("Underlying rows")}</h3>
          <p className="xs muted">{d.rows.total_rows}{" "}{tr("row")}{d.rows.total_rows === 1 ? "" : "s"}{" "}{tr("in “")}{d.rows.dataset.name}”
            {d.rows.total_rows > d.rows.rows.length ? `, first ${d.rows.rows.length} shown` : ""}
            {d.rows.exact === false ? tr(". The period filter is approximate for weekly groups.") : "."}</p>
          <DataTable columns={d.rows.columns} rows={d.rows.rows} maxHeight={300} />
        </div>
      )}
      {d.rows_error && <div className="notice warn xs">{d.rows_error}</div>}
      {d.findings.length > 0 && <div><h3>{tr("Review")}</h3>
        <ul className="findings">{d.findings.map((x: Finding) => <FindingItem key={x.id} x={x} onDecide={onDecide} editor={editor} />)}</ul></div>}
    </div>
  );
}

function SourceCard({ src }: { src: any }): ReactNode {
  return (
    <div className="source-card">
      <div className="xs muted">{src.id} · {src.system}{src.at ? ` · ${fmtDateTime(src.at)}` : ""}</div>
      <div className="small" style={{ fontWeight: 600 }}>{src.title}</div>
      {src.detail && <div className="xs muted" style={{ marginTop: 4 }}>{src.detail}</div>}
      {src.link && <button className="btn small" style={{ marginTop: 8 }} onClick={() => go(src.link)}><Icon name="link" size={14} />{tr("Open source")}</button>}
    </div>
  );
}

const OP_LABEL: Record<string, string> = { set: N("Set"), append_row: N("Added row"), add_sheet: N("Added sheet"), rename_sheet: N("Renamed sheet"),
  fill: N("Filled"), replace_text: N("Replaced"), set_paragraph: N("Rewrote"), insert_paragraph: N("Added paragraph"),
  set_table_cell: N("Table cell"), add_table_row: N("Added table row") };

/** The user's own Excel or Word file: what it contains, what each version changed and where every new figure came from. */
function UploadView({ id, data, onVersion, reload }: { id: string; data: any; onVersion: (v: number | null) => void; reload: () => void }): ReactNode {
  const { ws } = useApp();
  const { run } = useAction();
  const [confirm, setConfirm] = useState(false);
  const [peek, setPeek] = useState<string | null>(null);
  const model = data.model, file = data.file, fmt = model.format;
  const editor = can(ws, "editor");
  const findings: Finding[] = data.findings || [];
  const o = model.outline || {};
  return (
    <div className="page" style={{ maxWidth: 1100 }}>
      <PageHead title={file.name}
        sub={<><button className="linklike" onClick={() => go("files")}>{tr("Files")}</button>{" / "}
          {fmt === "xlsx" ? tr("Your Excel file") : tr("Your Word file")}{tr(", version")}{" "}{data.version}{data.note ? ` — ${data.note}` : ""}</>}
        actions={<>
          {data.versions.length > 1 && <select className="select" style={{ width: 150 }} aria-label={tr("Version")} value={data.version}
            onChange={(e) => onVersion(Number(e.target.value))}>
            {data.versions.map((v: any) => <option key={v.version} value={v.version}>{tr("Version")}{" "}{v.version}{v.version === 1 ? tr(" (original)") : ""}</option>)}</select>}
          <button className="btn primary" onClick={() => run(() => download(wsPath(ws, `/files/${id}/download?format=${fmt}&version=${data.version}`),
            `${file.name} v${data.version}.${fmt}`))}><Icon name={fmt === "xlsx" ? "data" : "docs"} />{tr("Download")}</button>
          <button className="btn" onClick={async () => { const r: any = await run(() => get(wsPath(ws, `/files/${id}/inspect?version=${data.version}`))); if (r) setPeek(r.text); }}>
            <Icon name="search" />{tr("Show contents")}</button>
          <button className="btn" title={tr("Ask the assistant to fill in or update this file")} onClick={async () => {
            const t: any = await run(() => post(wsPath(ws, "/threads"), {}));
            if (t) go(`chat/${t.id}?file=${id}&name=${encodeURIComponent(file.name)}`);
          }}>
            <Icon name="chat" />{tr("Ask the assistant")}</button>
          {editor && <button className="btn ghost" aria-label={tr("Delete file")} onClick={() => setConfirm(true)}><Icon name="trash" /></button>}
        </>} />
      {model.at_risk?.length > 0 && <div className="notice warn" role="note">{tr("This file has")}{" "}{model.at_risk.join(", ")}{tr(". Editing it here keeps your values, formulas and formatting but drops those parts, so keep the original (version 1) for them.")}</div>}
      <div className="file-layout">
        <div className="stack" style={{ minWidth: 0 }}>
          <section className="panel panel-pad">
            <h2 className="h3">{tr("Contents")}</h2>
            {fmt === "xlsx" ? <ul className="plain small">{(o.sheets || []).map((s: any) => (
              <li key={s.name}><b>{s.name}</b> <span className="muted">{s.range}, {s.rows}{" "}{tr("rows ×")}{" "}{s.columns}{" "}{tr("columns,")}{" "}{s.formulas}{" "}{tr("formulas")}</span></li>))}</ul>
              : <p className="small">{o.paragraphs}{" "}{tr("paragraphs,")}{" "}{o.tables}{" "}{tr("tables")}{o.headings?.length ? <>{tr("; sections:")}{" "}{o.headings.join(", ")}</> : null}
                {o.placeholders?.length ? <><br />{tr("Placeholders to fill:")}{" "}{o.placeholders.map((p: string) => <code key={p} style={{ marginInlineEnd: 6 }}>{p}</code>)}</> : null}</p>}
            {peek && <pre className="peek">{peek}</pre>}
          </section>
          <section className="panel">
            <h2 className="h3 panel-pad" style={{ paddingBottom: 0 }}>{data.version === 1 ? tr("Original, as uploaded") : tr("Changes in version {0}", { 0: data.version })}</h2>
            {(model.edits || []).length === 0 ? <Empty title={tr("No changes yet")}>{tr("Attach this file in a conversation or open it from here and ask, for example, “Fill in October from the P&L” or “Update the fee table and the client name”.")}</Empty> : (
              <div className="table-wrap" style={{ maxHeight: "none" }}><table className="ledger">
                <thead><tr><th>{tr("Where")}</th><th>{tr("Change")}</th><th>{tr("New value")}</th><th>{tr("Source")}</th></tr></thead>
                <tbody>{model.edits.map((e: any, i: number) => (
                  <tr key={i}>
                    <td className="small">{e.target}</td>
                    <td className="small">{tr(OP_LABEL[e.op] || e.op)}</td>
                    <td className={`small ${e.status === "unverified" ? "fig-key unverified" : ""}`}>{e.status === "structure" ? "" : String(e.after ?? "").slice(0, 160)}</td>
                    <td className="xs">{e.status === "sourced" ? (e.source ? <SourceLink e={e} /> : (e.sources || []).map((s: any, k: number) => <div key={k}><SourceLink e={s} /> <span className="muted">{s.figure}</span></div>))
                      : e.status === "formula" ? <span className="fig-key computed">{tr("live formula")}</span>
                        : e.status === "unverified" ? <span className="fig-key unverified">{tr("no source:")}{" "}{(e.unverified || []).join(", ") || tr("check this figure")}</span>
                          : <span className="muted">{e.status === "structure" ? tr("layout") : tr("text")}</span>}</td>
                  </tr>))}</tbody></table></div>)}
          </section>
          <Comments targetType="file" targetId={id} />
        </div>
        <aside className="evidence" aria-label={tr("Checks")}>
          <h2 className="h3">{tr("Checks")}</h2>
          <Verification s={model.summary} />
          {findings.length === 0 ? <p className="small muted">{tr("Nothing to check.")}</p> : <ul className="plain">{findings.map((x) => (
            <FindingItem key={x.id} x={x} editor={editor} onDecide={reload} />))}</ul>}
          <h2 className="h3" style={{ marginTop: 16 }}>{tr("Versions")}</h2>
          <ol className="plain small">{data.versions.map((v: any) => (
            <li key={v.version}><button className="linklike" onClick={() => onVersion(v.version)}>{tr("Version")}{" "}{v.version}</button>
              {" "}<span className="muted">{v.note}, {fmtRelative(v.created_at)}{v.created_by_name ? `, ${v.created_by_name}` : ""}</span></li>))}</ol>
        </aside>
      </div>
      {confirm && <Confirm text={tr("Delete {0} and all its versions?", { 0: file.name })} onClose={() => setConfirm(false)}
        onYes={async () => { await run(() => del(wsPath(ws, `/files/${id}`))); go("files"); }} />}
    </div>
  );
}

function SourceLink({ e }: { e: any }): ReactNode {
  const title = e.line ? `${e.source}: ${e.line}` : e.source;
  return e.link ? <button className="linklike" title={title} onClick={() => go(e.link)}>{e.source}</button> : <span title={title}>{e.source}</span>;
}
