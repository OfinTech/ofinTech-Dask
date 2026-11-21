// Harbor Line, the demo company, on the two screens a new workspace opens on (`desk_engine/demo.py`).
//
// A workspace with nothing in it said "No general ledger yet" and offered only an import, so the one thing the
// product is sold on -- a figure you can click until you reach the entry behind it -- could not be seen without
// first exporting your own books. The button loads three invented files and the overview fills up.
//
// It offers itself only where it would work: an editor, a build that carries the files, and a workspace that has
// not already loaded them. Everywhere else it renders nothing rather than a button that explains why it is
// disabled.
import { type ReactNode } from "react";
import { del, get, post } from "../lib/api";
import { can, useApp, wsPath } from "../lib/app";
import { useAction, useLoad } from "../components/ui";
import { tr } from "../lib/i18n";

type DemoState = { available: boolean; company: string; loaded: { id: string; name: string }[] };

/** Offered in an empty state. `onLoaded` lets the page that owns the empty state refetch itself. */
export function TryDemoCompany({ onLoaded }: { onLoaded?: () => void }): ReactNode {
  const { ws } = useApp();
  const state = useLoad<DemoState>(() => get(wsPath(ws, "/demo")), [ws.id]);
  const { run, busy } = useAction();
  if (!can(ws, "editor") || !state.data?.available || state.data.loaded.length) return null;
  return (
    <button className="btn primary" disabled={busy} onClick={() => {
      void run(() => post(wsPath(ws, "/demo")), tr("The demo company is loaded.")).then(() => { state.reload(); onLoaded?.(); });
    }}>{tr("Try it with a demo company")}</button>
  );
}

/** Shown wherever the demo data is listed, so invented figures are never mistaken for the workspace's own. */
export function DemoCompanyBanner({ onRemoved }: { onRemoved?: () => void }): ReactNode {
  const { ws } = useApp();
  const state = useLoad<DemoState>(() => get(wsPath(ws, "/demo")), [ws.id]);
  const { run, busy } = useAction();
  if (!state.data?.loaded.length) return null;
  return (
    <div className="notice info" style={{ marginBottom: 12 }}>
      <strong>{tr("This workspace holds demo data")}</strong>
      <p style={{ margin: "2px 0 10px" }}>{tr("Harbor Line Logistics is an invented freight company, here so there is something to look at. Removing it deletes only what it brought in.")}</p>
      {can(ws, "editor") && <button className="btn" disabled={busy} onClick={() => {
        void run(() => del(wsPath(ws, "/demo")), tr("The demo company was removed.")).then(() => { state.reload(); onRemoved?.(); });
      }}>{tr("Remove the demo company")}</button>}
    </div>
  );
}
