import { useEffect, useState, type ReactNode } from "react";
import { del, get, post, put } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtDateTime, fmtRelative } from "../lib/format";
import { Comments } from "../components/comments";
import { Confirm, Empty, ErrorBox, Field, Icon, Loading, PageHead, StatusTag, go, query, useAction, useLoad } from "../components/ui";
import { N, lang, tr } from "../lib/i18n";

type Step = { id: string; name: string; type: string; [k: string]: any };
type Def = { trigger: any; steps: Step[] };

function triggerText(t: any): string {
  if (!t || t.type === "manual") return tr("Run by hand");
  if (t.type === "schedule") return tr("On schedule: {cron}", { cron: t.cron + (t.timezone ? ` (${t.timezone})` : "") });
  if (t.type === "email") return tr("When Gmail receives: {query}", { query: t.query });
  if (t.type === "channel_command") return tr("When someone sends /{word} in Telegram or WhatsApp", { word: t.keyword.replace(/^\//, "") });
  if (t.type === "webhook") return tr("When another system sends an event (webhook)");
  return t.type;
}

export function WorkflowsPage({ id }: { id?: string }): ReactNode {
  return id ? <WorkflowDetail id={id} /> : <WorkflowList />;
}

/** What still has to be filled in (mirrors the engine's check). Drafts can be saved incomplete. */
export function stepProblem(step: any): string | null {
  if (!String(step.name || "").trim()) return tr("Give the step a name.");
  if (step.type === "agent" && !String(step.prompt || "").trim()) return tr("Write what the assistant should do.");
  if (step.type === "tool" && !String(step.tool || "").trim()) return tr("Choose a tool.");
  if (step.type === "approval" && !String(step.message || "").trim()) return tr("Write what the approver is asked to check.");
  if (step.type === "notify" && !String(step.message || "").trim()) return tr("Write the message to send.");
  if (step.type === "condition" && !String(step.left || "").trim()) return tr("Say which value to check.");
  if (step.type === "file" && !String(step.file_id || "").trim()) return tr("Choose the file.");
  if (step.type === "file" && step.action === "edit" && !(step.edits || []).length) return tr("Add the edits to make.");
  return null;
}

function WorkflowList(): ReactNode {
  const { ws } = useApp();
  const list = useLoad(() => get<any[]>(wsPath(ws, "/workflows")), [ws.id]);
  const templates = useLoad(() => get<any[]>(wsPath(ws, `/workflow-templates?lang=${lang()}`)), [ws.id]);
  const { busy, run } = useAction();

  async function create(t: { name: string; description: string; definition: Def }) {
    const wf = await run(() => post(wsPath(ws, "/workflows"), { ...t, enabled: false }));
    if (wf) go(`workflows/${(wf as any).id}`);
  }
  async function fromTemplate(key: string) {
    const wf = await run(() => post(wsPath(ws, "/workflows/from-template"), { key, lang: lang() }), tr("Saved as a draft"));
    if (wf) go(`workflows/${(wf as any).id}`);
  }

  return (
    <div className="page">
      <PageHead title={tr("Workflows")} sub={tr("Repeatable finance routines: a trigger, then steps the assistant and your tools carry out. Steps that send or change something wait for approval.")}
        actions={can(ws, "editor") && <button className="btn primary" onClick={() => create({ name: "New workflow", description: "",
          definition: { trigger: { type: "manual" }, steps: [{ id: "step1", name: "Ask the assistant", type: "agent", prompt: "" }] } })}>
          <Icon name="plus" />{tr("New workflow")}</button>} />
      <ErrorBox error={list.error} />
      {list.loading ? <Loading /> : (list.data || []).length > 0 && (
        <ul className="list panel">
          {(list.data || []).map((w) => (
            <li key={w.id}><a className="item" href={`#/workflows/${w.id}`}>
              <div className="spread"><strong>{w.name}</strong>
                <span className="row">{!w.enabled && <span className="tag">{tr("off")}</span>}{w.last_run && <StatusTag status={w.last_run.status} />}</span></div>
              <div className="small muted">{triggerText(w.trigger)}, {w.steps}{" "}{tr("steps")}
                {w.next_run_at ? tr(", next run {0}", { 0: fmtDateTime(w.next_run_at) }) : ""}</div>
            </a></li>
          ))}
        </ul>
      )}
      {can(ws, "editor") && (
        <div className="section">
          <h2>{tr("Start from a template")}</h2>
          <p className="small muted" style={{ marginTop: -4 }}>{tr("Templates are saved switched off, so you can check the steps first.")}</p>
          <div className="grid3">
            {(templates.data || []).map((t) => (
              <div key={t.key} className="panel panel-pad template-card">
                <span className="xs muted">{t.category} · {triggerText(t.definition.trigger)}</span>
                <h3>{t.name}</h3>
                <p className="small muted" style={{ margin: "6px 0 12px" }}>{t.description}</p>
                <ol className="xs template-steps">{t.definition.steps.map((s: any) => <li key={s.id}>{s.name}</li>)}</ol>
                <button className="btn small" disabled={busy} onClick={() => fromTemplate(t.key)}>{tr("Use template")}</button>
              </div>
            ))}
          </div>
        </div>
      )}
      {!list.loading && (list.data || []).length === 0 && !can(ws, "editor") && <Empty title={tr("No workflows yet")} />}
    </div>
  );
}

function WorkflowDetail({ id }: { id: string }): ReactNode {
  const { ws } = useApp();
  const data = useLoad(() => get<any>(wsPath(ws, `/workflows/${id}`)), [id]);
  const tools = useLoad(() => get<any[]>(wsPath(ws, "/tools")), [ws.id]);
  const skills = useLoad(() => get<any[]>(wsPath(ws, "/skills")), [ws.id]);
  const files = useLoad(() => get<any[]>(wsPath(ws, "/files")), [ws.id]);
  const [def, setDef] = useState<Def | null>(null);
  const [meta, setMeta] = useState({ name: "", description: "", enabled: false });
  const [runId, setRunId] = useState<string | null>(query().get("run"));
  const [confirm, setConfirm] = useState(false);
  const { busy, run } = useAction();
  const editable = can(ws, "editor");

  useEffect(() => {
    if (data.data) {
      setDef(JSON.parse(JSON.stringify(data.data.workflow.definition)));
      setMeta({ name: data.data.workflow.name, description: data.data.workflow.description, enabled: !!data.data.workflow.enabled });
    }
  }, [data.data]);

  if (data.error) return <div className="page"><ErrorBox error={data.error} /></div>;
  if (!def) return <div className="page"><Loading /></div>;

  const setStep = (i: number, s: Step) => setDef({ ...def, steps: def.steps.map((x, j) => (j === i ? s : x)) });
  const move = (i: number, d: number) => {
    const steps = [...def.steps];
    const [s] = steps.splice(i, 1);
    steps.splice(i + d, 0, s);
    setDef({ ...def, steps });
  };
  const addStep = (type: string) => {
    const n = def.steps.length + 1;
    const base: Step = { id: `step${n}${Math.random().toString(36).slice(2, 5)}`.replace(/[^a-z0-9_]/g, ""), name: "", type };
    const extra: Record<string, any> = {
      agent: { name: "Ask the assistant", prompt: "", skills: [] }, tool: { name: "Run a tool", tool: "", arguments: {} },
      approval: { name: "Approval", message: "" }, condition: { name: "Check", left: "", op: "gt", right: "", on_false: "stop" },
      notify: { name: "Notify", channels: ["in_app"], recipients: "me", message: "" },
      file: { name: "Update a file", action: "refresh", file_id: "", edits: [], note: "" },
    };
    setDef({ ...def, steps: [...def.steps, { ...base, ...extra[type] }] });
  };

  async function save() {
    const r = await run(() => put(wsPath(ws, `/workflows/${id}`), { ...meta, definition: def }), tr("Workflow saved"));
    if (r) data.reload();
  }

  async function runNow() {
    const r = await run(() => post<{ run_id: string }>(wsPath(ws, `/workflows/${id}/run`), { input: "" }));
    if (r) { setRunId(r.run_id); data.reload(); }
  }

  const refs = (i: number) => def.steps.slice(0, i).map((s) => `{{steps.${s.id}.output}}`);

  return (
    <div className="page">
      <PageHead title={meta.name || tr("Workflow")} sub={triggerText(def.trigger)} actions={<>
        {editable && <button className="btn" onClick={runNow} disabled={busy}><Icon name="play" />{tr("Run now")}</button>}
        {editable && <button className="btn primary" onClick={save} disabled={busy}>{tr("Save")}</button>}
        {editable && <button className="btn danger" onClick={() => setConfirm(true)}><Icon name="trash" /></button>}
      </>} />
      <div className="grid2" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", alignItems: "start" }}>
        <div className="stack">
          <div className="panel panel-pad stack">
            <div className="grid2">
              <Field label={tr("Name")}><input className="input" value={meta.name} disabled={!editable} onChange={(e) => setMeta({ ...meta, name: e.target.value })} /></Field>
              <label className="check" style={{ alignSelf: "end" }}><input type="checkbox" checked={meta.enabled} disabled={!editable}
                onChange={(e) => setMeta({ ...meta, enabled: e.target.checked })} /><span>{tr("Enabled (schedules and e-mail triggers run only when on)")}</span></label>
            </div>
            <Field label={tr("Description")}><input className="input" value={meta.description} disabled={!editable} onChange={(e) => setMeta({ ...meta, description: e.target.value })} /></Field>
          </div>
          {def.steps.some(stepProblem) && (
            <div className="notice warn small">{tr("This workflow is a draft. You can save it now; fill in the steps marked “To finish” before you switch it on or run it.")}</div>
          )}
          <ol className="steps">
            <li className="step trigger"><span className="n">⏵</span><TriggerEditor value={def.trigger} disabled={!editable}
              onChange={(t) => setDef({ ...def, trigger: t })} workflowId={id} hasWebhook={!!data.data.workflow.webhook}
              saved={data.data.workflow.definition.trigger?.type === "webhook"} onWebhook={data.reload} /></li>
            {def.steps.map((s, i) => (
              <li className="step" key={s.id}><span className="n">{i + 1}</span>
                <StepEditor step={s} disabled={!editable} tools={tools.data || []} skills={skills.data || []} files={files.data || []} refs={refs(i)}
                  onChange={(x) => setStep(i, x)} />
                {stepProblem(s) && <p className="xs" style={{ color: "var(--amber)", margin: "6px 0 0" }} role="note">
                  {tr("To finish:")}{" "}{stepProblem(s)}</p>}
                {editable && <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn ghost small" disabled={i === 0} onClick={() => move(i, -1)}>{tr("Move up")}</button>
                  <button className="btn ghost small" disabled={i === def.steps.length - 1} onClick={() => move(i, 1)}>{tr("Move down")}</button>
                  <button className="btn ghost small" disabled={def.steps.length === 1}
                    onClick={() => setDef({ ...def, steps: def.steps.filter((_, j) => j !== i) })}>{tr("Remove")}</button>
                </div>}
              </li>
            ))}
          </ol>
          {editable && (
            <div className="row wrap"><span className="small muted">{tr("Add a step:")}</span>
              {[["agent", "Assistant"], ["tool", "Tool"], ["file", "File"], ["approval", "Approval"], ["condition", "Condition"], ["notify", "Notify"]].map(([t, l]) => (
                <button key={t} className="btn small" onClick={() => addStep(t)}>{tr(l)}</button>))}
            </div>
          )}
        </div>
        <div className="stack">
          <div className="panel">
            <div className="panel-pad"><h3>{tr("Runs")}</h3></div>
            {data.data.runs.length === 0 ? <p className="panel-pad small muted">{tr("No runs yet.")}</p> : (
              <ul className="list">
                {data.data.runs.map((r: any) => (
                  <li key={r.id}><button className="btn ghost" style={{ width: "100%", justifyContent: "space-between" }}
                    onClick={() => setRunId(r.id)}><span className="small">{fmtRelative(r.started_at)}, {r.trigger?.type}</span>
                    <StatusTag status={r.status} /></button></li>
                ))}
              </ul>
            )}
          </div>
          {runId && <RunView runId={runId} onClose={() => setRunId(null)} />}
        </div>
      </div>
      {confirm && <Confirm text={tr("Delete this workflow and its run history?")} onClose={() => setConfirm(false)}
        onYes={async () => { await run(() => del(wsPath(ws, `/workflows/${id}`))); go("workflows"); }} />}
    </div>
  );
}

function TriggerEditor({ value, onChange, disabled, workflowId, hasWebhook, saved, onWebhook }: { value: any; onChange: (t: any) => void;
  disabled: boolean; workflowId: string; hasWebhook: boolean; saved: boolean; onWebhook: () => void }): ReactNode {
  const t = value || { type: "manual" };
  return (
    <div className="stack">
      <strong>{tr("Trigger")}</strong>
      <div className="grid2">
        <Field label={tr("Starts")}>
          <select className="select" value={t.type} disabled={disabled} onChange={(e) => onChange({ type: e.target.value,
            ...(e.target.value === "schedule" ? { cron: "0 8 * * 1" } : e.target.value === "email" ? { query: "" }
              : e.target.value === "channel_command" ? { keyword: "report" } : {}) })}>
            <option value="manual">{tr("When I run it")}</option><option value="schedule">{tr("On a schedule")}</option>
            <option value="email">{tr("When an e-mail arrives (Gmail)")}</option><option value="channel_command">{tr("On a chat command")}</option>
            <option value="webhook">{tr("From another system (webhook)")}</option>
          </select>
        </Field>
        {t.type === "schedule" && <Field label={tr("Schedule (cron)")} hint={tr("minute hour day month weekday, e.g. 0 8 * * 1 = Mondays 08:00")}>
          <input className="input code-input" value={t.cron} disabled={disabled} onChange={(e) => onChange({ ...t, cron: e.target.value })} /></Field>}
        {t.type === "email" && <Field label={tr("Gmail search")} hint={tr("e.g. from:bank.com has:attachment")}>
          <input className="input" value={t.query} disabled={disabled} onChange={(e) => onChange({ ...t, query: e.target.value })} /></Field>}
        {t.type === "channel_command" && <Field label={tr("Command word")} hint={tr("Members send /word in Telegram or WhatsApp")}>
          <input className="input" value={t.keyword} disabled={disabled} onChange={(e) => onChange({ ...t, keyword: e.target.value })} /></Field>}
      </div>
      {t.type === "schedule" && <SchedulePreview cron={t.cron} timezone={t.timezone} />}
      {t.type === "webhook" && (saved ? <WebhookPanel workflowId={workflowId} has={hasWebhook} disabled={disabled} onChange={onWebhook} />
        : <p className="xs muted">{tr("Save the workflow to get its address and secret.")}</p>)}
    </div>
  );
}

function SchedulePreview({ cron, timezone }: { cron: string; timezone?: string }): ReactNode {
  const { ws } = useApp();
  const [out, setOut] = useState<{ next?: string[]; timezone?: string; error?: string }>({});
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      post<any>(wsPath(ws, "/workflows/schedule-preview"), { cron, timezone: timezone || null })
        .then((r) => live && setOut(r)).catch((e) => live && setOut({ error: e.message }));
    }, 350);
    return () => { live = false; clearTimeout(t); };
  }, [cron, timezone, ws.id]);
  if (out.error) return <p className="xs neg">{out.error}</p>;
  if (!out.next) return null;
  return (
    <div className="xs muted">{tr("Next runs (")}{out.timezone}):{" "}
      {out.next.map((x) => new Date(x).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: out.timezone })).join("; ")}</div>
  );
}

