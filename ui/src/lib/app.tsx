import { createContext, useContext } from "react";

export type Workspace = {
  id: string; name: string; role: "viewer" | "editor" | "admin" | "owner"; base_currency: string; country: string;
  fiscal_year_start_month: number; accounting_framework: string; timezone: string; egress_policy?: "open" | "sealed";
};

export type Session = {
  version: string; mode: "desktop" | "server"; signup_open: boolean; needs_first_user: boolean; public_url: string;
  features: { stdio_mcp: boolean; whatsapp: boolean; team: boolean; google_login?: boolean; email?: boolean; sealed?: boolean };
  user: { id: string; email: string; name: string; instance_admin: boolean } | null;
  workspaces: Workspace[];
};

export type AppState = {
  session: Session;
  ws: Workspace;
  reloadSession: () => Promise<void>;
  counts: { unread: number; pending_approvals: number };
  refreshCounts: () => void;
};

export const AppCtx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const v = useContext(AppCtx);
  if (!v) throw new Error("no app context");
  return v;
}

const ORDER = ["viewer", "editor", "admin", "owner"];
export function can(ws: Workspace, role: Workspace["role"]): boolean {
  return ORDER.indexOf(ws.role) >= ORDER.indexOf(role);
}

export function wsPath(ws: Workspace, rest: string): string {
  return `/api/w/${ws.id}${rest}`;
}
