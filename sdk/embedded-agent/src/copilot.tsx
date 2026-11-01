import { useState, type FormEvent, type ReactNode } from "react";
import { RENDERERS } from "./components";
import { dirOf, t } from "./i18n";
import { useOfinTechAgent } from "./provider";
import type { ComponentProps } from "./registry";
import type { AssistantTurn, ComponentPayload } from "./types";

export interface OfinTechFinancialCopilotProps {
  title?: string;
  /** Items the host wants the agent to know about the current screen (treated as untrusted page content). */
  context?: { description: string; value: string }[];
  onOpenSource?: ComponentProps["onOpenSource"];
  /** Show the tool calls the agent made under each answer (default true). */
  showToolCalls?: boolean;
  className?: string;
}

export function RenderComponent({ payload, locale, onOpenSource }: { payload: ComponentPayload; locale: "en" | "ar"; onOpenSource?: ComponentProps["onOpenSource"] }): ReactNode {
  const Renderer = (RENDERERS as Record<string, (p: ComponentProps<never>) => ReactNode>)[payload.component];
  if (!Renderer) {
    // the registry already refused unknown names; this is the belt to that braces
    return <pre className="ofe-fallback">{JSON.stringify(payload.props, null, 2)}</pre>;
  }
  return <Renderer props={payload.props as never} locale={locale} onOpenSource={onOpenSource} />;
}

function AssistantBubble({ turn, locale, showToolCalls, onOpenSource }: { turn: AssistantTurn; locale: "en" | "ar"; showToolCalls: boolean; onOpenSource?: ComponentProps["onOpenSource"] }): ReactNode {
  return (
    <div className="ofe-turn ofe-assistant">
      {showToolCalls && turn.toolCalls.length > 0 && (
        <div className="ofe-tools">
          {turn.toolCalls.map((c) => <span key={c.id} className="ofe-badge">{t(locale, "tool")} {c.name}</span>)}
        </div>
      )}
      {turn.text ? <div className="ofe-text">{turn.text}</div> : null}
      {turn.components.length > 0 && (
        <div className="ofe-components">
          {turn.components.map((c, i) => <RenderComponent key={i} payload={c} locale={locale} onOpenSource={onOpenSource} />)}
        </div>
      )}
      {turn.notices.map((n, i) => <div key={i} className="ofe-notice">{n.text}</div>)}
    </div>
  );
}

export function OfinTechFinancialCopilot({ title, context, onOpenSource, showToolCalls = true, className }: OfinTechFinancialCopilotProps): ReactNode {
  const agent = useOfinTechAgent();
  const { locale } = agent;
  const [draft, setDraft] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || agent.status === "running") return;
    setDraft("");
    void agent.send(text, { context });
  };

  return (
    <section className={"ofe-copilot" + (className ? " " + className : "")} dir={dirOf(locale)} lang={locale} aria-label={title ?? t(locale, "title")}>
      <header className="ofe-header">
        <h2>{title ?? t(locale, "title")}</h2>
        <p className="ofe-muted ofe-readonly">{t(locale, "readOnly")}</p>
      </header>
      <div className="ofe-turns" role="log" aria-live="polite">
        {agent.turns.map((turn) =>
          turn.kind === "user" ? (
            <div key={turn.id} className="ofe-turn ofe-user"><div className="ofe-text">{turn.text}</div></div>
          ) : (
            <AssistantBubble key={turn.id} turn={turn} locale={locale} showToolCalls={showToolCalls} onOpenSource={onOpenSource} />
          ),
        )}
        {agent.status === "running" && <div className="ofe-muted ofe-thinking">{t(locale, "thinking")}</div>}
        {agent.status === "waiting_approval" && agent.interrupts.map((i) => (
          <RenderComponent key={i.id} locale={locale} payload={{
            version: 1, component: "ApprovalRequestCard",
            props: { version: 1, approval_id: i.id, tool: i.metadata?.ofintech?.tool ?? "", summary: i.message ?? "", decided_in: "desk",
                     ...(i.expiresAt ? { expires_at: i.expiresAt } : {}),
                     ...(i.metadata?.ofintech?.approval_url ? { approval_url: i.metadata.ofintech.approval_url } : {}) },
          }} />
        ))}
        {agent.error && (
          <div className="ofe-error" role="alert">
            <strong>{t(locale, "error")}</strong> <span>{agent.error.message}</span>
            {agent.deskRunId && agent.error.code === "stream_dropped" ? <button type="button" className="ofe-btn" onClick={() => void agent.reattach()}>{t(locale, "retry")}</button> : null}
          </div>
        )}
      </div>
      <form className="ofe-compose" onSubmit={submit}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t(locale, "placeholder")} aria-label={t(locale, "placeholder")} maxLength={8000} />
        {agent.status === "running" ? (
          <button type="button" className="ofe-btn" onClick={() => void agent.stop()}>{t(locale, "stop")}</button>
        ) : (
          <button type="submit" className="ofe-btn ofe-primary" disabled={!draft.trim()}>{t(locale, "send")}</button>
        )}
      </form>
    </section>
  );
}