function WebhookPanel({ workflowId, has, disabled, onChange }: { workflowId: string; has: boolean; disabled: boolean; onChange: () => void }): ReactNode {
  const { ws } = useApp();
  const { busy, run } = useAction();
  const [shown, setShown] = useState<{ url: string; secret: string } | null>(null);
  const [off, setOff] = useState(false);
  return (
    <div className="webhook-box stack">
      {shown ? (<>
        <p className="small">{tr("Copy the secret now; it is not shown again. The other system sends a POST with JSON to:")}</p>
        <code className="copyable">{shown.url}</code>
        <code className="copyable">{tr("Authorization: Bearer")}{" "}{shown.secret}</code>
        <p className="xs muted">{tr("Use the event in steps as")}{" "}{tr("{{trigger.data.field}}")}{" "}{tr("or the whole body as")}{" "}{tr("{{trigger.text}}")}{tr(". At most 30 events a minute and 256 KB each; events are refused while the workflow is switched off.")}</p>
      </>) : <p className="small">{has ? tr("This workflow has a webhook secret. Create a new one if it was lost or leaked; the old one stops working.")
        : tr("Create a secret so another system can start this workflow.")}</p>}
      {!disabled && <div className="row">
        <button className="btn small" disabled={busy} onClick={async () => {
          const r: any = await run(() => post(wsPath(ws, `/workflows/${workflowId}/webhook`)));
          if (r) { setShown(r); onChange(); }
        }}>{has ? tr("Create a new secret") : tr("Create secret")}</button>
        {has && <button className="btn small ghost" disabled={busy} onClick={() => setOff(true)}>{tr("Remove")}</button>}
      </div>}
      {off && <Confirm text={tr("Remove the webhook secret? The other system can no longer start this workflow.")} onClose={() => setOff(false)}
        onYes={async () => { await run(() => del(wsPath(ws, `/workflows/${workflowId}/webhook`))); setShown(null); onChange(); }} />}
    </div>
  );
}

