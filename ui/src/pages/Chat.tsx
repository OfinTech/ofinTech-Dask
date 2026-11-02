import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { del, get, patch, post, subscribe, uploadMany } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtRelative } from "../lib/format";
import { Markdown } from "../lib/markdown";
import { Blocks } from "../components/blocks";
import { Approval } from "../components/approval";
import { Comments } from "../components/comments";
import { Confirm, Field, Icon, Modal, go, query, useAction, useLoad, useToast } from "../components/ui";
import { tr } from "../lib/i18n";

type Message = { id: string; role: string; content: string; payload: any; author_name?: string; created_at: number };
type Thread = { id: string; title: string; provider_id: string | null; model: string | null; skills: string[];
  visibility: string; created_by: string; created_by_name?: string; updated_at: number; channel: any };

const STARTERS = [
  "What were revenue, expenses and net profit for the last 3 months?",
  "Which customer invoices are more than 30 days overdue?",
  "Build a dashboard from my profit and loss data.",
  "Walk me through this month's close checklist.",
];

const TOOL_LABELS: Record<string, string> = {
  calc_npv_irr: "NPV / IRR", calc_xnpv_xirr: "XNPV / XIRR", calc_loan_schedule: "Loan schedule",
  calc_depreciation: "Depreciation", calc_financial_ratios: "Ratios", calc_variance: "Variance",
  calc_sales_tax: "Sales tax", calc_breakeven: "Break-even", calc_growth: "Growth", calc_currency_convert: "Currency",
  list_datasets: "Datasets", describe_dataset: "Dataset profile", query_dataset: "Query", create_dashboard: "Dashboard",
  accounting_report: "Accounting report", list_invoices: "Invoices", sync_accounting: "Sync", gmail_search: "Gmail search",
  gmail_read: "Read e-mail", search_documents: "Document search", read_document: "Read document",
  list_documents: "Documents", gmail_save_to_documents: "Save to documents", drive_add_document: "Add from Drive", gmail_create_draft: "Gmail draft", gmail_send: "Send e-mail", load_skill: "Skill",
  create_workbook: "Workbook", create_report: "Report", get_file_review: "File review",
  propose_memory: "Memory", search_memory: "Memory search",
};

function toolLabel(name: string): string {
  if (name.startsWith("mcp_")) return "MCP · " + name.split("__").slice(1).join(" ");
  return TOOL_LABELS[name] || name.replace(/_/g, " ");
}

