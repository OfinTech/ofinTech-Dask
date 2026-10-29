import { useEffect, useRef, useState, type ReactNode } from "react";
import { del, get, patch, post } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtRelative } from "../lib/format";
import { Confirm, Empty, ErrorBox, Field, Icon, Loading, PageHead, query, useAction, useLoad } from "../components/ui";
import { N, tr } from "../lib/i18n";

const CATEGORIES = ["fact", "preference", "policy", "mapping", "contact", "deadline"];
/** Category names (listed so they are translated). */
export const CATEGORY_WORDS = [N("fact"), N("preference"), N("policy"), N("mapping"), N("contact"), N("deadline")];
const TABS: [string, string][] = [["active", N("Saved")], ["proposed", N("Waiting for approval")], ["archived", N("Archived")]];

export function MemoryPage(): ReactNode {
  const { ws } = useApp();
  const [status, setStatus] = useState(() => (query().get("status") || "active"));
  const [typed, setTyped] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => { const t = setTimeout(() => setQ(typed.trim()), 300); return () => clearTimeout(t); }, [typed]);
  const data = useLoad(() => get<any>(wsPath(ws, `/memories?status=${status}${q ? `&q=${encodeURIComponent(q)}` : ""}`)),
    [ws.id, status, q]);
  const focus = query().get("id");
  const [adding, setAdding] = useState(false);
  const counts = data.data?.counts || {};
  return (
    <div className="page" style={{ maxWidth: 980 }}>
      <PageHead title={tr("Memory")} sub={tr("What the assistant may reuse across conversations. Nothing is saved without a person's approval; every entry shows where it came from, who saved it and how often it was used.")}
        actions={<button className="btn primary" onClick={() => setAdding(true)}><Icon name="plus" />{tr("Add memory")}</button>} />
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <div className="seg" role="tablist">
          {TABS.map(([key, label]) => (
            <button key={key} role="tab" aria-pressed={status === key} onClick={() => setStatus(key)}>
              {tr(label)}{counts[key] ? ` (${counts[key]})` : ""}</button>))}
        </div>
        <input className="input" style={{ maxWidth: 320 }} placeholder={tr("Search memory")} value={typed} aria-label={tr("Search memory")}
          onChange={(e) => setTyped(e.target.value)} />
      </div>
      {adding && <AddMemory onDone={() => { setAdding(false); data.reload(); }} />}
      <ErrorBox error={data.error} />
      {data.loading && !data.data ? <Loading /> : (data.data?.memories || []).length === 0 ? (
        q ? <div className="panel"><Empty title={tr("Nothing matches “{0}”", { 0: q })}>{tr("Try another word, or look under the other tabs.")}</Empty></div> : <div className="panel"><Empty title={status === "proposed" ? tr("Nothing waiting for approval") : tr("No memory yet")}>
          {tr("When you tell the assistant something lasting, such as a materiality threshold, an account mapping or a close deadline, it suggests saving it. You decide.")}</Empty></div>
      ) : (
        <ul className="memory-list">
          {data.data.memories.map((m: any) => <MemoryItem key={m.id} m={m} focus={focus === m.id} onChange={data.reload} />)}
        </ul>
      )}
    </div>
  );
}

function AddMemory({ onDone }: { onDone: () => void }): ReactNode {
  const { ws } = useApp();
  const [f, setF] = useState({ content: "", scope: can(ws, "editor") ? "workspace" : "personal", category: "fact", pinned: false });
  const { busy, run } = useAction();
  return (
    <div className="panel panel-pad stack" style={{ marginBottom: 16 }}>
      <Field label={tr("What should the assistant remember?")} hint={tr("One short, self-contained sentence. Secrets are refused.")}>
        <textarea className="textarea" rows={2} value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })}
          placeholder={tr("e.g. Accruals are posted by working day 3; the controller approves journals above 25,000.")} /></Field>
      <div className="row wrap">
        <select className="select" style={{ width: 200 }} value={f.scope} aria-label={tr("Who it is for")} onChange={(e) => setF({ ...f, scope: e.target.value })}>
          {can(ws, "editor") && <option value="workspace">{tr("Everyone in the workspace")}</option>}
          <option value="personal">{tr("Only me")}</option></select>
        <select className="select" style={{ width: 160 }} value={f.category} aria-label={tr("Category")} onChange={(e) => setF({ ...f, category: e.target.value })}>
          {CATEGORIES.map((c) => <option key={c} value={c}>{tr(c)}</option>)}</select>
        <label className="check small"><input type="checkbox" checked={f.pinned} onChange={(e) => setF({ ...f, pinned: e.target.checked })} />
          <span>{tr("Always include")}</span></label>
        <span className="grow" />
        <button className="btn" onClick={onDone}>{tr("Cancel")}</button>
        <button className="btn primary" disabled={busy || !f.content.trim()} onClick={async () => {
          if (await run(() => post(wsPath(ws, "/memories"), f), tr("Saved to memory"))) onDone();
        }}>{tr("Save")}</button>
      </div>
    </div>
  );
}