function StepEditor({ step, onChange, disabled, tools, skills, files, refs }: { step: Step; onChange: (s: Step) => void; disabled: boolean;
  tools: any[]; skills: any[]; files: any[]; refs: string[] }): ReactNode {
  const set = (patch: Record<string, any>) => onChange({ ...step, ...patch });
  const [argsText, setArgsText] = useState(JSON.stringify(step.arguments || {}, null, 2));
  const [argsError, setArgsError] = useState<string | null>(null);
  const tool = tools.find((t) => t.name === step.tool);
  const label: Record<string, string> = { agent: tr("Assistant"), tool: tr("Tool"), approval: tr("Approval"), condition: tr("Condition"), notify: tr("Notify"), file: tr("File") };
  const [editsText, setEditsText] = useState(JSON.stringify(step.edits || [], null, 2));
  const chosen = files.find((f) => f.id === step.file_id);
  const own = chosen && String(chosen.kind).startsWith("user_");
  return (
    <div className="stack">
      <div className="spread"><strong>{label[step.type]}</strong><span className="xs muted">{tr("id:")}{" "}{step.id}</span></div>
      <Field label={tr("Step name")}><input className="input" value={step.name} disabled={disabled} onChange={(e) => set({ name: e.target.value })} /></Field>
      {step.type === "agent" && (
        <>
          <Field label={tr("Instruction")} hint={refs.length ? tr("You can use {0}, {{trigger.text}}, {{today}}", { 0: refs.join(", ") }) : tr("You can use {{trigger.text}} and {{today}}")}>
            <textarea className="textarea" value={step.prompt} disabled={disabled} onChange={(e) => set({ prompt: e.target.value })}
              placeholder={tr("e.g. List customer invoices more than 30 days overdue and propose the next action for each customer.")} /></Field>
          <Field label={tr("Skills to follow")}>
            <select className="select" multiple size={4} value={step.skills || []} disabled={disabled}
              onChange={(e) => set({ skills: Array.from(e.target.selectedOptions).map((o) => o.value) })}>
              {skills.filter((s) => s.enabled).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
        </>
      )}
      {step.type === "tool" && (
        <>
          <Field label={tr("Tool")} hint={tool ? `${tool.description}${tool.effect === "write" ? " This tool needs approval when the workflow runs." : ""}` : undefined}>
            <select className="select" value={step.tool} disabled={disabled} onChange={(e) => set({ tool: e.target.value })}>
              <option value="">{tr("Choose…")}</option>
              {step.tool && !tool && <option value={step.tool}>{step.tool}{" "}{tr("(available once its connection is set up)")}</option>}
              {tools.map((t) => <option key={t.name} value={t.name}>{t.name}{t.effect === "write" ? tr(" (needs approval)") : ""}</option>)}
            </select>
          </Field>
          <Field label={tr("Arguments (JSON)")} hint={tool ? `Fields: ${Object.keys(tool.parameters?.properties || {}).join(", ") || "none"}` : undefined}>
            <textarea className="textarea code-input" value={argsText} disabled={disabled} onChange={(e) => {
              setArgsText(e.target.value);
              try { set({ arguments: JSON.parse(e.target.value || "{}") }); setArgsError(null); } catch { setArgsError("Not valid JSON yet."); }
            }} />
          </Field>
          {argsError && <span className="small neg">{argsError}</span>}
        </>
      )}
      {step.type === "approval" && (
        <Field label={tr("What the approver sees")}><textarea className="textarea" value={step.message} disabled={disabled}
          onChange={(e) => set({ message: e.target.value })} /></Field>
      )}
      {step.type === "condition" && (
        <div className="grid3">
          <Field label={tr("Value")}><input className="input" value={step.left} disabled={disabled} placeholder={refs[refs.length - 1] || ""}
            onChange={(e) => set({ left: e.target.value })} /></Field>
          <Field label={tr("Is")}>
            <select className="select" value={step.op} disabled={disabled} onChange={(e) => set({ op: e.target.value })}>
              <option value="gt">{tr("greater than")}</option><option value="gte">{tr("at least")}</option><option value="lt">{tr("less than")}</option>
              <option value="lte">{tr("at most")}</option><option value="eq">{tr("equal to")}</option><option value="ne">{tr("not equal to")}</option>
              <option value="contains">{tr("contains")}</option><option value="empty">{tr("empty")}</option><option value="not_empty">{tr("not empty")}</option>
            </select>
          </Field>
          <Field label={tr("Compared with")}><input className="input" value={step.right} disabled={disabled} onChange={(e) => set({ right: e.target.value })} /></Field>
          <Field label={tr("If not")}>
            <select className="select" value={step.on_false} disabled={disabled} onChange={(e) => set({ on_false: e.target.value })}>
              <option value="stop">{tr("Stop the run")}</option><option value="skip_next">{tr("Skip the next step")}</option>
            </select>
          </Field>
        </div>
      )}
      {step.type === "file" && (
        <>
          <div className="grid2">
            <Field label={tr("File")}>
              <select className="select" value={step.file_id} disabled={disabled}
                onChange={(e) => { const f = files.find((x) => x.id === e.target.value);
                  set({ file_id: e.target.value, action: f && String(f.kind).startsWith("user_") ? "edit" : "refresh" }); }}>
                <option value="">{tr("Choose…")}</option>
                {files.map((f) => <option key={f.id} value={f.id}>{f.name}{String(f.kind).startsWith("user_") ? tr(" (your file)") : ""}</option>)}
              </select>
            </Field>
            <Field label={tr("What to do")} hint={step.action === "refresh" ? tr("Runs the file's queries again and saves a new version with the latest data.")
              : tr("Applies the edits below to the latest version of your Excel or Word file and saves a new version.")}>
              <select className="select" value={step.action} disabled={disabled} onChange={(e) => set({ action: e.target.value })}>
                <option value="refresh" disabled={!!own}>{tr("Refresh with the latest data")}</option>
                <option value="edit" disabled={!!chosen && !own}>{tr("Apply edits")}</option>
              </select>
            </Field>
          </div>
          {step.action === "edit" && (<>
            <Field label={tr("Edits (JSON)")} hint={tr("The same edits the assistant makes, e.g. [{\"op\": \"fill\", \"values\": {\"client_name\": \"{{trigger.data.client}}\"}}] or [{\"op\": \"set_cell\", \"sheet\": \"Pack\", \"cell\": \"B4\", \"value\": \"{{steps.step1.data.total}}\"}]")}>
              <textarea className="textarea code-input" value={editsText} disabled={disabled} onChange={(e) => {
                setEditsText(e.target.value);
                try { const v = JSON.parse(e.target.value || "[]"); if (Array.isArray(v)) { set({ edits: v }); setArgsError(null); } else setArgsError("Use a list of edits."); }
                catch { setArgsError("Not valid JSON yet."); }
              }} />
            </Field>
            {argsError && <span className="small neg">{argsError}</span>}
            <Field label={tr("Version note")}><input className="input" value={step.note || ""} disabled={disabled} onChange={(e) => set({ note: e.target.value })}
              placeholder={tr("e.g. October actuals")} /></Field>
          </>)}
          <p className="xs muted">{tr("Later steps can use")}{" "}{`{{steps.${step.id}.output}}`}{" "}{tr("and the link")}{" "}{`{{steps.${step.id}.data.link}}`}.</p>
        </>
      )}
      {["tool", "file", "notify"].includes(step.type) && (
        <details className="retry-box">
          <summary className="small">{tr("If it fails")}{step.retries ? tr(step.retries > 1 ? ": try {0} more times" : ": try {0} more time", { 0: step.retries }) : ""}{step.on_error === "continue" ? tr(", then continue") : ""}</summary>
          <div className="grid3" style={{ marginTop: 8 }}>
            <Field label={tr("Try again")}>
              <select className="select" value={step.retries || 0} disabled={disabled} onChange={(e) => set({ retries: Number(e.target.value) })}>
                {[0, 1, 2, 3, 5].map((n) => <option key={n} value={n}>{n === 0 ? tr("No") : `${n} time${n > 1 ? "s" : ""}`}</option>)}
              </select>
            </Field>
            <Field label={tr("First wait")} hint={tr("Doubles after each try.")}>
              <select className="select" value={step.retry_delay ?? 30} disabled={disabled || !step.retries} onChange={(e) => set({ retry_delay: Number(e.target.value) })}>
                {[[10, "10 seconds"], [30, "30 seconds"], [60, "1 minute"], [300, "5 minutes"], [900, "15 minutes"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
            <Field label={tr("Then")}>
              <select className="select" value={step.on_error || "fail"} disabled={disabled} onChange={(e) => set({ on_error: e.target.value })}>
                <option value="fail">{tr("Stop the run")}</option><option value="continue">{tr("Continue with the next step")}</option>
              </select>
            </Field>
          </div>
        </details>
      )}
      {step.type === "notify" && (
        <>
          <div className="grid2">
            <Field label={tr("Send to")}>
              <select className="select" value={step.recipients} disabled={disabled} onChange={(e) => set({ recipients: e.target.value })}>
                <option value="me">{tr("Me")}</option><option value="admins">{tr("Admins and owners")}</option><option value="editors">{tr("Editors and above")}</option>
                <option value="everyone">{tr("Everyone in the workspace")}</option>
              </select>
            </Field>
            <Field label={tr("Through")} hint={tr("Only workspace members receive notifications; chat apps must be linked by each member.")}>
              <div className="row wrap">{["in_app", "email", "telegram", "whatsapp"].map((c) => (
                <label key={c} className="check small"><input type="checkbox" checked={(step.channels || []).includes(c)} disabled={disabled}
                  onChange={(e) => set({ channels: e.target.checked ? [...(step.channels || []), c] : (step.channels || []).filter((x: string) => x !== c) })} />
                  {c === "in_app" ? tr("App") : c[0].toUpperCase() + c.slice(1)}</label>))}</div>
            </Field>
          </div>
          <Field label={tr("Message")}><textarea className="textarea" value={step.message} disabled={disabled} onChange={(e) => set({ message: e.target.value })} /></Field>
        </>
      )}
    </div>
  );
}

function RunView({ runId, onClose }: { runId: string; onClose: () => void }): ReactNode {
  const { ws } = useApp();
  const r = useLoad(() => get<any>(wsPath(ws, `/workflow-runs/${runId}`)), [runId]);
  useEffect(() => {
    if (r.data && ["running", "waiting"].includes(r.data.status)) {
      const t = setTimeout(r.reload, 1500);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [r.data]);
  if (!r.data) return <div className="panel panel-pad"><Loading /></div>;
  return (
    <div className="panel panel-pad stack">
      <div className="spread"><h3>{tr("Run")}{" "}{fmtDateTime(r.data.started_at)}</h3><span className="row"><StatusTag status={r.data.status} />
        <button className="btn ghost small" onClick={onClose} aria-label={tr("Close run")}><Icon name="x" /></button></span></div>
      {r.data.error && <div className="notice error small">{r.data.error}</div>}
      {r.data.status === "waiting_approval" && <div className="notice warn small">{tr("Waiting for approval in the")}{" "}<a href="#/inbox">{tr("Inbox")}</a>.</div>}
      <ol className="timeline">
        {(r.data.timeline || []).map((s: any) => (
          <li key={s.id} className={`tl tl-${s.status.replace(" ", "-")}`}>
            <div className="spread">
              <strong className="small">{s.name}</strong>
              <span className="xs muted">{tr(STATUS_WORD[s.status] || s.status)}{s.ms != null ? ` · ${fmtMs(s.ms)}` : ""}
                {s.attempts > 1 ? ` · ${s.attempts} tries` : ""}</span>
            </div>
            {s.error && <div className="xs neg">{s.error}</div>}
            {s.output && <details className="tl-out"><summary className="xs">{tr("Output")}</summary><div className="small" style={{ whiteSpace: "pre-wrap" }}>{s.output}</div></details>}
            <div className="row" style={{ gap: 10 }}>
              {s.thread_id && <a href={`#/chat/${s.thread_id}`} className="xs">{tr("Open the conversation")}</a>}
              {s.data?.file_id && <a href={`#/files/${s.data.file_id}`} className="xs">{tr("Open")}{" "}{s.data.name || tr("the file")}{tr(", version")}{" "}{s.data.version}</a>}
            </div>
          </li>
        ))}
      </ol>
      <details><summary className="xs muted">{tr("Full log")}</summary>
        <ol className="xs" style={{ paddingInlineStart: 18, margin: "6px 0 0" }}>
          {r.data.log.map((l: any, i: number) => (
            <li key={i} style={{ marginBottom: 4 }}><strong>{l.step}</strong> <span className={l.status === "error" ? "neg" : "muted"}>{l.status}</span>{" "}
              <span className="muted">{fmtDateTime(l.at)}</span><div style={{ whiteSpace: "pre-wrap" }}>{l.message}</div></li>
          ))}
        </ol>
      </details>
      <Comments targetType="workflow_run" targetId={runId} />
    </div>
  );
}


const STATUS_WORD: Record<string, string> = { done: N("done"), failed: N("failed"), running: N("running"), waiting: N("waiting"),
  pending: N("not started"), skipped: N("skipped"), "not run": N("not run") };

function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60000)} min ${Math.round((ms % 60000) / 1000)} s`;
}
