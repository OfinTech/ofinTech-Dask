import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { AgUiClient, GatewayError, newId, type TokenProvider } from "./client";
import { acceptComponent } from "./registry";
import type { AgUiEvent, AgUiInterrupt, AssistantTurn, Locale, RunStatus, Turn } from "./types";

export interface OfinTechAgentProviderProps {
  /** `https://desk.example.com/api/embedded` */
  endpoint: string;
  /** Returns a session token minted by your backend (`POST /api/embedded/sessions` with the integration key). */
  tokenProvider: TokenProvider;
  agentId?: string;
  locale?: Locale;
  /** A stable id per conversation; defaults to one per provider instance. */
  threadId?: string;
  children: ReactNode;
}

export interface AgentState {
  turns: Turn[];
  status: RunStatus;
  error: { code: string; message: string } | null;
  interrupts: AgUiInterrupt[];
  deskRunId: string | null;
  locale: Locale;
  agentId: string;
  threadId: string;
  send: (text: string, options?: { context?: { description: string; value: string }[]; state?: unknown }) => Promise<void>;
  stop: () => Promise<void>;
  reattach: () => Promise<void>;
  reset: () => void;
}

const Ctx = createContext<AgentState | null>(null);

function newAssistant(id: string): AssistantTurn {
  return { kind: "assistant", id, text: "", done: false, toolCalls: [], components: [], notices: [], unverifiedFigures: [] };
}

