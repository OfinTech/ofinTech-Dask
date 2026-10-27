import { useEffect, useState, type ReactNode } from "react";
import { get, post } from "../lib/api";
import { useApp, wsPath } from "../lib/app";
import { fmtRelative } from "../lib/format";
import { Approval } from "../components/approval";
import { Empty, ErrorBox, Loading, PageHead, useLoad } from "../components/ui";
import { tr } from "../lib/i18n";

export function InboxPage(): ReactNode {
  const { ws, refreshCounts } = useApp();
  const [status, setStatus] = useState("pending");
  const approvals = useLoad(() => get<any[]>(wsPath(ws, `/approvals?status=${status}`)), [ws.id, status]);
  const notes = useLoad(() => get<any>(wsPath(ws, "/notifications")), [ws.id]);

  useEffect(() => {
    if (notes.data?.unread) post(wsPath(ws, "/notifications/read")).then(refreshCounts).catch(() => undefined);
  }, [notes.data]);

  return (
    <div className="page">
      <PageHead title={tr("Inbox")} sub={tr("Actions waiting for approval, and messages for you.")} />
      <div className="spread" style={{ marginBottom: 12 }}>
        <h2>{tr("Approvals")}</h2>
        <div className="seg" role="group" aria-label={tr("Filter approvals")}>
          {[["pending", "Waiting"], ["approved", "Approved"], ["rejected", "Rejected"], ["all", "All"]].map(([v, l]) => (
            <button key={v} aria-pressed={status === v} onClick={() => setStatus(v)}>{l}</button>))}
        </div>
      </div>
      <ErrorBox error={approvals.error} />
      {approvals.loading ? <Loading /> : (approvals.data || []).length === 0 ? (
        <div className="panel"><Empty title={status === "pending" ? tr("Nothing waiting for approval") : tr("No approvals here")}>
          {tr("When the assistant or a workflow wants to send an e-mail or message, run a workflow or call an external tool, the request appears here first.")}</Empty></div>
      ) : (approvals.data || []).map((a) => (
        <div key={a.id}>
          <Approval approval={a} onDecided={() => { approvals.reload(); refreshCounts(); }} />
          {a.thread_id && <a className="small" href={`#/chat/${a.thread_id}`}>{tr("Open the conversation")}</a>}
          {a.workflow_id && <a className="small" href={`#/workflows/${a.workflow_id}?run=${a.source_id}`}>{tr("Open the workflow run")}</a>}
        </div>
      ))}
      <div className="section">
        <h2>{tr("Notifications")}</h2>
        {(notes.data?.items || []).length === 0 ? <p className="muted">{tr("No notifications.")}</p> : (
          <ul className="list panel">
            {notes.data.items.map((n: any) => (
              <li key={n.id}>
                <div className="spread"><span>{n.link ? <a href={`#${n.link.startsWith("/") ? n.link : "/" + n.link}`}>{n.title}</a> : n.title}</span>
                  <span className="xs muted">{fmtRelative(n.created_at)}</span></div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
