# Website

The public website for OfinTech Desk (`ofintech.ai`): one page, static files in `site/`, served by nginx in Docker.
Signing in and creating an account happen on this page; the forms post to the product at `desk.ofintech.ai`.

```
site/index.html      the page: who it is for, features, latest changes, demo company, sign in / create account,
                     install guide, connecting a ledger
site/assets/         logo, icons, product screenshots (from the demo company and the UI smoke test)
nginx.conf           headers, caching, the /features/ → /#features redirect
docker-compose*.yml  the site alone; on the Team Server's network; with its own HTTPS
```

The page has no JavaScript: nginx's content security policy blocks scripts (`script-src 'none'`) and allows forms to
be sent only to `https://desk.ofintech.ai` (`form-action`).

## Sign in and create account

The two forms post to `https://desk.ofintech.ai/auth/form/sign-in` and `/auth/form/sign-up`; *Continue with Google*
links to `/auth/form/google`. The Team Server accepts them only when its `DESK_SITE_URL` is `https://ofintech.ai`
(the browser's `Origin` header is checked), sets the session cookie and opens the app. On a failure it sends the person
back to `https://ofintech.ai/#<code>`, and the page shows the matching message with CSS (`:target`), for example
`#signin-error`, `#signup-password` or `#signup-invitation`. The codes are listed in `docs/DEPLOYMENT.md`.

Another installation can use the same page: set its `DESK_SITE_URL` and change the three addresses in `index.html`
and the `form-action` in `nginx.conf`.

## Preview

```bash
cd landing/site && python3 -m http.server 8000     # http://localhost:8000
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

2. **Add to the end of `server/Caddyfile`.** If it already has an `ofintech.ai` block, replace it.

   ```
   ofintech.ai, www.ofintech.ai {
   	encode zstd gzip
   	reverse_proxy ofintech-landing:8080
   }
   ```

3. **Reload Caddy:** `cd ../server && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.

4. **Tell the Team Server about the website:** `DESK_SITE_URL=https://ofintech.ai` in `server/.env`, then
   `docker compose up -d`.

### On a server of its own

```bash
LANDING_DOMAIN=ofintech.ai ACME_EMAIL=it@ofintech.ai \
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```

Caddy gets the certificates for `ofintech.ai` and `www.ofintech.ai`. DNS A records for both must point at the
server, with ports 80 and 443 open.

## Deploying a change

```bash
git pull
cd landing && docker compose -f docker-compose.yml -f docker-compose.desk.yml up -d --build
```

The website has no deploy hook of its own: deploys are a `git pull` by someone with access to the server.

## Editing

- **Look.** Plain HTML with one stylesheet in the `<head>`, in the OfinTech design system (docs/DESIGN.md): Deep
  Navy for dark bands and the footer, Primary Blue for actions, white and cool neutral surfaces, Inter served from
  `site/assets/fonts` (no font CDN: the CSP allows `self` only).
- **Images.** Keep them under 200 KB each: PNG with 256 colours suits screenshots.
- **Facts.** Product facts must match the README and docs. Screenshots come from the demo company
  (`examples/demo-company`) and the UI smoke test (`e2e/ui_smoke.py`).
- **Check.** Open the page at 1440 and 390 pixels wide; nothing should scroll sideways.
