# Team Server deployment

## Server

| Team size | CPU | RAM | Disk |
|---|---|---|---|
| up to 25 people | 2 vCPU | 4 GB | 40 GB SSD |
| 25–150 people | 4 vCPU | 8 GB | 100 GB SSD |

The engine does not run models itself. It calls the providers you configure. To run models locally (Ollama or
vLLM), size that machine separately; it typically needs a GPU.

Requirements: Docker Engine 24+ with Compose v2, a DNS record for your domain, and ports 80 and 443 open.

### Domains (OfinTech's own installation)

| Address | What it serves | Where it is configured |
|---|---|---|
| `desk.ofintech.ai` | the product: the Team Server (web app, API, OAuth callbacks), and what the desktop client opens | `DESK_DOMAIN=desk.ofintech.ai` in `server/.env` |
| `ofintech.ai`, `www.ofintech.ai` | the website: one page with the product, who it is for, the install guide, and the **sign-in and create-account forms**, which post to `desk.ofintech.ai` | the `ofintech.ai` block in `server/Caddyfile`, see `landing/README.md`; `DESK_SITE_URL=https://ofintech.ai` in `server/.env` |

Both can run on one server: the Team Server's Caddy holds ports 80 and 443 and serves the two names. Point DNS
A records for `desk.ofintech.ai`, `ofintech.ai` and `www.ofintech.ai` at that server. OAuth apps (Google,
QuickBooks, Xero, Zoho) use `https://desk.ofintech.ai/oauth/callback`, never the website's address.

### Sign-in from the website (`DESK_SITE_URL`)

With `DESK_SITE_URL` set to the website's origin (`https://ofintech.ai`), the Team Server accepts three plain
requests from it, so that people sign in and create accounts on the website without any script there:

| Request | From the website | Result |
|---|---|---|
| `POST /auth/form/sign-in` (`email`, `password`) | the *Sign in* form | the session cookie is set and the browser is sent to the app (`303`) |
| `POST /auth/form/sign-up` (`name`, `email`, `password`) | the *Create account* form | an account is created (the first one administers the server; later ones only with `DESK_OPEN_SIGNUP=true`, otherwise by invitation) and signed in |
| `GET /auth/form/google` | the *Continue with Google* link | Google sign-in starts, as from the app |

The `Origin` header (or the `Referer`) must be the website or the app, or the request is refused with `403`. On a
failure the browser is sent back to `DESK_SITE_URL/#<code>`, and the website shows the matching message:
`signin-error`, `signin-rate`, `signin-disabled`, `signup-invalid`, `signup-password`, `signup-exists`,
`signup-invitation`, `signup-rate`, `google-off` and `desktop`. The same rate limits and password policy apply as
in the app, and the session cookie is the app's (HttpOnly, Secure, SameSite=strict), which reaches the app because
the website and the app share the registrable domain. Without `DESK_SITE_URL`, failures go to the app's own
sign-in page and only the app's origin is accepted. The website's `form-action` content-security policy must name
the Team Server (`landing/nginx.conf`).

## Install

```bash
git clone <repository> ofintech-desk && cd ofintech-desk/server
cp .env.example .env
# the master key first (Compose checks it on every command, including build):
sed -i "s|^DESK_MASTER_KEY=.*|DESK_MASTER_KEY=$(python3 -c 'import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')|" .env
nano .env                      # DESK_DOMAIN, ACME_EMAIL
docker compose up -d --build
docker compose logs -f engine  # "Uvicorn running on http://0.0.0.0:8700"
```

Open `https://<DESK_DOMAIN>`, create the administrator account, then create the first workspace. Invite colleagues
under **Settings → Members**: each invitation is a single-use link that is valid for 7 days.

## Sign-in and e-mail

**E-mail and password** is always available; passwords need at least 12 characters with upper- and lower-case
letters and a digit. With SMTP set (`DESK_SMTP_HOST`, `DESK_SMTP_PORT`, `DESK_SMTP_SECURITY`, `DESK_SMTP_USER`,
`DESK_SMTP_PASSWORD`, `DESK_SMTP_FROM`), the server e-mails:

- invitations (people who already have an account are added directly and told by e-mail);
- **password reset links**: *Forgot your password?* on the sign-in page; a single-use link valid for one hour that
  signs the person out everywhere when used. The page always gives the same answer, so it does not reveal which
  addresses have accounts, and it is rate-limited.

Without SMTP, invitations are links the administrator passes on, and an administrator can create a reset link for a
member under Settings → Members.

**Continue with Google** appears when the Google OAuth app is set (in `.env` or Connections → OAuth apps). It uses
the same redirect URI (`https://<DESK_DOMAIN>/oauth/callback`) with PKCE and accepts only addresses Google has
verified. A Google account signs in to an existing account with the same e-mail, or joins through an invitation; it
never creates an account on an invitation-only server. Set `DESK_GOOGLE_ALLOWED_DOMAINS=example.com,example.sa` to
accept only your company's domains.

## OAuth apps

Register each provider's app with the redirect URI `https://<DESK_DOMAIN>/oauth/callback`. Then put the credentials
in `.env` (`DESK_QUICKBOOKS_CLIENT_ID`, …) or enter them in the app under **Connections → OAuth apps**. See
[CONNECTORS.md](CONNECTORS.md).

## File storage: MinIO or any S3-compatible store

By default, files are kept on the engine's data volume. This covers documents, spreadsheet imports and generated
workbooks and reports. To keep them in MinIO instead, as in the earlier OfinTech stack, fill in `MINIO_ROOT_USER`,
`MINIO_ROOT_PASSWORD`, `DESK_S3_ACCESS_KEY` and `DESK_S3_SECRET_KEY` in `.env`. The four values must all be
different. Then start the stack with the MinIO overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.minio.yml up -d
# files that were already on the engine volume:
docker compose -f docker-compose.yml -f docker-compose.minio.yml exec engine python -m desk_engine storage-migrate
```

The overlay works as follows:

- It runs MinIO from PGSTY Silo. MinIO, Inc. no longer publishes community images, and Silo is a maintained build of
  the same AGPLv3 code.
- The one-shot `minio-init` container creates a private, versioned bucket. Overwritten or deleted objects are kept
  for 90 days.
- `minio-init` also creates a user that can read, write, list and delete only in that bucket. The engine uses that
  user. It never uses the root credentials.
- The MinIO console is bound to `127.0.0.1:9001`. Reach it with `ssh -L 9001:127.0.0.1:9001 <server>`.

To use MinIO AIStor (commercial, licence file required), add `-f docker-compose.aistor.yml` as a third file.
Set `AISTOR_IMAGE` to a pinned tag and `AISTOR_LICENSE_FILE` to the absolute path of the licence file.

Any other S3-compatible store works without an overlay, for example AWS S3, Cloudflare R2, Wasabi, SeaweedFS, Garage
or Ceph RGW. Set `DESK_STORAGE=s3` and the `DESK_S3_*` variables.

Whatever the backend, the engine encrypts every object with AES-256-GCM before uploading it. The key is derived from
`DESK_MASTER_KEY`, so a copy of the bucket is unreadable without that key. Back up the bucket together with the
database, and keep the master key separately.

## Backups

```bash
./backup.sh            # consistent online copy -> backups/<timestamp>.sqlite3 + checksum
```

Copy backups off the server, encrypted. Store `DESK_MASTER_KEY` separately: without it, the stored credentials in a
backup cannot be decrypted. Datasets, conversations and dashboards can still be read. To restore, stop the engine,
put the file in the `desk_data` volume as `desk.sqlite3`, and start it again with the same master key.

## Upgrades

Take a backup, pull the new version and run `docker compose up -d --build`. Database migrations run automatically
at start-up and are only ever added, never rewritten; read `CHANGELOG.md` for anything to do by hand.

## Production checklist

- [ ] `DESK_MASTER_KEY` generated with `keygen`, stored in a password manager or secrets store **separately from
      backups** (it can also be given as a Docker secret with `DESK_MASTER_KEY_FILE`).
- [ ] `DESK_DOMAIN` points at the server; Caddy obtained a certificate (`docker compose logs caddy`). The engine
      refuses to start with an `http://` public address or with `DESK_DEV` set.