export function ChatPage({ threadId }: { threadId?: string }): ReactNode {
  const { ws } = useApp();
  const [q, setQ] = useState("");
  const threads = useLoad(() => get<Thread[]>(wsPath(ws, `/threads${q ? `?q=${encodeURIComponent(q)}` : ""}`)), [ws.id, q]);
  const { run } = useAction();
  const [deleting, setDeleting] = useState<Thread | null>(null);

  async function remove(t: Thread) {
    await run(() => del(wsPath(ws, `/threads/${t.id}`)), tr("Conversation deleted"));
    const rest = (threads.data || []).filter((x) => x.id !== t.id);
    threads.reload();
    if (t.id === threadId) go(rest.length ? `chat/${rest[0].id}` : "chat");
  }

  async function newThread() {
    const t = await run(() => post<Thread>(wsPath(ws, "/threads"), {}));
    if (t) { threads.reload(); go(`chat/${t.id}`); }
  }

  useEffect(() => {
    if (!threadId && threads.data && threads.data.length) go(`chat/${threads.data[0].id}`);
  }, [threadId, threads.data]);

  return (
    <div className="chat">
      <aside className="threads" aria-label={tr("Conversations")}>
        <div className="threads-head">
          <button className="btn primary grow" onClick={newThread}><Icon name="plus" />{tr("New conversation")}</button>
        </div>
        <div style={{ padding: "0 14px 8px" }}>
          <input className="input" placeholder={tr("Search conversations")} value={q} onChange={(e) => setQ(e.target.value)}
            aria-label={tr("Search conversations")} />
        </div>
        <ul>
          {(threads.data || []).map((t) => (
            <li key={t.id} className="thread-item">
              <a href={`#/chat/${t.id}`} aria-current={t.id === threadId ? "page" : undefined}>
                <div className="t">{t.title}</div>
                <div className="xs muted">{t.channel?.type ? `${t.channel.type} · ` : ""}{fmtRelative(t.updated_at)}
                  {t.visibility === "private" ? tr(", private") : ""}</div>
              </a>
              <button className="btn ghost small thread-del" aria-label={`Delete ${t.title}`} title={tr("Delete conversation")}
                onClick={() => setDeleting(t)}><Icon name="trash" size={14} /></button>
            </li>
          ))}
        </ul>
      </aside>
      {deleting && <Confirm text={tr("Delete “{0}”? Its messages are removed for everyone in the workspace. Files and documents it created stay.", { 0: deleting.title })} onClose={() => setDeleting(null)} onYes={() => void remove(deleting)} />}
      {threadId ? <Conversation key={threadId} threadId={threadId} onChanged={threads.reload}
        onDelete={() => { const t = (threads.data || []).find((x) => x.id === threadId); if (t) setDeleting(t); }} />
        : <div className="convo"><div className="messages"><div className="msg">
          <h1 style={{ marginBottom: 8 }}>{tr("Ask about your numbers")}</h1>
          <p className="muted">{tr("Start a conversation. The assistant answers from your connected systems and imported data, and asks for approval before it sends or changes anything.")}</p>
          <button className="btn primary" onClick={newThread}>{tr("New conversation")}</button>
        </div></div></div>}
    </div>
  );
}

