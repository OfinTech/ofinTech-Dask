# Website

The public website for OfinTech Desk at **`ofintech.ai`** (and `www.ofintech.ai`). The product itself runs at
**`desk.ofintech.ai`** (the Team Server); the website's *Sign in* buttons link there. It is static files in `site/`, served by nginx in Docker, and a
push to `main` that changes `landing/` can redeploy it automatically through a GitHub webhook.

```
site/index.html            home page: the product, install guide, connecting a ledger
site/features/index.html   features: financial analysis, forecasting, cost centres, the workspace, the demo company
site/assets/               logo, icons, product screenshots (the demo company, examples/demo-company)
deploy/                    the deploy webhook: webhook.py, deploy.sh, its Dockerfile, settings example
docker-compose*.yml        the site alone, on the Team Server's network, with its own HTTPS, the webhook
```

The pages have no JavaScript, and nginx's content security policy blocks scripts (`script-src 'none'`).

## Preview

```bash
cd landing/site && python3 -m http.server 8000     # http://localhost:8000 and /features/
cd landing && docker compose up -d --build           # http://127.0.0.1:8080
```

## HTTPS

### On the same server as the Team Server (`desk.ofintech.ai`)

Caddy already holds ports 80 and 443, so let it serve the website too.

1. **Start the website on the Team Server's network:**

   ```bash
   cd landing
   docker compose -f docker-compose.yml -f docker-compose.desk.yml up -d --build
   ```

2. **Add to the end of `server/Caddyfile`.** If it already has an `ofintech.ai` block, replace it. The
   `/hooks/deploy` route is only needed with automatic deploys.

   ```
   ofintech.ai, www.ofintech.ai {
   	encode zstd gzip
   	handle /hooks/deploy* {
   		reverse_proxy ofintech-landing-hook:9000
   	}
   	handle {
   		reverse_proxy ofintech-landing:8080
   	}
   }
   ```

3. **Reload Caddy:** `cd ../server && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.

### On a server of its own

```bash
LANDING_DOMAIN=ofintech.ai ACME_EMAIL=it@ofintech.ai \
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```

Caddy gets the certificates for `ofintech.ai` and `www.ofintech.ai`. DNS A records for both must point at the
server, with ports 80 and 443 open. The `Caddyfile` here already routes `/hooks/deploy` to the webhook.

## Automatic deploys (GitHub webhook)

On every push, GitHub calls `https://ofintech.ai/hooks/deploy`. The webhook (`deploy/webhook.py`, Python standard
library):

1. **checks the signature:** `X-Hub-Signature-256`, an HMAC-SHA256 of the body with your secret, compared in
   constant time. Anything unsigned or wrongly signed gets 401.
2. **acts only on `main` of this repository**, and only when the push changes files under `landing/`. Changes to the
   product, other branches and other events are acknowledged and ignored. `ping` answers 200.
3. **runs `deploy/deploy.sh`:** it pulls `main` and rebuilds only the website container. One deployment runs at a
   time, and pushes during a run are folded into one more run.

**Security.** The webhook runs in its own container with no published port; Caddy reaches it over the Docker
network. It mounts the host's Docker socket to rebuild the website, so whoever controls that container controls
Docker on the host. Keep the secret long and private.

### Set it up

1. **Make a checkout used only for the website.** `deploy.sh` resets its checkout to `main`, which would throw away
   local edits such as the Team Server's `Caddyfile`. It refuses to run in a checkout that has `server/.env`.

   ```bash
   sudo git clone --depth 1 https://github.com/OfinTech/ofinTech-Dask.git /opt/ofintech-site
   cd /opt/ofintech-site/landing
   ```

2. **Write the settings.** Never commit `deploy/webhook.env`; it is in `.gitignore`.

   ```bash
   cp deploy/webhook.env.example deploy/webhook.env
   python3 -c "import secrets; print(secrets.token_hex(32))"   # paste as WEBHOOK_SECRET
   nano deploy/webhook.env                                     # REPO_DIR=/opt/ofintech-site
   ```

3. **Start the website with the webhook.** If the website already runs from another folder, stop it there first,
   because both use the project name `ofintech-landing`.

   ```bash
   REPO_DIR=/opt/ofintech-site docker compose -f docker-compose.yml -f docker-compose.desk.yml \
     -f docker-compose.webhook.yml up -d --build
   ```

4. **Add the `/hooks/deploy` route to Caddy** (see above).

5. **Add the webhook on GitHub:** repository → **Settings → Webhooks → Add webhook**.
   - **Payload URL:** `https://ofintech.ai/hooks/deploy`
   - **Content type:** `application/json`
   - **Secret:** the same value as `WEBHOOK_SECRET`
   - **Events:** *Just the push event*

   GitHub sends a `ping` at once. Under *Recent Deliveries* it should show **200**.

6. **Watch a deploy.** Push a change under `landing/` and run:

   ```bash
   docker compose -p ofintech-landing logs -f hook
   ```

### Deploy by hand

```bash
REPO_DIR=/opt/ofintech-site COMPOSE_FILES="docker-compose.yml docker-compose.desk.yml" landing/deploy/deploy.sh
```

## Editing

- **Look.** Plain HTML with inline styles, in the OFINTECH design system: monochrome, system fonts, and corners
  rounded on the top-right and bottom-left only.
- **Links.** The home page uses relative links; pages in sub-folders use absolute ones (`/assets/…`).
- **Images.** Keep them under 200 KB each: PNG with 256 colours suits screenshots.
- **Facts.** Product facts must match the README and docs.
