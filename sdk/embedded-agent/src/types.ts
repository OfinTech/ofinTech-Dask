/** AG-UI wire types the SDK consumes and produces (names as published by the AG-UI specification). */

export type Locale = "en" | "ar";

export interface AgUiTextPart { type: "text"; text: string }

export interface AgUiMessage {
  id: string;
  role: "user" | "assistant" | "system" | "developer" | "tool";
  content?: string | AgUiTextPart[];
  name?: string;
}

export interface AgUiContextItem { description: string; value: string }

export interface AgUiResumeItem {
  interruptId: string;
  status: "resolved" | "cancelled";
  payload?: Record<string, unknown>;
}

export interface RunAgentInput {
  threadId: string;
  runId: string;
  messages: AgUiMessage[];
  state?: unknown;
  tools?: unknown[];
  context?: AgUiContextItem[];
  forwardedProps?: Record<string, unknown>;
  resume?: AgUiResumeItem[];
}

export interface AgUiInterrupt {
  id: string;
  reason: string;
  message?: string;
  toolCallId?: string;
  expiresAt?: string;
  responseSchema?: unknown;
  metadata?: { ofintech?: OfinTechApprovalMeta } & Record<string, unknown>;
}

export interface OfinTechApprovalMeta {
  version: number;
  tool?: string;
  arguments?: Record<string, unknown>;
  decided_in?: "desk";
  approval_url?: string;
}

export type AgUiEvent =
  | { type: "RUN_STARTED"; threadId: string; runId: string; metadata?: { ofintech?: { version: number; desk_run_id: string; agent_id: string } } }
  | { type: "RUN_FINISHED"; threadId: string; runId: string; outcome?: { type: "success" } | { type: "interrupt"; interrupts: AgUiInterrupt[] }; result?: { text?: string }; usage?: { input_tokens?: number; output_tokens?: number } }
  | { type: "RUN_ERROR"; message: string; code?: string; metadata?: { ofintech?: Record<string, unknown> } }
  | { type: "STEP_STARTED"; stepName: string }
  | { type: "STEP_FINISHED"; stepName: string }
  | { type: "TEXT_MESSAGE_START"; messageId: string; role: "assistant" }
  | { type: "TEXT_MESSAGE_CONTENT"; messageId: string; delta: string }
  | { type: "TEXT_MESSAGE_END"; messageId: string }
  | { type: "TOOL_CALL_START"; toolCallId: string; toolCallName: string; parentMessageId?: string }
  | { type: "TOOL_CALL_ARGS"; toolCallId: string; delta: string }
  | { type: "TOOL_CALL_END"; toolCallId: string }
  | { type: "TOOL_CALL_RESULT"; messageId: string; toolCallId: string; content: string; role?: "tool" }
  | { type: "STATE_SNAPSHOT"; snapshot: unknown }
  | { type: "CUSTOM"; name: string; value: unknown }
  | { type: string; [key: string]: unknown };

/** A validated generative-UI payload (CUSTOM event `ofintech.component`). */
export interface ComponentPayload {
  version: 1;
  component: string;
  props: Record<string, unknown>;
  toolCallId?: string;
}

export interface Notice { kind: string; text: string }

/** What the SDK keeps for one conversation turn. */
export interface ToolCallView {
  id: string;
  name: string;
  args: string;
  result?: string;
}

export interface AssistantTurn {
  kind: "assistant";
  id: string;
  text: string;
  done: boolean;
  toolCalls: ToolCallView[];
  components: ComponentPayload[];
  notices: Notice[];
  unverifiedFigures: string[];
}

export interface UserTurn { kind: "user"; id: string; text: string }

export type Turn = UserTurn | AssistantTurn;

export type RunStatus = "idle" | "running" | "waiting_approval" | "error";

export interface AgentDescriptor {
  id: string;
  name: Record<string, string>;
  description: Record<string, string>;
  capability: string;
  read_only: boolean;
  tool_categories: string[];
}