function MemoryItem({ m, focus, onChange }: { m: any; focus: boolean; onChange: () => void }): ReactNode {
  const { ws } = useApp();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(m.content);
  const [confirm, setConfirm] = useState(false);
  const { busy, run } = useAction();
  const mayEdit = m.scope === "personal" || can(ws, "editor");
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => { if (focus) ref.current?.scrollIntoView({ block: "center" }); }, [focus]);
  const save = (body: any, ok?: string) => run(() => patch(wsPath(ws, `/memories/${m.id}`), body), ok).then(onChange);
  return (
    <li ref={ref} className={`memory-item ${focus ? "focus" : ""}`}>
      <div className="spread">
        <span className="row" style={{ gap: 6 }}>
          <span className={`tag ${m.scope === "workspace" ? "blue" : ""}`}>{m.scope === "workspace" ? tr("workspace") : tr("only me")}</span>
          <span className="tag">{tr(m.category)}</span>
          {m.pinned ? <span className="tag green">{tr("always included")}</span> : null}
        </span>
        <span className="xs muted">{m.use_count === 1 ? tr("used once") : tr("used {0} times", { 0: m.use_count })}{m.last_used_at ? `, ${tr("last {0}", { 0: fmtRelative(m.last_used_at) })}` : ""}</span>
      </div>
      {editing ? (
        <div className="stack" style={{ marginTop: 8 }}>
          <textarea className="textarea" rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label={tr("Memory text")} />
          <div className="row"><button className="btn small primary" disabled={busy} onClick={async () => {
            await (m.status === "proposed" ? run(() => post(wsPath(ws, `/memories/${m.id}/decide`), { approve: true, content: text }), tr("Saved"))
              : save({ content: text }, tr("Updated")));
            setEditing(false);
            onChange();
          }}>{tr("Save")}</button><button className="btn small" onClick={() => { setEditing(false); setText(m.content); }}>{tr("Cancel")}</button></div>
        </div>
      ) : <p className="memory-text">{m.content}</p>}
      <div className="xs muted">
        {m.source_type === "chat" ? <>{tr("Suggested by the assistant")}{m.thread_id ? <>{" "}{tr("in")}{" "}<a href={`#/chat/${m.thread_id}`}>{m.thread_title || tr("a conversation")}</a></> : null}
          {m.reason ? ` (${m.reason})` : ""}, </> : tr("Added by hand, ")}
        {m.created_by_name ? `${tr("by {0}", { 0: m.created_by_name })} ` : ""}{fmtRelative(m.created_at)}
        {m.approved_by_name ? tr("; saved by {0}", { 0: m.approved_by_name }) : ""}
        {m.updated_at > m.created_at + 1 ? `; ${tr("changed {0}", { 0: fmtRelative(m.updated_at) })}` : ""}
        {m.replaces_id ? `; ${tr("replaces an older memory, which is archived when this one is saved")}` : ""}
      </div>
      {mayEdit && !editing && (
        <div className="row wrap" style={{ marginTop: 8 }}>
          {m.status === "proposed" ? <>
            <button className="btn small primary" disabled={busy} onClick={() => run(() => post(wsPath(ws, `/memories/${m.id}/decide`), { approve: true }), tr("Saved to memory")).then(onChange)}>{tr("Save")}</button>
            <button className="btn small" onClick={() => setEditing(true)}>{tr("Edit and save")}</button>
            <button className="btn small ghost" disabled={busy} onClick={() => run(() => post(wsPath(ws, `/memories/${m.id}/decide`), { approve: false })).then(onChange)}>{tr("Discard")}</button>
          </> : <>
            <button className="btn small" onClick={() => setEditing(true)}><Icon name="edit" size={14} />{tr("Edit")}</button>
            {m.status === "active" && <button className="btn small" onClick={() => save({ pinned: !m.pinned })}>{m.pinned ? tr("Unpin") : tr("Always include")}</button>}
            <button className="btn small" onClick={() => save({ status: m.status === "archived" ? "active" : "archived" })}>
              {m.status === "archived" ? tr("Restore") : tr("Archive")}</button>
            <button className="btn small ghost" aria-label={tr("Delete")} onClick={() => setConfirm(true)}><Icon name="trash" size={14} /></button>
          </>}
        </div>
      )}
      {confirm && <Confirm text={tr("Delete this memory? The assistant will no longer know it.")} onClose={() => setConfirm(false)}
        onYes={() => run(() => del(wsPath(ws, `/memories/${m.id}`))).then(onChange)} />}
    </li>
  );
}

/** The card shown in the conversation when the assistant proposes a memory. */
export function MemoryProposal({ memory }: { memory: any }): ReactNode {
  const { ws } = useApp();
  const [m, setM] = useState(memory);
  const [text, setText] = useState(memory.content);
  const [editing, setEditing] = useState(false);
  const { busy, run } = useAction();
  const mayDecide = m.scope === "personal" || can(ws, "editor");
  async function decide(approve: boolean, content?: string) {
    const r: any = await run(() => post(wsPath(ws, `/memories/${m.id}/decide`), { approve, content }), approve ? tr("Saved to memory") : undefined);
    if (r) { setM(r); setEditing(false); }
  }
  return (
    <div className="memory-card">
      <div className="xs muted"><Icon name="memory" size={14} />{" "}{m.scope === "workspace" ? tr("Save to workspace memory?") : tr("Save to your personal memory?")}</div>
      {editing ? <textarea className="textarea" rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label={tr("Memory text")} />
        : <p className="memory-text">{m.content}</p>}
      {memory.replaces && <p className="xs muted" style={{ margin: 0 }}>{tr("Replaces: “{0}”", { 0: memory.replaces })}</p>}
      {m.status === "proposed" ? (mayDecide ? (
        <div className="row">
          <button className="btn small primary" disabled={busy} onClick={() => decide(true, editing ? text : undefined)}>{tr("Save")}</button>
          {!editing && <button className="btn small" onClick={() => setEditing(true)}>{tr("Edit")}</button>}
          <button className="btn small ghost" disabled={busy} onClick={() => decide(false)}>{tr("Discard")}</button>
        </div>
      ) : <p className="xs muted">{tr("An editor can save this under Memory.")}</p>)
        : <p className="xs">{m.status === "active" ? <>{tr("Saved.")}{" "}<a href={`#/memory?id=${m.id}`}>{tr("View in Memory")}</a></> : tr("Discarded.")}</p>}
    </div>
  );
}
