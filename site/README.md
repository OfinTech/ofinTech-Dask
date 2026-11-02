# OfinTech — public website

Four static pages for people deciding whether to use OfinTech Desk, plus the two pages that let them in. No build
step, no framework, no third-party request: HTML, one stylesheet, one small script, and the brand's own fonts.

| File | What it is |
|---|---|
| `index.html` | The home page, written for a finance lead rather than an engineer: what it answers, how you use it, **what it will not do**, and how to get it. |
| `docs.html` | Everything technical, kept off the home page: installing on a server, models and providers, bringing data in, roles and approvals, security, backups, interfaces, and the known gaps. |
| `sign-in.html`, `sign-up.html` | The forms. They post to the engine, not to this site. |
| `styles.css` | The product's own palette and type, so the site and the app read as one thing. |
| `site.js` | Language switch, the engine's address, and the error codes the engine sends back. |
| `404.html` | Shown for an address that is not here, in the same chrome as the rest. Its links are absolute, because a 404 is served at whatever path was asked for. |
| `Dockerfile`, `nginx.conf` | How it is served: nginx as a non-root user on port 8080. Everything in this folder ships except the files that describe how to ship it (`.dockerignore`), so a page added here is served without editing anything. |
| `docker-compose*.yml`, `Caddyfile` | The site alone, on the Team Server's network, or with HTTPS of its own. |

## Language

Arabic is the source of truth in the markup and the default for Arabic browsers; English lives in `data-en`
attributes on the same elements, so there is one file per page rather than two. `?lang=en` makes a choice
shareable, and the choice is remembered. The script swaps `html[lang]` and `html[dir]` together, so the whole page
flips to left-to-right with the text.

To change a line, edit the Arabic in the element and the English in its `data-en`. Both or neither.

## Pointing it at your engine

The forms post to the Team Server. Set the address once, in the page or in `site.js`:

```html
<script>window.OFINTECH_ENGINE = "https://desk.ofintech.ai";</script>
<script src="site.js"></script>
```

On the engine, set `DESK_SITE_URL` to this site's origin. Two things depend on it: the engine accepts the form only
when the `Origin` header matches, and it sends failures back to `{DESK_SITE_URL}/#<code>` — the script reads that
code, moves the person to the page the error belongs to, and shows the message in their language.

## Serving it

Any static host will do. Next to the engine, Caddy can serve both from one machine:

```caddyfile
ofintech.ai, www.ofintech.ai {
    root * /srv/ofintech-site
    file_server
    encode gzip
    header /assets/* Cache-Control "public, max-age=31536000, immutable"
    header Cache-Control "no-cache"
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
        X-Frame-Options DENY
        Content-Security-Policy "default-src 'self'; form-action https://desk.ofintech.ai; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    }
}

desk.ofintech.ai {
    reverse_proxy engine:8700
}
```

`form-action` has to name the engine, because that is where the sign-in and sign-up forms post.

## Checking it

```
python e2e/site_check.py
```

Four pages × two languages × five widths: no horizontal overflow anywhere, the language switch leaves nothing
untranslated, the forms point at the engine and not at the site, every error code the engine can send reaches the
right page with a readable message, and no console errors. It runs in CI.

## Before you publish

- Replace the figures on the home page if you would rather show nothing at all; they are illustrative and the
  footer says so.
- The claims on the page are the ones the product can keep: no payments, nothing outbound without an approval,
  sealed mode, an audit log the application cannot rewrite. Do not add a compliance or certification claim — the
  product has none, and `docs.html` says that plainly.

## Serving it

```bash
cd site
docker compose up -d --build                      # 127.0.0.1:8080, HTTPS in front of it
```

On the same server as the Team Server, so its Caddy can reach this container as `ofintech-site:8080`:

```bash
docker compose -f docker-compose.yml -f docker-compose.desk.yml up -d --build
```

On a server of its own, with its own certificate:

```bash
SITE_DOMAIN=ofintech.ai ACME_EMAIL=it@ofintech.ai \
  docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```

## Addresses that were written down before

The site used to be a single page, where signing in and creating an account were sections of it. Those addresses
are in people's e-mails and bookmarks, so they still arrive somewhere sensible:

| Written down as | Goes to | Where that happens |
|---|---|---|
| `/features`, `/features/` | the home page, at *how it works* | `nginx.conf`, a 301 |
| `/#signin`, `/#signup` | `sign-in.html`, `sign-up.html` | `site.js` — a fragment never reaches the server, so the page has to send it on |
| `/docs`, `/sign-in` | `docs.html`, `sign-in.html` | `nginx.conf`, `try_files $uri $uri.html` |

## What it is allowed to load

The Content-Security-Policy in `nginx.conf` permits this site's own script, styles, images and fonts, and nothing
from anywhere else. Forms may post only to `https://desk.ofintech.ai`. There is no analytics, no font CDN and no
third-party request of any kind, which is also why the brand's own faces are bundled here.