export function OfinTechAgentProvider({ endpoint, tokenProvider, agentId = "finance-copilot", locale = "en", threadId, children }: OfinTechAgentProviderProps): ReactNode {
  const client = useMemo(() => new AgUiClient({ endpoint, tokenProvider }), [endpoint, tokenProvider]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [status, setStatus] = useState<RunStatus>("idle");
  const [error, setError] = useState<AgentState["error"]>(null);
  const [interrupts, setInterrupts] = useState<AgUiInterrupt[]>([]);
  const [deskRunId, setDeskRunId] = useState<string | null>(null);
  const thread = useMemo(() => threadId ?? newId("thread"), [threadId]);
  const abort = useRef<AbortController | null>(null);
  const lastRunId = useRef<string>("");

  const apply = useCallback((ev: AgUiEvent) => {
    switch (ev.type) {
      case "RUN_STARTED": {
        const id = (ev as { metadata?: { ofintech?: { desk_run_id?: string } } }).metadata?.ofintech?.desk_run_id;
        if (id) setDeskRunId(id);
        return;
      }
      case "TEXT_MESSAGE_START":
        setTurns((ts) => (ts.some((t) => t.id === ev.messageId) ? ts : [...ts, newAssistant(ev.messageId as string)]));
        return;
      case "TEXT_MESSAGE_CONTENT":
        setTurns((ts) => ts.map((t) => (t.kind === "assistant" && t.id === ev.messageId ? { ...t, text: t.text + (ev.delta as string) } : t)));
        return;
      case "TEXT_MESSAGE_END":
        setTurns((ts) => ts.map((t) => (t.kind === "assistant" && t.id === ev.messageId ? { ...t, done: true } : t)));
        return;
      case "TOOL_CALL_START":
        setTurns((ts) => {
          const last = [...ts].reverse().find((t) => t.kind === "assistant") as AssistantTurn | undefined;
          if (!last) return ts;
          return ts.map((t) => (t === last ? { ...last, toolCalls: [...last.toolCalls, { id: ev.toolCallId as string, name: ev.toolCallName as string, args: "" }] } : t));
        });
        return;
      case "TOOL_CALL_ARGS":
        setTurns((ts) => ts.map((t) => (t.kind === "assistant" ? { ...t, toolCalls: t.toolCalls.map((c) => (c.id === ev.toolCallId ? { ...c, args: c.args + (ev.delta as string) } : c)) } : t)));
        return;
      case "TOOL_CALL_RESULT":
        setTurns((ts) => ts.map((t) => (t.kind === "assistant" ? { ...t, toolCalls: t.toolCalls.map((c) => (c.id === ev.toolCallId ? { ...c, result: ev.content as string } : c)) } : t)));
        return;
      case "CUSTOM": {
        const name = ev.name as string;
        setTurns((ts) => {
          const last = [...ts].reverse().find((t) => t.kind === "assistant") as AssistantTurn | undefined;
          if (!last) return ts;
          let next = last;
          if (name === "ofintech.component") {
            const comp = acceptComponent(ev.value);
            if (comp) next = { ...last, components: [...last.components, comp] };
          } else if (name === "ofintech.unverified_figures") {
            const v = ev.value as { figures?: unknown };
            const figures = Array.isArray(v?.figures) ? v.figures.filter((f): f is string => typeof f === "string") : [];
            next = { ...last, unverifiedFigures: figures };
          } else if (name === "ofintech.notice") {
            const v = ev.value as { kind?: unknown; text?: unknown };
            next = { ...last, notices: [...last.notices, { kind: String(v?.kind ?? "notice"), text: String(v?.text ?? "") }] };
          }
          return next === last ? ts : ts.map((t) => (t === last ? next : t));
        });
        return;
      }
      case "RUN_FINISHED": {
        const outcome = (ev as { outcome?: { type: string; interrupts?: AgUiInterrupt[] } }).outcome;
        if (outcome?.type === "interrupt") {
          setInterrupts(outcome.interrupts ?? []);
          setStatus("waiting_approval");
        } else {
          setInterrupts([]);
          setStatus("idle");
        }
        return;
      }
      case "RUN_ERROR":
        setError({ code: (ev.code as string) ?? "run_error", message: ev.message as string });
        setStatus("error");
        return;
      default:
        return;
    }
  }, []);

  const consume = useCallback(async (events: AsyncGenerator<AgUiEvent>) => {
    for await (const ev of events) apply(ev);
  }, [apply]);

  const send = useCallback<AgentState["send"]>(async (text, options) => {
    if (status === "running") return;
    const runId = newId("run");
    lastRunId.current = runId;
    setError(null);
    setStatus("running");
    setTurns((ts) => [...ts, { kind: "user", id: newId("m"), text }]);
    const controller = new AbortController();
    abort.current = controller;
    try {
      await consume(client.run(agentId, {
        threadId: thread,
        runId,
        messages: [{ id: newId("m"), role: "user", content: text }],
        ...(options?.state !== undefined ? { state: options.state } : {}),
        ...(options?.context ? { context: options.context } : {}),
        forwardedProps: { locale },
        ...(interrupts.length ? { resume: interrupts.map((i) => ({ interruptId: i.id, status: "cancelled" as const })) } : {}),
      }, controller.signal));
      setStatus((s) => (s === "running" ? "idle" : s));
    } catch (e) {
      if (controller.signal.aborted) { setStatus("idle"); return; }
      const err = e instanceof GatewayError ? { code: e.code, message: e.message } : { code: "network", message: String((e as Error)?.message ?? e) };
      setError(err);
      setStatus("error");
    } finally {
      abort.current = null;
    }
  }, [agentId, client, consume, interrupts, locale, status, thread]);

  const stop = useCallback(async () => {
    abort.current?.abort();
    if (deskRunId) { try { await client.cancel(agentId, deskRunId); } catch { /* best effort */ } }
    setStatus("idle");
  }, [agentId, client, deskRunId]);

  const reattach = useCallback(async () => {
    if (!deskRunId || status === "running") return;
    setError(null);
    setStatus("running");
    const controller = new AbortController();
    abort.current = controller;
    try {
      await consume(client.attach(agentId, deskRunId, thread, lastRunId.current || newId("run"), controller.signal));
      setStatus((s) => (s === "running" ? "idle" : s));
    } catch (e) {
      const err = e instanceof GatewayError ? { code: e.code, message: e.message } : { code: "network", message: String((e as Error)?.message ?? e) };
      setError(err);
      setStatus("error");
    } finally {
      abort.current = null;
    }
  }, [agentId, client, consume, deskRunId, status, thread]);

  const reset = useCallback(() => {
    abort.current?.abort();
    setTurns([]); setStatus("idle"); setError(null); setInterrupts([]); setDeskRunId(null);
  }, []);

  const value = useMemo<AgentState>(() => ({ turns, status, error, interrupts, deskRunId, locale, agentId, threadId: thread, send, stop, reattach, reset }),
    [turns, status, error, interrupts, deskRunId, locale, agentId, thread, send, stop, reattach, reset]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useOfinTechAgent(): AgentState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useOfinTechAgent must be used inside <OfinTechAgentProvider>.");
  return v;
}
