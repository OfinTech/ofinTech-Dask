# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately through GitHub's *Report a
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
PipesHub, S3 stores), findings that need an already compromised computer or administrator account, and missing
hardening that the deployment guide asks the operator to provide (TLS in front of the server, backups, firewalling).

How the product protects data is described in [docs/SECURITY.md](docs/SECURITY.md).
