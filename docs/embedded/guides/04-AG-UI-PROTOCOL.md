# 04 — AG-UI protocol

The gateway is an AG-UI server. The full mapping, with the event names verified against the specification, is in
[`../AG-UI-PROTOCOL-MAPPING.md`](../AG-UI-PROTOCOL-MAPPING.md). The short version:

```
POST /api/embedded/agents/{agent_id}/run
Authorization: Bearer oes_…      Origin: https://erp.partner.example
Accept: text/event-stream        Content-Type: application/json

{"threadId": "conv-42", "runId": "r-1",
 "messages": [{"id": "m1", "role": "user", "content": "What is this month's gross margin?"}],
 "state": {"screen": "customer", "customer": "ACME-001"},
 "context": [{"description": "screen", "value": "Customer ACME-001 statement, Q3 2026"}],
 "forwardedProps": {"locale": "ar"}}
```

```
data: {"type":"RUN_STARTED","threadId":"conv-42","runId":"r-1","metadata":{"ofintech":{"version":1,"desk_run_id":"run_…","agent_id":"finance-copilot"}}}
data: {"type":"STATE_SNAPSHOT","snapshot":{"screen":"customer","customer":"ACME-001"}}
data: {"type":"STEP_STARTED","stepName":"model"}
data: {"type":"TEXT_MESSAGE_START","messageId":"m_…","role":"assistant"}
data: {"type":"TOOL_CALL_START","toolCallId":"call_1","toolCallName":"financial_kpis","parentMessageId":"m_…"}
data: {"type":"TOOL_CALL_ARGS","toolCallId":"call_1","delta":"{\"period\":\"2026-10\"}"}
data: {"type":"TOOL_CALL_END","toolCallId":"call_1"}
data: {"type":"TEXT_MESSAGE_END","messageId":"m_…"}
data: {"type":"STEP_FINISHED","stepName":"model"}
data: {"type":"TOOL_CALL_RESULT","messageId":"m_…","toolCallId":"call_1","content":"Gross margin …","role":"tool"}
data: {"type":"CUSTOM","name":"ofintech.component","value":{"version":1,"toolCallId":"call_1","component":"FinancialKpiCard","props":{…}}}
data: {"type":"STEP_STARTED","stepName":"model"}
data: {"type":"TEXT_MESSAGE_START","messageId":"m_2","role":"assistant"}
data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"m_2","delta":"Gross margin for October is "}
…
data: {"type":"TEXT_MESSAGE_END","messageId":"m_2"}
data: {"type":"STEP_FINISHED","stepName":"model"}
data: {"type":"RUN_FINISHED","threadId":"conv-42","runId":"r-1","outcome":{"type":"success"},"result":{"text":"…"},"usage":{"input_tokens":1200,"output_tokens":180}}
```

Rules of thumb

* Keep `threadId` stable for one conversation and send a fresh `runId` per turn. Only the **last** `user` message is
  new input; the gateway keeps the history.
* Treat `context` as a place for screen facts, not instructions: the model sees it labelled as page content.
* `tools` is accepted and ignored (no client-side tools in this release).
* Close the stream to cancel. Re-attach with `GET …/runs/{desk_run_id}/events?threadId=…&runId=…` after a drop.
* Expect `: keep-alive` comment frames every 15 seconds.
* Errors before the run starts are plain JSON with a status code; after it starts they are `RUN_ERROR` frames.
* `outcome.type == "interrupt"` can only occur with a write-capable agent, which this release does not ship; see
  [08 — Approvals](08-APPROVALS.md).
