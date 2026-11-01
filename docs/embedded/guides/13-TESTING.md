# 13 — Testing your integration

Against a development Desk server:

1. Register a development integration with `http://localhost:<port>` as an allowed origin and the capabilities you
   need. Keep development and production integrations separate.
2. Point the workspace at a test model (Desk accepts any OpenAI-compatible endpoint) so tests do not spend tokens.
3. Mint a session from your backend and run a turn with `curl`:

```
curl -N https://desk.dev.example/api/embedded/agents/finance-copilot/run \
  -H "authorization: Bearer $OES" -H "origin: http://localhost:5173" -H "content-type: application/json" \
  -d '{"threadId":"t1","runId":"r1","messages":[{"id":"m1","role":"user","content":"List overdue invoices"}]}'
```

Expect the sequence in [04](04-AG-UI-PROTOCOL.md). Negative cases worth automating on your side: wrong origin (403),
expired token (401), two parallel turns on one thread (409), a 9 000-character message (422).

What the Desk repository tests for you (`engine/tests/test_embedded_*.py`, 53 tests):

* authentication, origins, rate limits, registration, audit of every refusal;
* two synthetic organisations with no cross-tenant visibility; ignored tenant/role fields; per-subject threads;
* approvals as interrupts; service user cannot approve; expiry, replay and argument-change detection;
* AG-UI sequence validity, component validation, disconnect cancels, re-attach replay;
* read-only scope, payment refusal, injection cannot reach outbound tools, bank identifiers masked, golden figures;
* usage rows, daily limits, entitlement hook.

Browser: `python e2e/embedded_browser.py` (Playwright) runs a partner page against a real engine process.
SDK: `npm test` in `sdk/embedded-agent`.
