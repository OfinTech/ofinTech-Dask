# 14 — Operating the gateway (Desk administrators)

* **Enable**: nothing to configure; the endpoints exist on every Team Server. Integrations are created per workspace
  by an admin with a fresh sign-in. The feature is unavailable on sealed workspaces and on `DESK_SEALED` servers.
* **Review**: `GET /api/w/{ws}/embedded/integrations` lists integrations (name, origins, capabilities, limits, last
  use). Each has a service user named `Integration: <name>` in the members list, always a viewer; the members API
  refuses to promote it.
* **Watch**: audit actions `embedded.integration.*`, `embedded.session.issue`, `embedded.run.start`,
  `embedded.run.finish`, `embedded.run.cancel`, `embedded.denied`, `embedded.input.ignored_fields`. They flow to
  outgoing webhooks like every other audit event, so an external SIEM can alert on `embedded.denied` bursts.
* **Usage**: `GET …/integrations/{id}/usage?days=30`.
* **Respond**: suspend (`PATCH status=suspended`), rotate the key, revoke sessions individually, or delete the
  integration. All take effect at once for new requests; open streams end at the next event or keep-alive.
* **Limits**: `PATCH …/integrations/{id} {"limits": {...}}`; see guide 10.
* **Database**: migration 21 adds `embedded_integrations`, `embedded_sessions`, `embedded_threads`,
  `embedded_usage`. Backups include them (the backup tool's schema version check is 21).
* **Capacity**: the engine is a single process (accepted risk AR-1). Per-integration limits are the protection for
  other tenants; there is no metrics endpoint yet (OP-2). Keep `runs_per_minute` conservative for new partners.
* **Not provided**: billing, a UI page for integrations (API only in this release), multi-replica operation,
  compliance certification.
