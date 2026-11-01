# 03 — Multi-tenant setup

One integration = one Desk workspace = one partner application origin set. A partner that serves several
organisations therefore holds **one integration key per organisation**, each registered by that organisation's Desk
admin in that organisation's workspace.

```
Partner backend
 ├─ tenant A  →  oei_A  →  Desk workspace "A Ltd"   (service user: Integration: Partner ERP, viewer)
 └─ tenant B  →  oei_B  →  Desk workspace "B LLC"   (service user: Integration: Partner ERP, viewer)
```

Rules the gateway enforces, so you do not have to:

* The workspace comes from the key, never from the request. Sending `workspace_id`, `tenant`, `user_id`, `role` or
  similar in `forwardedProps`, `state` or `context` has no effect and is audited (`embedded.input.ignored_fields`).
* Conversations are keyed by `(integration, subject hash, threadId)`. The same `threadId` string at two tenants, or
  for two users, is two conversations. A Desk thread id of another tenant used as `threadId` simply names a new
  conversation of your own.
* Run ids (`desk_run_id`) can be re-attached to or cancelled only by a session of the same integration **and** subject.
* Sealing a workspace in Desk (`egress_policy = sealed`) switches its integrations off immediately.

What you must do on your side:

* Map your tenant → key correctly before minting a session; the gateway cannot detect a key used for the wrong
  tenant (that would be two of your own tenants sharing an organisation's data).
* Use a stable, non-guessable `subject` per end user; do not reuse one subject for many users (they would share
  conversations).
* Keep each organisation's key in a separate secret; rotate per organisation.

Verification: `engine/tests/test_embedded_tenant.py` runs two synthetic organisations through the gateway and checks
that neither sees the other's datasets, threads, runs or usage.
