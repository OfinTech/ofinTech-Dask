# 09 — Security requirements for partners

The gateway's threat model is in [`../SECURITY-THREAT-MODEL.md`](../SECURITY-THREAT-MODEL.md). These are the
obligations on the partner's side; the gateway cannot enforce them for you.

1. **Keep the integration key on the server.** Never in a web page, mobile app, repository or log. Rotate it from
   Desk if it may have leaked; rotation closes every session at once.
2. **Mint sessions only for authenticated users of your application**, with a stable per-user `subject`. One subject
   per person; never a shared or guessable value.
3. **Protect your page against XSS.** A session token in a compromised page is a 15-minute read grant on the
   organisation's financial answers. Use a Content-Security-Policy; the example sets
   `connect-src 'self' <desk host>`.
4. **Do not frame the copilot in third-party pages.** Serve the embedding page with `X-Frame-Options: DENY` or a
   `frame-ancestors` directive; Desk itself refuses to be framed.
5. **Forward only what the agent needs in `context`.** It is treated as untrusted page content, but it still reaches
   the organisation's model. Do not forward other users' data.
6. **Handle the answer as financial data.** It is subject to the same confidentiality as the organisation's books;
   do not log, cache or forward it to third-party analytics.
7. **Respect rate limits** (10 — limits). Do not retry 429 or 403 in a loop.
8. **Register exact origins.** `https://app.example.com`, not wildcards; development origins only on development
   integrations.
9. **Do not build a workaround for approvals.** If your users need to act, deep-link them into Desk.
10. **Report incidents** to the organisation's Desk administrator, who can suspend the integration immediately.

Shared responsibility summary: Desk guarantees tenant binding, read-only scope, origin checks, token lifetime, audit
and masking of bank identifiers; the partner guarantees who gets a session, where the page runs and what happens to
the answer.
