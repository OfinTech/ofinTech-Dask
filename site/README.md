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
