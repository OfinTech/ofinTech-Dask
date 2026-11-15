# Security policy

## Reporting a vulnerability

Please **do not open a public issue or post on Discord** about a security problem. Report it privately through GitHub's *Report a
vulnerability* button on the repository's Security tab (private vulnerability reporting). Include what you found,
how to reproduce it, the version (Settings → Account shows it), and whether it affects the desktop app, the Team
Server or both.

What to expect:

- an acknowledgement within 3 working days;
- an assessment and a planned fix date within 10 working days;
- credit in the release notes if you would like it, once a fix is released.

Please give us a reasonable time to release a fix before you publish details, and do not access, change or delete
data that is not yours while testing.

## Supported versions

| Version | Security fixes |
|---|---|
| 1.0.x | Yes |
| < 1.0 | No (pre-release) |

## Scope

In scope: the engine, the web UI, the desktop shell, the Team Server image and its default configuration, and the
documented APIs (including `/mcp`, the Open Accounting Connector and workflow webhooks).

Out of scope: the security of third-party services you connect (model providers, accounting systems, Google, Meta,
S3 stores), findings that need an already compromised computer or administrator account, and missing
hardening that the deployment guide asks the operator to provide (TLS in front of the server, backups, firewalling).

How the product protects data is described in [docs/SECURITY.md](docs/SECURITY.md).

## The audit record

The engineering and security audit of the platform (1.1.0) and the review of the AI agent below produced
findings, reproductions, fixes, verification runs and a register of accepted risks. That record is kept with the
release rather than published, for the same reason as below: a reproduction, and a list of what is still open,
is as useful to an attacker as to a reviewer. It is shared in full with a customer or an assessor under an
agreement — ask through the address in this policy. What the product does to protect data is described in
[docs/SECURITY.md](docs/SECURITY.md), and what is not claimed is listed in the README.

## Review of the AI agent, November 2026

The agent loop, its tools, the instructions it receives and the data it can reach were reviewed in November
2026, with reproducible evaluations and a re-run of the agent's own security suites. Twenty-three findings were
raised.

- **Every HIGH finding is closed and re-verified.** A skill pinned to a conversation can no longer reach the
  model's instructions while it is disabled or waiting for an admin; an editor's change to an approved skill
  now waits for approval again; and an incomplete answer from a model is no longer stored as a complete one.
- **Names that come from imported data — file names, connection labels, the workspace name — are no longer
  placed among the system instructions.** They are passed as data, marked as data.
- **One thing was not measured and is not claimed:** how far a given model provider's model can be talked into
  something. What was measured is what the engine allows, which is what the engine can be held to: every write
  needs an approval, and no tool can move money.

The evidence — findings, reproductions, fixes and verification — is kept with the release record rather than
published, because a reproduction is as useful to an attacker as to a reviewer. It is shared in full with a
customer under an agreement; ask through the address in this policy.
