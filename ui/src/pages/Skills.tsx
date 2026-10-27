import { useRef, useState, type ReactNode } from "react";
import { del, download, get, post, put, upload } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { Markdown } from "../lib/markdown";
import { Confirm, ErrorBox, Field, Icon, Loading, Modal, PageHead, go, useAction, useLoad } from "../components/ui";
import { tr } from "../lib/i18n";

const TEMPLATE = `---
name: expense-policy
description: Our travel and expense policy; use for any question about expense limits or approvals.
version: 1.0.0
tags: [policy]
---

# Expense policy

- Hotels: up to 180 per night in the base currency.
- Meals: up to 60 per day when travelling.
- Approvals: expenses above 1,000 need the finance manager.

When answering, quote the rule and say what to do if the expense is above the limit.
`;

export function SkillsPage({ name }: { name?: string }): ReactNode {
  const { ws } = useApp();
  const list = useLoad(() => get<any[]>(wsPath(ws, "/skills")), [ws.id]);
  const [editing, setEditing] = useState<{ id?: string; markdown: string } | null>(null);
  const [filter, setFilter] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const { run } = useAction();

  async function importFile(f: File) {
    const r: any = await run(() => upload(wsPath(ws, "/skills/import"), f));
    if (r) {
      list.reload();
      if (r.note) alertNote(r.note + " Ignored: " + r.ignored_files.join(", "));
    }
  }
  const [note, alertNote] = useState<string | null>(null);

  const items = (list.data || []).filter((s) => !filter || (s.name + s.description + s.tags.join(" ")).toLowerCase().includes(filter.toLowerCase()));
  const groups = [["custom", "Your skills"], ["builtin", "Built-in finance skills"]] as const;

  return (
    <div className="page">
      <PageHead title={tr("Skills")} sub={tr("Know-how the assistant follows: checklists, policies, methods. It reads a skill when a request matches its description. Skills are instructions only; nothing in them is executed.")}
        actions={can(ws, "editor") && <>
          <input ref={file} type="file" accept=".md,.zip" className="hidden" onChange={(e) => e.target.files?.[0] && importFile(e.target.files[0])} />
          <button className="btn" onClick={() => file.current?.click()}><Icon name="upload" />{tr("Import")}</button>
          <button className="btn primary" onClick={() => setEditing({ markdown: TEMPLATE })}><Icon name="plus" />{tr("New skill")}</button>
        </>} />
      {note && <div className="notice warn" style={{ marginBottom: 12 }}>{note}</div>}
      <input className="input" style={{ maxWidth: 320, marginBottom: 16 }} placeholder={tr("Filter skills")} value={filter}
        onChange={(e) => setFilter(e.target.value)} aria-label={tr("Filter skills")} />
      <ErrorBox error={list.error} />
      {list.loading ? <Loading /> : groups.map(([source, title]) => {
        const group = items.filter((s) => s.source === source);
        if (!group.length) return null;
        return (
          <div className="section" key={source} style={{ marginTop: 16 }}>
            <h2>{title}</h2>
            <ul className="list panel">
              {group.map((s) => (
                <li key={s.id}>
                  <div className="spread">
                    <a href={`#/skills/${s.name}`}><strong>{s.name}</strong></a>
                    <span className="row">
                      {s.tags.map((t: string) => <span key={t} className="tag">{t}</span>)}
                      {can(ws, "admin") && <label className="check small"><input type="checkbox" checked={s.enabled}
                        onChange={(e) => run(() => put(wsPath(ws, `/skills/${s.id}/enabled`), { enabled: e.target.checked })).then(list.reload)} />{tr("On")}</label>}
                    </span>
                  </div>
                  <div className="small muted">{s.description}</div>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      {name && <SkillView name={name} onEdit={(id, md) => setEditing({ id, markdown: md })} onClose={() => go("skills")} onChanged={list.reload} />}
      {editing && <SkillEditor initial={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); list.reload(); }} />}
    </div>
  );
}

function toMarkdown(s: any): string {
  return `---\nname: ${s.name}\ndescription: ${JSON.stringify(s.description)}\nversion: ${s.version}\ntags: [${s.tags.join(", ")}]\n---\n\n${s.body}\n`;
}

function SkillView({ name, onEdit, onClose, onChanged }: { name: string; onEdit: (id: string | undefined, md: string) => void;
  onClose: () => void; onChanged: () => void }): ReactNode {
  const { ws } = useApp();
  const s = useLoad(() => get<any>(wsPath(ws, `/skills/${encodeURIComponent(name)}`)), [name]);
  const [confirm, setConfirm] = useState(false);
  const { run } = useAction();
  const sk = s.data;
  return (
    <Modal wide title={name} onClose={onClose} footer={sk && <>
      <button className="btn" onClick={() => download(wsPath(ws, `/skills/${name}/export`), `${name}-SKILL.md`)}>{tr("Export SKILL.md")}</button>
      {can(ws, "editor") && sk.source === "builtin" && <button className="btn" onClick={() => onEdit(undefined,
        toMarkdown({ ...sk, name: `${sk.name}-custom` }))}>{tr("Make my own copy")}</button>}
      {can(ws, "editor") && sk.source === "custom" && <>
        <button className="btn danger" onClick={() => setConfirm(true)}>{tr("Delete")}</button>
        <button className="btn primary" onClick={() => onEdit(sk.id, toMarkdown(sk))}>{tr("Edit")}</button></>}
    </>}>
      {!sk ? <Loading /> : (
        <div className="stack">
          <p className="muted">{sk.description}</p>
          <div className="panel panel-pad"><Markdown text={sk.body} /></div>
          {Object.keys(sk.files || {}).length > 0 && <p className="small muted">{tr("Reference files:")}{" "}{Object.keys(sk.files).join(", ")}</p>}
        </div>
      )}
      {confirm && <Confirm text={tr("Delete the skill {0}?", { 0: name })} onClose={() => setConfirm(false)} onYes={async () => {
        await run(() => del(wsPath(ws, `/skills/${sk.id}`)));
        onChanged();
        onClose();
      }} />}
    </Modal>
  );
}

function SkillEditor({ initial, onClose, onSaved }: { initial: { id?: string; markdown: string }; onClose: () => void;
  onSaved: () => void }): ReactNode {
  const { ws } = useApp();
  const [md, setMd] = useState(initial.markdown);
  const { busy, run } = useAction();
  async function save() {
    const r = await run(() => initial.id ? put(wsPath(ws, `/skills/${initial.id}`), { markdown: md })
      : post(wsPath(ws, "/skills"), { markdown: md }), tr("Skill saved"));
    if (r) onSaved();
  }
  return (
    <Modal wide title={initial.id ? tr("Edit skill") : tr("New skill")} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>{tr("Cancel")}</button>
      <button className="btn primary" disabled={busy} onClick={save}>{tr("Save skill")}</button></>}>
      <div className="grid2" style={{ alignItems: "start" }}>
        <Field label={tr("SKILL.md")} hint={tr("Front matter needs a name (lowercase, hyphens) and a description that says when to use it.")}>
          <textarea className="textarea code-input" style={{ minHeight: 420 }} value={md} onChange={(e) => setMd(e.target.value)} />
        </Field>
        <div><div className="field"><span>{tr("Preview")}</span></div>
          <div className="panel panel-pad" style={{ maxHeight: 440, overflow: "auto" }}><Markdown text={md.replace(/^---[\s\S]*?---\s*/, "")} /></div></div>
      </div>
    </Modal>
  );
}
