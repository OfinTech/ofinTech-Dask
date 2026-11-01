/**
 * AG-UI client for the OfinTech Desk embedded gateway. One network destination: `endpoint`. The session token comes
 * from `tokenProvider` (your backend mints it with the integration key; the key itself never reaches the browser).
 * No telemetry, no third-party calls.
 */

import type { AgentDescriptor, AgUiEvent, RunAgentInput } from "./types";

export type TokenProvider = () => Promise<string> | string;

export interface ClientOptions {
  /** `https://desk.example.com/api/embedded` */
  endpoint: string;
  tokenProvider: TokenProvider;
  /** Sent as `X-OfinTech-SDK`; helps the operator's logs. */
  sdkTag?: string;
  fetch?: typeof fetch;
}

export class GatewayError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
    this.name = "GatewayError";
  }
}

function joinUrl(base: string, path: string): string {
  return base.replace(/\/+$/, "") + path;
}

async function readError(resp: Response): Promise<GatewayError> {
  let code = "http_" + resp.status;
  let message = resp.statusText || "Request failed";
  try {
    const body = (await resp.json()) as { error?: string; message?: string };
    if (body.error) code = body.error;
    if (body.message) message = body.message;
  } catch {
    /* not JSON */
  }
  return new GatewayError(resp.status, code, message);
}

/** Parses `data: {...}\n\n` frames; comment frames (`: keep-alive`) are ignored. */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<AgUiEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const data = frame
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart())
          .join("\n");
        if (!data) continue;
        try {
          yield JSON.parse(data) as AgUiEvent;
        } catch {
          /* a malformed frame is skipped, never evaluated */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export class AgUiClient {
  private readonly fetchFn: typeof fetch;

  constructor(private readonly options: ClientOptions) {
    this.fetchFn = options.fetch ?? ((input, init) => fetch(input, init));
  }

  private async headers(extra: Record<string, string> = {}): Promise<Record<string, string>> {
    const token = await this.options.tokenProvider();
    return {
      authorization: `Bearer ${token}`,
      "x-ofintech-sdk": this.options.sdkTag ?? "embedded-agent/0.1.0",
      ...extra,
    };
  }

  async agents(): Promise<{ agents: AgentDescriptor[]; locale: string; expires_at: string }> {
    const resp = await this.fetchFn(joinUrl(this.options.endpoint, "/agents"), { headers: await this.headers() });
    if (!resp.ok) throw await readError(resp);
    return (await resp.json()) as { agents: AgentDescriptor[]; locale: string; expires_at: string };
  }

  /** Starts a run and yields AG-UI events until the stream ends. Abort the signal to cancel the run server-side. */
  async *run(agentId: string, input: RunAgentInput, signal?: AbortSignal): AsyncGenerator<AgUiEvent> {
    const resp = await this.fetchFn(joinUrl(this.options.endpoint, `/agents/${encodeURIComponent(agentId)}/run`), {
      method: "POST",
      headers: await this.headers({ "content-type": "application/json", accept: "text/event-stream" }),
      body: JSON.stringify(input),
      signal,
    });
    if (!resp.ok) throw await readError(resp);
    if (!resp.body) throw new GatewayError(0, "no_body", "The gateway returned no stream.");
    yield* parseSse(resp.body);
  }

  /** Re-attaches to a run (after a network drop, or after an approval was decided in OfinTech Desk). */
  async *attach(agentId: string, deskRunId: string, threadId: string, runId: string, signal?: AbortSignal): AsyncGenerator<AgUiEvent> {
    const q = new URLSearchParams({ threadId, runId });
    const resp = await this.fetchFn(
      joinUrl(this.options.endpoint, `/agents/${encodeURIComponent(agentId)}/runs/${encodeURIComponent(deskRunId)}/events?${q}`),
      { headers: await this.headers({ accept: "text/event-stream" }), signal },
    );
    if (!resp.ok) throw await readError(resp);
    if (!resp.body) throw new GatewayError(0, "no_body", "The gateway returned no stream.");
    yield* parseSse(resp.body);
  }

  async cancel(agentId: string, deskRunId: string): Promise<void> {
    const resp = await this.fetchFn(
      joinUrl(this.options.endpoint, `/agents/${encodeURIComponent(agentId)}/runs/${encodeURIComponent(deskRunId)}/cancel`),
      { method: "POST", headers: await this.headers() },
    );
    if (!resp.ok && resp.status !== 404) throw await readError(resp);
  }
}

export function newId(prefix: string): string {
  const rnd = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36).slice(2);
  return `${prefix}_${rnd}`;
}
