# Landing page

The public website for OfinTech Desk (`ofintech.ai`): static files in `site/`, served by nginx in Docker.

```bash
cd landing
docker compose up -d --build          # http://127.0.0.1:8080
```

## HTTPS

**Same server as the Team Server (`desk.ofintech.ai`).** Caddy already holds ports 80 and 443, so let it serve the
landing page too.

1. Start the landing container on the Team Server's network:

   ```bash
   cd landing
   docker compose -f docker-compose.yml -f docker-compose.desk.yml up -d --build
   ```

2. Add to the end of `server/Caddyfile`:

   ```
   ofintech.ai, www.ofintech.ai {
   	encode zstd gzip
   	reverse_proxy ofintech-landing:8080
   }
   ```

3. Reload Caddy: `cd ../server && docker compose up -d && docker compose restart caddy`.

**A server of its own.**

```bash
LANDING_DOMAIN=ofintech.ai ACME_EMAIL=it@ofintech.ai \
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```

Caddy gets the certificates for `ofintech.ai` and `www.ofintech.ai`. DNS A records for both must point at the server,
with ports 80 and 443 open.

## Updating

Replace the files in `site/` and run `docker compose up -d --build` again.
