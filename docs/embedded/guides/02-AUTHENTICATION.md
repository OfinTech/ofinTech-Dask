# 02 — Authentication and tokens

Two credentials, two audiences:

| Credential | Prefix | Holder | Lifetime | Used for |
|---|---|---|---|---|
| Integration key | `oei_` | your **backend** | until rotated or revoked | `POST /api/embedded/sessions` only |
| Session token | `oes_` | your **web page**, one end user | 15 min default, 1–60 min | `/api/embedded/agents/...` |

Both are 256-bit random values; Desk stores only their SHA-256. The key is shown once at registration and once at
rotation.

## Session issuance

`subject` is the end user's identifier **in your system** (an opaque string, ≤ 200 characters). Desk stores a salted
hash of it, never the value; it isolates conversations per user (`threadId` is scoped to the subject) and appears as
a hash prefix in the audit log. `label` (optional, ≤ 80 characters) is a display name you may pass for your own
operators to recognise in Desk's session list; leave it out if you do not want names in Desk.

## Using the session token

Every browser call must carry `Authorization: Bearer oes_…` **and** an `Origin` header that is on the integration's
allow-list (the browser adds `Origin` on cross-site `fetch` automatically). A request without `Origin`, or with one
not on the list, is refused with 403 even when the token is valid. CORS headers are emitted only for the matching
origin; there is no wildcard.

Refresh: mint a new token before `expires_at` (the SDK's `tokenProvider` is called per request, so cache it on your
side and refresh a minute early). A 401 on any call means the token is unknown, expired or revoked — the response
does not say which; mint a new one.

## Rotation, suspension, revocation (in Desk)

* `POST …/integrations/{id}/rotate` — new key; **all** sessions of the integration are closed.
* `PATCH …/integrations/{id} {"status": "suspended"}` — sessions are closed, no new ones are issued, the key stays.
* `DELETE …/integrations/{id}` — key, sessions and the service user's membership are removed.

What the token can **not** do: it is not a Desk session. `/api/w/...`, `/api/session/...`, `/mcp` and every other
Desk route answer 401 to it. Nothing in a request body can change which workspace or which user a token stands for.