function Conversation({ threadId, onChanged, onDelete }: { threadId: string; onChanged: () => void;
  onDelete: () => void }): ReactNode {
  const { ws, refreshCounts } = useApp();
  const toast = useToast();
  const [thread, setThread] = useState<Thread | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [approvals, setApprovals] = useState<any[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ id: string; text: string } | null>(null);
  const [text, setText] = useState(() => {  // opened from a file: start the request for the user to finish
    const fid = query().get("file");
    return fid ? `In my file “${query().get("name") || "file"}” (file ${fid}), ` : "";
  });
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const addFiles = (list: FileList | File[] | null) => {
    const picked = Array.from(list || []).filter((f) => f.size > 0);
    if (picked.length) setFiles((fs) => [...fs, ...picked].slice(0, 10));
  };
  const [showSettings, setShowSettings] = useState(false);
  const [showComments, setShowComments] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const providers = useLoad(() => get(wsPath(ws, "/providers")), [ws.id]);

  async function load() {
    const d = await get(wsPath(ws, `/threads/${threadId}`));
    setThread(d.thread);
    setMessages(d.messages);
    setApprovals(d.approvals);
    setStatus(d.run?.status ?? null);
  }

  useEffect(() => {
    load().catch((e) => toast(String(e.message || e), "error"));
    return subscribe(wsPath(ws, `/threads/${threadId}/events`), (ev) => {
      if (ev.type === "assistant_start") { setDraft({ id: ev.message_id, text: "" }); setStatus("running"); }
      else if (ev.type === "delta") setDraft((d) => (d && d.id === ev.message_id ? { ...d, text: d.text + ev.text } : { id: ev.message_id, text: ev.text }));
      else if (ev.type === "message") {
        setMessages((ms) => (ms.some((m) => m.id === ev.message.id) ? ms : [...ms, ev.message]));
        setDraft((d) => (d && d.id === ev.message.id ? null : d));
      } else if (ev.type === "tool_start") setStatus("running");
      else if (ev.type === "approval") {
        setApprovals((as) => [...as.filter((a) => a.id !== ev.approval.id), ev.approval]);
        refreshCounts();
      } else if (ev.type === "run") { setStatus(ev.run.status); setDraft(null); refreshCounts(); }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, ws.id]);

  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [messages.length, draft?.text, approvals.length]);

  const busy = status === "running" || status === "waiting_approval";

  async function send(value?: string) {
    const body = (value ?? text).trim();
    const attaching = value === undefined ? files : [];
    if ((!body && attaching.length === 0) || busy) return;
    setText("");
    setFiles([]);
    setStatus("running");
    try {
      let attachments: string[] = [];
      if (attaching.length) {
        const r: any = await uploadMany(wsPath(ws, "/documents"), attaching, { purpose: "chat" });
        for (const f of r.failed || []) toast(`${f.name}: ${f.error}`, "error");
        attachments = (r.added || []).map((d: any) => d.id);
        if (!attachments.length && !body) { setStatus(null); return; }
      }
      await post(wsPath(ws, `/threads/${threadId}/messages`), { text: body, attachments });
      onChanged();
      if (thread && thread.title === "New conversation") {
        get(wsPath(ws, `/threads/${threadId}`)).then((d) => setThread(d.thread)).catch(() => undefined);
      }
    } catch (e: any) {
      setStatus(null);
      setText(body);
      setFiles(attaching);
      toast(e.message || String(e), "error");
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
  }

  const byRun = useMemo(() => {
    const m: Record<string, any[]> = {};
    for (const a of approvals.filter((x) => x.status === "pending")) {
      const key = String(a.source_id);
      (m[key] = m[key] || []).push(a);
    }
    return m;
  }, [approvals]);

  const noModel = providers.data && providers.data.providers.length === 0;
  const modelLabel = thread?.model || providers.data?.default_model || "default model";

  return (
    <section className="convo" aria-label={tr("Conversation")}>
      <header className="convo-head">
        <div className="grow">
          <div className="title">{thread?.title || "…"}</div>
          <div className="xs muted">{modelLabel}{thread && thread.skills.length ? `, skills: ${thread.skills.join(", ")}` : ""}</div>
        </div>
        <button className="btn small" onClick={() => setShowComments(!showComments)} aria-pressed={showComments}>{tr("Comments")}</button>
        <button className="btn small" onClick={() => setShowSettings(true)}>{tr("Model and skills")}</button>
        <button className="btn small ghost" onClick={onDelete} aria-label={tr("Delete conversation")} title={tr("Delete conversation")}>
          <Icon name="trash" size={14} /></button>
      </header>
      <div className="messages">
        {noModel && (
          <div className="msg"><div className="notice warn">{tr("No language model is set up for this workspace yet.")}{" "}
            {can(ws, "admin") ? <a href="#/settings/models">{tr("Add one under Settings → Models")}</a> : tr("Ask an administrator to add one.")}</div></div>
        )}
        {messages.length === 0 && !draft && (
          <div className="msg">
            <h2 style={{ marginBottom: 12 }}>{tr("What do you need to know?")}</h2>
            <div className="stack">
              {STARTERS.map((s) => <button key={s} className="btn" style={{ justifyContent: "flex-start" }} onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {messages.map((m) => <MessageView key={m.id} m={m} />)}
        {Object.entries(byRun).map(([runId, items]) => (
          <div className="msg" key={runId}>{items.map((a) => <Approval key={a.id} approval={a} onDecided={(x) => {
            setApprovals((as) => as.map((y) => (y.id === x.id ? x : y)));
            refreshCounts();
          }} />)}</div>
        ))}
        {draft && (
          <div className="msg"><div className="who">{tr("Assistant")}</div>
            {draft.text ? <div className="caret"><Markdown text={draft.text} /></div> : <div className="typing caret">{tr("Working")}</div>}</div>
        )}
        {status === "running" && !draft && <div className="msg typing caret">{tr("Working")}</div>}
        {showComments && thread && <div className="msg"><Comments targetType="thread" targetId={thread.id} /></div>}
        <div ref={bottom} />
      </div>
      <div className={`composer ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging(true); } }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}>
        {files.length > 0 && (
          <ul className="attach-list" aria-label={tr("Files to send")}>
            {files.map((f, i) => (
              <li key={i} className="attach-chip"><Icon name="clip" size={14} /><span>{f.name}</span>
                <span className="xs muted">{f.size > 1048576 ? `${(f.size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(f.size / 1024))} KB`}</span>
                <button type="button" className="btn ghost small" aria-label={`Remove ${f.name}`}
                  onClick={() => setFiles((fs) => fs.filter((_, j) => j !== i))}><Icon name="x" size={12} /></button></li>
            ))}
          </ul>
        )}
        <form onSubmit={(e) => { e.preventDefault(); void send(); }}>
          <input ref={fileInput} type="file" multiple className="hidden" aria-label={tr("Attach files")}
            accept=".pdf,.docx,.xlsx,.xlsm,.csv,.txt,.md,.html,.htm,.eml,.json,.png,.jpg,.jpeg,.tif,.tiff"
            onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
          <button type="button" className="btn ghost attach-btn" aria-label={tr("Attach files")} title={tr("Attach files (PDF, Word, Excel, CSV, text, e-mail, scans and images)")} disabled={status === "waiting_approval" || files.length >= 10}
            onClick={() => fileInput.current?.click()}><Icon name="clip" /></button>
          <label className="sr" htmlFor="composer">{tr("Message")}</label>
          <textarea id="composer" className="textarea" rows={2} value={text} placeholder={status === "waiting_approval"
            ? tr("Waiting for an approval above…") : files.length ? tr("Ask about the attached files…")
              : tr("Ask about cash, receivables, a report, a variance… or attach a file")}
            onChange={(e) => setText(e.target.value)} onKeyDown={onKey} disabled={status === "waiting_approval"}
            onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); } }} />
          {status === "running" ? (
            <button type="button" className="btn" onClick={() => post(wsPath(ws, `/threads/${threadId}/stop`)).then(load)}>
              <Icon name="stop" />{tr("Stop")}</button>
          ) : (
            <button className="btn primary" disabled={(!text.trim() && files.length === 0) || busy}><Icon name="send" />{tr("Send")}</button>
          )}
        </form>
      </div>
      {showSettings && thread && (
        <ThreadSettings thread={thread} providers={providers.data} onClose={() => setShowSettings(false)}
          onSaved={(t) => { setThread(t); onChanged(); }} />
      )}
    </section>
  );
}

function MessageView({ m }: { m: Message }): ReactNode {
  const { ws } = useApp();
  if (m.role === "user") {
    const attached: { id: string; name: string; status: string }[] = m.payload?.attachments || [];
    const shown = attached.length ? m.content.split("\n\n[Attached files]\n")[0] : m.content;
    return (
      <div className="msg user"><div className="who">{m.author_name || tr("You")}</div>
        {attached.length > 0 && (
          <ul className="attach-list sent">{attached.map((a) => (
            <li key={a.id}><a className="attach-chip" href={`#/documents/${a.id}`}><Icon name="clip" size={14} /><span>{a.name}</span>
              {a.status === "failed" && <span className="xs neg">{tr("not readable")}</span>}</a></li>))}</ul>
        )}
        {shown && <div className="bubble">{shown}</div>}
      </div>
    );
  }
  if (m.role === "event") {
    return <div className="msg"><div className="notice error">{m.content}</div></div>;
  }
  if (m.role === "tool") {
    const p = m.payload || {};
    const blocks = p.blocks || [];
    return (
      <div className="msg">
        <div className={`tool ${p.status === "error" || p.status === "rejected" ? "error" : ""}`}>
          <details open={blocks.length === 0 ? false : undefined}>
            <summary>{toolLabel(p.name || "tool")}{p.approval_id ? (p.status === "rejected" ? tr(", rejected by an approver") : tr(", approved")) : ""}{p.status === "error" ? tr(", failed") : ""}</summary>
            <pre className="xs" style={{ whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto" }}>{m.content}</pre>
          </details>
          {blocks.length > 0 && <div className="blocks"><Blocks blocks={blocks} currency={ws.base_currency} /></div>}
        </div>
      </div>
    );
  }
  if (!m.content) return null;
  const flagged: string[] = m.payload?.unverified_figures || [];
  return (
    <div className="msg">
      <div className="who">{tr("Assistant")}{m.payload?.model ? `, ${m.payload.model}` : ""}</div>
      <Markdown text={m.content} />
      {(m.payload?.memories || []).length > 0 && (
        <details className="memory-used"><summary className="xs muted"><Icon name="memory" size={13} />{" "}{tr("Memory consulted (")}{m.payload.memories.length})</summary>
          <ul>{m.payload.memories.map((x: any) => <li key={x.id} className="xs"><a href={`#/memory?id=${x.id}`}>{x.content}</a>
            {x.scope === "personal" ? <span className="muted">{" "}{tr("(only you)")}</span> : null}</li>)}</ul></details>
      )}
      {flagged.length > 0 && (
        <div className="figcheck">{tr("Check before relying on:")}{" "}{flagged.join(", ")}{tr(". These figures do not appear in any tool result or message in this conversation.")}</div>
      )}
    </div>
  );
}

function ThreadSettings({ thread, providers, onClose, onSaved }: { thread: Thread; providers: any; onClose: () => void;
  onSaved: (t: Thread) => void }): ReactNode {
  const { ws } = useApp();
  const skills = useLoad(() => get<any[]>(wsPath(ws, "/skills")), [ws.id]);
  const [providerId, setProviderId] = useState(thread.provider_id || "");
  const [model, setModel] = useState(thread.model || "");
  const [pinned, setPinned] = useState<string[]>(thread.skills);
  const [visibility, setVisibility] = useState(thread.visibility);
  const [title, setTitle] = useState(thread.title);
  const { busy, run } = useAction();
  const current = (providers?.providers || []).find((p: any) => p.id === providerId);

  async function save() {
    const t = await run(() => patch<Thread>(wsPath(ws, `/threads/${thread.id}`), {
      title, provider_id: providerId || null, model: model || null, skills: pinned, visibility }), tr("Saved"));
    if (t) { onSaved(t); onClose(); }
  }

  return (
    <Modal title={tr("Conversation settings")} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>{tr("Cancel")}</button>
      <button className="btn primary" disabled={busy} onClick={save}>{tr("Save")}</button></>}>
      <div className="stack">
        <Field label={tr("Title")}><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <div className="grid2">
          <Field label={tr("Model provider")}>
            <select className="select" value={providerId} onChange={(e) => { setProviderId(e.target.value); setModel(""); }}>
              <option value="">{tr("Workspace default")}</option>
              {(providers?.providers || []).map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </Field>
          <Field label={tr("Model")} hint={tr("Pick from the list or type any model id the provider offers.")}>
            <input className="input" list="models" value={model} disabled={!providerId} onChange={(e) => setModel(e.target.value)}
              placeholder={providerId ? tr("model id") : tr("default")} />
            <datalist id="models">{(current?.models || []).map((m: string) => <option key={m} value={m} />)}</datalist>
          </Field>
        </div>
        <Field label={tr("Who can see this conversation")}>
          <div className="seg">
            {["workspace", "private"].map((v) => <button type="button" key={v} aria-pressed={visibility === v}
              onClick={() => setVisibility(v)}>{v === "workspace" ? tr("Everyone in the workspace") : tr("Only me")}</button>)}
          </div>
        </Field>
        <div>
          <div className="field"><span>{tr("Pinned skills")}</span><small>{tr("Pinned skills are always followed in this conversation. Others are loaded automatically when relevant.")}</small></div>
          <div className="stack" style={{ maxHeight: 260, overflow: "auto", marginTop: 8 }}>
            {(skills.data || []).filter((s) => s.enabled).map((s) => (
              <label key={s.name} className="check">
                <input type="checkbox" checked={pinned.includes(s.name)} onChange={(e) => setPinned(e.target.checked
                  ? [...pinned, s.name].slice(0, 10) : pinned.filter((x) => x !== s.name))} />
                <span><strong>{s.name}</strong><br /><span className="small muted">{s.description}</span></span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}
