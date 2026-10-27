import { useState, type ReactNode } from "react";
import { del, get, post, put } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtMoney, fmtNumber, fmtRelative } from "../lib/format";
import { Chart, DataTable, toSeries } from "../components/charts";
import { Comments } from "../components/comments";
import { Confirm, Empty, ErrorBox, Field, Icon, Loading, Modal, PageHead, go, useAction, useLoad } from "../components/ui";
import { tr } from "../lib/i18n";

type Widget = { id?: string; title: string; type: string; width: number; query: any; data?: any; error?: string };

export function DashboardsPage({ id }: { id?: string }): ReactNode {
  return id ? <DashboardView id={id} /> : <DashboardList />;
}

function DashboardList(): ReactNode {
  const { ws } = useApp();
  const list = useLoad(() => get<any[]>(wsPath(ws, "/dashboards")), [ws.id]);
  const [creating, setCreating] = useState(false);
  return (
    <div className="page">
      <PageHead title={tr("Dashboards")} sub={tr("Live views over your imported spreadsheets and synced accounting data. Ask the assistant to build one, start from a template, or add widgets yourself.")}
        actions={can(ws, "editor") && <button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" />{tr("New dashboard")}</button>} />
      <ErrorBox error={list.error} />
      {list.loading ? <Loading /> : (list.data || []).length === 0 ? (
        <div className="panel"><Empty title={tr("No dashboards yet")}>{tr("Import an Excel file under Data or connect QuickBooks, Zoho Books or Xero, then create a dashboard from a template.")}</Empty></div>
      ) : (
        <ul className="list panel">
          {(list.data || []).map((d) => (
            <li key={d.id}><a className="item" href={`#/dashboards/${d.id}`}>
              <div className="spread"><strong>{d.name}</strong><span className="small muted">{d.widget_count}{" "}{tr("widgets, updated")}
                {" "}{fmtRelative(d.updated_at)}</span></div>
              {d.description && <div className="small muted">{d.description}</div>}
            </a></li>
          ))}
        </ul>
      )}
      {creating && <NewDashboard onClose={() => setCreating(false)} />}
    </div>
  );
}

function NewDashboard({ onClose }: { onClose: () => void }): ReactNode {
  const { ws } = useApp();
  const datasets = useLoad(() => get<any[]>(wsPath(ws, "/datasets")), [ws.id]);
  const templates = useLoad(() => get<Record<string, { label: string }>>("/api/dashboard-templates"), []);
  const [template, setTemplate] = useState("dataset_overview");
  const [datasetId, setDatasetId] = useState("");
  const [name, setName] = useState("");
  const { busy, run } = useAction();

  async function create() {
    const d = await run(() => template === "blank"
      ? post(wsPath(ws, "/dashboards"), { name: name || "New dashboard", widgets: [] })
      : post(wsPath(ws, "/dashboards/from-template"), { template, dataset_id: datasetId, name: name || undefined }));
    if (d) go(`dashboards/${(d as any).id}`);
  }

  return (
    <Modal title={tr("New dashboard")} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>{tr("Cancel")}</button>
      <button className="btn primary" disabled={busy || (template !== "blank" && !datasetId)} onClick={create}>{tr("Create")}</button></>}>
      <div className="stack">
        <Field label={tr("Start from")}>
          <select className="select" value={template} onChange={(e) => setTemplate(e.target.value)}>
            {Object.entries(templates.data || {}).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            <option value="blank">{tr("Blank dashboard")}</option>
          </select>
        </Field>
        {template !== "blank" && (
          <Field label={tr("Dataset")} hint={tr("P&L, receivables and payables templates need a dataset synced from an accounting connection.")}>
            <select className="select" value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
              <option value="">{tr("Choose…")}</option>
              {(datasets.data || []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
        )}
        <Field label={tr("Name (optional)")}><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

function WidgetBody({ w, currency }: { w: Widget; currency: string }): ReactNode {
  if (w.error) return <div className="notice error small">{w.error}</div>;
  const data = w.data;
  if (!data) return null;
  if (w.type === "kpi") {
    const mi = data.columns.findIndex((c: any) => c.key.startsWith("m"));
    const v = data.rows[0]?.[mi];
    const money = data.columns[mi]?.format === "money";
    return <div className={"big" + (typeof v === "number" && v < 0 ? " neg" : "")}>{money ? fmtMoney(v, currency) : fmtNumber(v)}</div>;
  }
  if (w.type === "table") return <DataTable columns={data.columns} rows={data.rows} currency={currency} maxHeight={320} />;
  const s = toSeries(data.columns, data.rows);
  return <Chart kind={w.type as any} categories={s.categories} series={s.series} format={s.format} currency={currency} title={w.title} />;
}

function DashboardView({ id }: { id: string }): ReactNode {
  const { ws } = useApp();
  const dash = useLoad(() => get<any>(wsPath(ws, `/dashboards/${id}`)), [id, ws.id]);
  const [editing, setEditing] = useState<{ index: number | null; widget: Widget } | null>(null);
  const [confirm, setConfirm] = useState(false);
  const { run } = useAction();
  if (dash.loading && !dash.data) return <div className="page"><Loading /></div>;
  if (dash.error) return <div className="page"><ErrorBox error={dash.error} /></div>;
  const d = dash.data;
  const widgets: Widget[] = d.widgets;

  async function saveWidgets(next: Widget[]) {
    const clean = next.map(({ data: _d, error: _e, ...w }) => w);
    const r = await run(() => put(wsPath(ws, `/dashboards/${id}`), { name: d.name, description: d.description, widgets: clean }), tr("Saved"));
    if (r) dash.reload();
  }

  return (
    <div className="page" style={{ maxWidth: 1400 }}>
      <PageHead title={d.name} sub={d.description || tr("Figures in {0} unless the data says otherwise.", { 0: ws.base_currency })}
        actions={<>
          <button className="btn" onClick={dash.reload}><Icon name="refresh" />{tr("Refresh")}</button>
          {can(ws, "editor") && <button className="btn" onClick={() => setEditing({ index: null, widget: { title: "", type: "bar", width: 1,
            query: { dataset: "", measures: [{ column: "", agg: "sum" }], group_by: [], filters: [], sort: [], limit: 1000 } } })}>
            <Icon name="plus" />{tr("Add widget")}</button>}
          {can(ws, "editor") && <button className="btn danger" onClick={() => setConfirm(true)}><Icon name="trash" /></button>}
        </>} />
      {widgets.length === 0 && <div className="panel"><Empty title={tr("This dashboard is empty")}>{tr("Add a widget, or ask the assistant: “Add a chart of expenses by month to")}{" "}{d.name}”.</Empty></div>}
      <div className="dash-grid">
        {widgets.map((w, i) => (
          <section key={w.id || i} className={`widget ${w.width === 2 ? "w2" : w.width === 3 ? "w3" : ""}`} aria-label={w.title}>
            <div className="spread"><h3>{w.title}</h3>
              {can(ws, "editor") && <span className="row">
                <button className="btn ghost small" aria-label={tr("Edit widget")} onClick={() => setEditing({ index: i, widget: w })}><Icon name="edit" size={15} /></button>
                <button className="btn ghost small" aria-label={tr("Remove widget")} onClick={() => saveWidgets(widgets.filter((_, j) => j !== i))}><Icon name="x" size={15} /></button>
              </span>}</div>
            <WidgetBody w={w} currency={ws.base_currency} />
          </section>
        ))}
      </div>
      <div className="section"><Comments targetType="dashboard" targetId={id} /></div>
      {editing && <WidgetEditor initial={editing.widget} onClose={() => setEditing(null)} onSave={(w) => {
        const next = [...widgets];
        if (editing.index === null) next.push(w); else next[editing.index] = w;
        setEditing(null);
        void saveWidgets(next);
      }} />}
      {confirm && <Confirm text={tr("Delete the dashboard “{0}”? The data stays.", { 0: d.name })} onClose={() => setConfirm(false)}
        onYes={async () => { await run(() => del(wsPath(ws, `/dashboards/${id}`))); go("dashboards"); }} />}
    </div>
  );
}

function WidgetEditor({ initial, onClose, onSave }: { initial: Widget; onClose: () => void; onSave: (w: Widget) => void }): ReactNode {
  const { ws } = useApp();
  const datasets = useLoad(() => get<any[]>(wsPath(ws, "/datasets")), [ws.id]);
  const [w, setW] = useState<Widget>(JSON.parse(JSON.stringify({ ...initial, data: undefined, error: undefined })));
  const ds = (datasets.data || []).find((d) => d.id === w.query.dataset || d.name === w.query.dataset);
  const cols: any[] = ds?.columns || [];
  const q = w.query;
  const m = q.measures[0] || { column: "", agg: "sum" };
  const g0 = q.group_by[0] || { column: "" };
  const g1 = q.group_by[1] || { column: "" };
  const f0 = q.filters[0] || { column: "", op: "eq", value: "" };
  const setQ = (patch: any) => setW({ ...w, query: { ...q, ...patch } });
  const dateCol = (name: string) => cols.find((c) => c.name === name)?.type === "date";

  const groupBy = (a: any, b: any) => [a, b].filter((x) => x.column).map((x) => ({ column: x.column, ...(x.grain ? { grain: x.grain } : {}) }));

  return (
    <Modal wide title={initial.title ? `Edit “${initial.title}”` : tr("New widget")} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>{tr("Cancel")}</button>
      <button className="btn primary" disabled={!w.title || !q.dataset} onClick={() => onSave({
        ...w, query: { ...q, filters: q.filters.filter((f: any) => f.column) } })}>{tr("Save widget")}</button></>}>
      <div className="stack">
        <div className="grid3">
          <Field label={tr("Title")}><input className="input" value={w.title} onChange={(e) => setW({ ...w, title: e.target.value })} /></Field>
          <Field label={tr("Type")}>
            <select className="select" value={w.type} onChange={(e) => setW({ ...w, type: e.target.value })}>
              <option value="kpi">{tr("Single figure")}</option><option value="bar">{tr("Bar chart")}</option>
              <option value="stacked_bar">{tr("Stacked bars")}</option><option value="line">{tr("Line chart")}</option>
              <option value="area">{tr("Area chart")}</option><option value="table">{tr("Table")}</option>
            </select>
          </Field>
          <Field label={tr("Width")}>
            <select className="select" value={w.width} onChange={(e) => setW({ ...w, width: Number(e.target.value) })}>
              <option value={1}>{tr("One third")}</option><option value={2}>{tr("Two thirds")}</option><option value={3}>{tr("Full width")}</option>
            </select>
          </Field>
        </div>
        <Field label={tr("Dataset")}>
          <select className="select" value={ds?.id || ""} onChange={(e) => setQ({ dataset: e.target.value, measures: [{ column: "", agg: "sum" }],
            group_by: [], filters: [] })}>
            <option value="">{tr("Choose…")}</option>
            {(datasets.data || []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </Field>
        {ds && (
          <>
            <div className="grid3">
              <Field label={tr("Measure")}>
                <select className="select" value={m.agg} onChange={(e) => setQ({ measures: [{ ...m, agg: e.target.value,
                  column: e.target.value === "count" ? null : m.column }] })}>
                  <option value="sum">{tr("Sum")}</option><option value="avg">{tr("Average")}</option><option value="min">{tr("Minimum")}</option>
                  <option value="max">{tr("Maximum")}</option><option value="count">{tr("Count of rows")}</option>
                  <option value="count_distinct">{tr("Count distinct")}</option>
                </select>
              </Field>
              <Field label={tr("of column")}>
                <select className="select" value={m.column || ""} disabled={m.agg === "count"}
                  onChange={(e) => setQ({ measures: [{ ...m, column: e.target.value }] })}>
                  <option value="">—</option>
                  {cols.filter((c) => ["count_distinct", "min", "max"].includes(m.agg) || c.type === "number")
                    .map((c) => <option key={c.key}>{c.name}</option>)}
                </select>
              </Field>
              <Field label={tr("Rows to show")}><input className="input" type="number" min={1} max={10000} value={q.limit}
                onChange={(e) => setQ({ limit: Number(e.target.value) || 1000 })} /></Field>
            </div>
            {w.type !== "kpi" && (
              <div className="grid3">
                <Field label={tr("Group by")}>
                  <select className="select" value={g0.column} onChange={(e) => setQ({ group_by: groupBy({ column: e.target.value,
                    grain: dateCol(e.target.value) ? "month" : undefined }, g1) })}>
                    <option value="">—</option>{cols.map((c) => <option key={c.key}>{c.name}</option>)}
                  </select>
                </Field>
                <Field label={tr("Date grouping")}>
                  <select className="select" value={g0.grain || ""} disabled={!dateCol(g0.column)}
                    onChange={(e) => setQ({ group_by: groupBy({ ...g0, grain: e.target.value || undefined }, g1) })}>
                    <option value="">{tr("Exact date")}</option><option value="month">{tr("Month")}</option><option value="quarter">{tr("Quarter")}</option>
                    <option value="year">{tr("Calendar year")}</option><option value="fiscal_year">{tr("Fiscal year")}</option>
                  </select>
                </Field>
                <Field label={tr("Split into series by")} hint={tr("For stacked bars and multi-line charts.")}>
                  <select className="select" value={g1.column} onChange={(e) => setQ({ group_by: groupBy(g0, { column: e.target.value }) })}>
                    <option value="">—</option>{cols.filter((c) => c.type === "text").map((c) => <option key={c.key}>{c.name}</option>)}
                  </select>
                </Field>
              </div>
            )}
            <div className="grid3">
              <Field label={tr("Filter column")}>
                <select className="select" value={f0.column} onChange={(e) => setQ({ filters: e.target.value ? [{ ...f0, column: e.target.value }] : [] })}>
                  <option value="">{tr("No filter")}</option>{cols.map((c) => <option key={c.key}>{c.name}</option>)}
                </select>
              </Field>
              <Field label={tr("Condition")}>
                <select className="select" value={f0.op} disabled={!f0.column} onChange={(e) => setQ({ filters: [{ ...f0, op: e.target.value }] })}>
                  <option value="eq">{tr("equals")}</option><option value="ne">{tr("is not")}</option><option value="contains">{tr("contains")}</option>
                  <option value="gt">{tr("greater than")}</option><option value="lt">{tr("less than")}</option>
                  <option value="last_n_months">{tr("in the last N months")}</option><option value="this_fiscal_year">{tr("in the latest fiscal year")}</option>
                </select>
              </Field>
              <Field label={tr("Value")}><input className="input" value={f0.value ?? ""} disabled={!f0.column || f0.op === "this_fiscal_year"}
                onChange={(e) => setQ({ filters: [{ ...f0, value: ["gt", "lt", "last_n_months"].includes(f0.op) ? Number(e.target.value) : e.target.value }] })} /></Field>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
