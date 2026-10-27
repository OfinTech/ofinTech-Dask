import { useState, type ReactNode } from "react";
import { del, get, post } from "../lib/api";
import { useApp, wsPath } from "../lib/app";
import { fmtRelative } from "../lib/format";
import { useAction, useLoad } from "./ui";
import { tr } from "../lib/i18n";

const MENTION = /@\[([^\]]+)\]\((u_[a-z0-9]+)\)/g;

function render(body: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  MENTION.lastIndex = 0;
  while ((m = MENTION.exec(body))) {
    if (m.index > last) out.push(body.slice(last, m.index));
    out.push(<strong key={m.index} className="tag blue">@{m[1]}</strong>);
    last = m.index + m[0].length;
  }
  out.push(body.slice(last));
  return out;
}

/** Comment thread with @mentions. Type @ and pick a colleague; they get a notification. */
export function Comments({ targetType, targetId }: { targetType: string; targetId: string }): ReactNode {
  const { ws, session } = useApp();
  const list = useLoad(() => get<any[]>(wsPath(ws, `/comments?target_type=${targetType}&target_id=${targetId}`)), [targetId]);
  const members = useLoad(() => get<any[]>(wsPath(ws, "/members")), [ws.id]);
  const [body, setBody] = useState("");
  const { busy, run } = useAction();
  const atMatch = /@(\w*)$/.exec(body);
  const suggestions = atMatch ? (members.data || []).filter((m) => m.id !== session.user?.id &&
    m.name.toLowerCase().startsWith(atMatch[1].toLowerCase())).slice(0, 5) : [];

  async function submit() {
    if (!body.trim()) return;
    const r = await run(() => post(wsPath(ws, "/comments"), { target_type: targetType, target_id: targetId, body }));
    if (r) { setBody(""); list.reload(); }
  }

  return (
    <div className="panel panel-pad">
      <h3 style={{ marginBottom: 8 }}>{tr("Comments")}</h3>
      {(list.data || []).length === 0 && <p className="small muted">{tr("No comments yet.")}</p>}
      <ul className="list">
        {(list.data || []).map((c) => (
          <li key={c.id} style={{ padding: "8px 0" }}>
            <div className="spread"><strong className="small">{c.author_name}</strong>
              <span className="xs muted">{fmtRelative(c.created_at)}
                {c.author_id === session.user?.id && <button className="btn ghost small" onClick={() =>
                  run(() => del(wsPath(ws, `/comments/${c.id}`))).then(list.reload)}>{tr("Delete")}</button>}</span></div>
            <div style={{ whiteSpace: "pre-wrap" }}>{render(c.body)}</div>
          </li>
        ))}
      </ul>
      <div style={{ position: "relative", marginTop: 8 }}>
        <textarea className="textarea" rows={2} value={body} placeholder={tr("Add a comment. Type @ to mention someone.")}
          onChange={(e) => setBody(e.target.value)} aria-label={tr("Comment")} />
        {suggestions.length > 0 && (
          <div className="panel" style={{ position: "absolute", bottom: "100%", insetInlineStart: 0, zIndex: 3, padding: 4 }}>
            {suggestions.map((m) => (
              <button key={m.id} className="btn ghost small" style={{ display: "block", width: "100%", textAlign: "left" }}
                onClick={() => setBody(body.replace(/@(\w*)$/, `@[${m.name}](${m.id}) `))}>{m.name}</button>
            ))}
          </div>
        )}
        <div className="row" style={{ marginTop: 6 }}>
          <button className="btn small primary" disabled={busy || !body.trim()} onClick={submit}>{tr("Comment")}</button>
        </div>
      </div>
    </div>
  );
}
