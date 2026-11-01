# 08 — Approvals and interrupts

Desk pauses an agent before any action that changes something and asks a workspace **editor** to approve the exact
arguments. The gateway keeps that engine and maps a pause to AG-UI's standard interrupt outcome:

```
RUN_FINISHED {outcome: {type: "interrupt", interrupts: [{id, reason: "tool_call", toolCallId, message, expiresAt,
                                                         responseSchema, metadata: {ofintech: {version: 1, tool,
                                                         arguments, decided_in: "desk", approval_url}}}]}}
```

**In this release every embedded agent is read-only, so no run pauses.** The mapping exists so a future
write-capable agent needs no protocol change, and it is covered by tests.

Why the partner app cannot approve: the gateway acts in Desk as the integration's service user, a **viewer**.
Deciding an approval needs the editor role in the engine (`approvals.decide`), so a `resume` item with
`status: "resolved"` is refused by the engine, audited as `approval.denied`, and surfaces as
`RUN_ERROR code=approval_requires_desk_editor` with the Desk URL in `metadata.ofintech.approval_url`. This is by
construction, not configuration: there is no setting that lets a partner application approve actions.

What the partner app can do:

* Show the `ApprovalRequestCard` with the link to Desk.
* Send `resume: [{interruptId, status: "cancelled"}]` to withdraw the request (the waiting run stops, its approvals
  expire) and continue the conversation.
* After a Desk editor decides, re-attach to the run (`GET …/runs/{desk_run_id}/events`) to receive the tool result
  and the final answer, or start a new turn.

Replay and tamper protection come from the engine: approvals expire, carry a digest of workspace + requester + tool
+ arguments, and are consumed atomically once. `engine/tests/test_embedded_approvals.py` exercises all of this through
the gateway with a write-capable agent that exists only in the test.
