import { useState, type ReactNode } from "react";
import { post } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { fmtDateTime } from "../lib/format";
import { useAction } from "./ui";
import { tr } from "../lib/i18n";

/** An approval voucher: the exact action the agent or a workflow wants to take. */
export function Approval({ approval, onDecided }: { approval: any; onDecided: (a: any) => void }): ReactNode {
  const { ws } = useApp();
  const { busy, run } = useAction();
  const [note, setNote] = useState("");
  const pending = approval.status === "pending";

  async function decide(approve: boolean) {
    const a = await run(() => post(wsPath(ws, `/approvals/${approval.id}/decide`), { approve, note: note || null }),
      approve ? "Approved" : "Rejected");
    if (a) onDecided(a);
  }

  return (
    <div className="voucher" role="group" aria-label={tr("Approval request")}>
      <div className="vhead">
        <span>{approval.source === "workflow" ? tr("Workflow step") : tr("Proposed by the assistant")}
          {approval.requested_by_name ? ` for ${approval.requested_by_name}` : ""}, {fmtDateTime(approval.created_at)}</span>
        <span className={`stamp ${approval.status}`}>{approval.status.toUpperCase()}</span>
      </div>
      <div className="vbody">{approval.summary}</div>
      {approval.arguments && Object.keys(approval.arguments).length > 0 && approval.tool !== "workflow.approval" && (
        <details className="small"><summary className="muted">{tr("Exact arguments")}</summary>
          <pre className="xs" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(approval.arguments, null, 2)}</pre></details>
      )}
      {pending && can(ws, "editor") && (
        <div className="row wrap" style={{ marginTop: 10 }}>
          <input className="input grow" placeholder={tr("Note (optional)")} value={note} onChange={(e) => setNote(e.target.value)}
            aria-label={tr("Note")} style={{ maxWidth: 360 }} />
          <button className="btn primary" disabled={busy} onClick={() => decide(true)}>{tr("Approve and run")}</button>
          <button className="btn danger" disabled={busy} onClick={() => decide(false)}>{tr("Reject")}</button>
        </div>
      )}
      {pending && !can(ws, "editor") && <p className="small muted">{tr("An editor, admin or owner must approve this.")}</p>}
      {!pending && approval.decided_by_name && (
        <p className="small muted">{approval.status === "approved" ? tr("Approved") : tr("Decided")}{" "}{tr("by")}{" "}{approval.decided_by_name},
          {" "}{fmtDateTime(approval.decided_at)}</p>
      )}
    </div>
  );
}