- [ ] `.env` readable only by root (`chmod 600 .env`) and never committed.
- [ ] Sign-up stays invitation-only (`DESK_OPEN_SIGNUP=false`); the first account is created by you straight after
      the first start. There are no default or demo accounts.
- [ ] SMTP set, so invitations and password resets reach people; Google sign-in limited to your domains if used.
- [ ] Model providers added with keys that have spending limits at the provider; a cheaper *model for short jobs*
      chosen under Settings → Models.
- [ ] OAuth apps registered for the systems you use; tested against a sandbox company first.
- [ ] File storage decided (data volume or S3/MinIO with engine-side encryption on).
- [ ] `backup.sh` scheduled (cron) and a restore tested on another machine; bucket versioning on if you use S3.
- [ ] Monitoring: the container health check (`/health`), disk space of the `desk_data` volume, and the audit log
      (Settings → Audit log) reviewed by an administrator.
- [ ] Firewall: only ports 80 and 443 open; the MinIO console stays on `127.0.0.1`.
- [ ] Your own review of the legal points in [LICENSES.md](../LICENSES.md) and of any regulation that applies to the
      data you process; the product claims no certification or compliance.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `DESK_PUBLIC_URL` | (from `DESK_DOMAIN`) | Public HTTPS address; used for OAuth redirects, webhooks, invitations and the CSRF check. Must start with `https://` (`DESK_ALLOW_HTTP=true` only for a private test install) |
| `DESK_MASTER_KEY` | required | 32 bytes, base64; encrypts stored credentials |
| `DESK_OPEN_SIGNUP` | `false` | Allow sign-up without an invitation |
| `DESK_ALLOW_PRIVATE_URLS` | `false` | Allow model or MCP endpoints on private networks (for example a self-hosted vLLM in the same VPC) |
| `DESK_ALLOW_STDIO_MCP` | `false` | Allow local-command MCP servers on the server (not recommended) |
| `DESK_MAX_UPLOAD_MB` | `25` | Import size limit |
| `DESK_SESSION_IDLE_MINUTES` | `60` | Sign out after this long with no activity; `0` never does |
| `DESK_SESSION_DAYS` | `0` | Absolute cap on a session in days, whatever the person does; `0` = none |
| `DESK_WHATSAPP_GRAPH_VERSION` | `v26.0` | Meta Graph API version |
| `DESK_GOOGLE_ALLOWED_DOMAINS` | — | Comma-separated e-mail domains allowed to use Google sign-in |
| `DESK_SMTP_HOST`, `DESK_SMTP_PORT`, `DESK_SMTP_SECURITY`, `DESK_SMTP_USER`, `DESK_SMTP_PASSWORD`, `DESK_SMTP_FROM` | — / `587` / `starttls` | Outgoing e-mail for invitations and password resets (`*_FILE` variants read Docker secrets) |
| `DESK_OCR` | on when Tesseract is installed | `off` disables OCR of scans and images |
| `DESK_OCR_LANGS` | `eng+ara+spa+fra` | Tesseract languages to use (those installed) |
| `DESK_TOOL_SELECTION` | `on` | `off` sends every tool definition with every request (see COSTS.md) |
| `DESK_LOG_LEVEL` | `INFO` | Engine log level |
| `DESK_STORAGE` | `local` | `local` (data volume) or `s3` |
| `DESK_STORAGE_ENCRYPTION` | `true` | Encrypt files in the engine before storing them |
| `DESK_S3_ENDPOINT` | AWS in the region | e.g. `http://minio:9000`, `https://<account>.r2.cloudflarestorage.com` |
| `DESK_S3_REGION` | `us-east-1` | Signing region |
| `DESK_S3_BUCKET`, `DESK_S3_PREFIX` | — | Bucket and optional key prefix |
| `DESK_S3_ACCESS_KEY`, `DESK_S3_SECRET_KEY` | — | Credentials (`*_FILE` variants read Docker secrets) |
| `DESK_S3_PATH_STYLE` | `true` with an endpoint | Path-style addressing (MinIO); `false` for virtual-hosted buckets |
| `DESK_S3_CREATE_BUCKET` | `false` | Create the bucket on start if it is missing |
| `DESK_S3_SSE` | — | Server-side encryption header, e.g. `AES256` |
