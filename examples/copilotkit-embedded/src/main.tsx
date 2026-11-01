import { createRoot } from "react-dom/client";
import { CopilotKit, useCopilotAction } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { HttpAgent } from "@ag-ui/client";
import { OfinTechAgentProvider, OfinTechFinancialCopilot, RenderComponent, acceptComponent } from "@ofintech/embedded-agent";
import "@ofintech/embedded-agent/styles.css";

const DESK = "https://desk.example.com";   // same value as DESK_URL on the server

// 1. Session tokens come from *your* backend; the browser never sees the integration key.
let cached: { token: string; expires: number } | null = null;
async function tokenProvider(): Promise<string> {
  if (cached && cached.expires - Date.now() > 60_000) return cached.token;
  const r = await fetch("/api/ofintech/session", { method: "POST" });
  if (!r.ok) throw new Error("could not start a financial-copilot session");
  const body = (await r.json()) as { token: string; expires_at: string };
  cached = { token: body.token, expires: Date.parse(body.expires_at) };
  return cached.token;
}

// 2a. The SDK's own widget — no CopilotKit needed.
function SdkWidget() {
  return (
    <OfinTechAgentProvider endpoint={`${DESK}/api/embedded`} tokenProvider={tokenProvider} locale="ar">
      <OfinTechFinancialCopilot context={[{ description: "screen", value: "Customer statement for ACME-001, period 2026-Q3" }]} />
    </OfinTechAgentProvider>
  );
}

// 2b. The same gateway through CopilotKit: an AG-UI HttpAgent with the session token in the Authorization header.
//     CopilotKit's `HttpAgent` sends RunAgentInput and consumes the SSE event stream; generative-UI components arrive
//     as CUSTOM events named `ofintech.component` and are rendered through the SDK's registry (validated first).
const agent = new HttpAgent({
  url: `${DESK}/api/embedded/agents/finance-copilot/run`,
  // headers are resolved per request; HttpAgent accepts a static object, so refresh before each run
  headers: { authorization: "" },
});
async function refreshAgentToken() { agent.headers = { authorization: `Bearer ${await tokenProvider()}` }; }

function CopilotKitWidget() {
  // Render the gateway's components when CopilotKit surfaces CUSTOM events (API shape may vary by CopilotKit version;
  // see the CopilotKit docs for the generative-UI hook of your release).
  useCopilotAction({
    name: "ofintech.component",
    available: "frontend",
    render: ({ args }) => {
      const payload = acceptComponent(args);
      return payload ? <RenderComponent payload={payload} locale="ar" /> : <></>;
    },
  } as never);
  return <CopilotChat labels={{ title: "المساعد المالي", initial: "اسأل عن الإيرادات أو الفواتير أو النقد." }} />;
}

function App() {
  return (
    <main style={{ display: "grid", gap: 24, gridTemplateColumns: "1fr 1fr", padding: 24 }}>
      <section><h1>SDK widget</h1><SdkWidget /></section>
      <section>
        <h1>CopilotKit</h1>
        {/* telemetry off: nothing about this page leaves the partner's domain except the gateway calls */}
        <CopilotKit agent="finance-copilot" agents={{ "finance-copilot": agent }} showDevConsole={false} properties={{ locale: "ar" }}>
          <CopilotKitWidget />
        </CopilotKit>
      </section>
    </main>
  );
}

void refreshAgentToken().then(() => createRoot(document.getElementById("root")!).render(<App />));
