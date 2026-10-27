# Connecting accounting systems, Google, Telegram and WhatsApp

Whoever runs an installation registers one OAuth app per provider. That is the company distributing the desktop app,
or the administrator of a Team Server. Each customer then connects their own company file through that app. Client
IDs and secrets can be:

- set as environment variables (`DESK_<PROVIDER>_CLIENT_ID`, `DESK_<PROVIDER>_CLIENT_SECRET`, where PROVIDER is
  `QUICKBOOKS`, `ZOHO_BOOKS`, `XERO` or `GOOGLE`); or
- entered by the installation administrator under **Connections → OAuth apps**. Secrets are encrypted at rest.

**Redirect URI** to register with every provider:

| Where | Redirect URI |
|---|---|
| Desktop app | `http://localhost:47821/oauth/callback` |
| Team Server | `https://<your domain>/oauth/callback` |

Each customer's tokens are encrypted and refreshed automatically. Rotated refresh tokens (QuickBooks, Xero) are
stored as soon as they are issued. All connectors are **read-only**.

> **Desktop distribution note.** An OAuth client secret shipped inside a desktop app cannot be kept secret. For a
> commercial release, run the connectors through the Team Server (or a small token-exchange service you host), or
> let each customer register their own developer app. Each provider's review rules (below) apply to whichever app
> your customers sign in to.

---

## QuickBooks Online (Intuit)

1. Create an app at <https://developer.intuit.com/app/developer/dashboard> and choose the **Accounting** scope.
2. Under *Keys & credentials*, add the redirect URI. Development keys work with sandbox companies. For those, set
   **Environment: Sandbox** in OfinTech Desk.
3. Scope requested: `com.intuit.quickbooks.accounting`.
4. **Before going live:** production keys require Intuit's app assessment questionnaire (security and compliance
   questions) and a production redirect URI over HTTPS.

What is synced:
- Profit and loss by month (`reports/ProfitAndLoss`, `summarize_column_by=Month`, minor version 75).
- Balance sheet.
- Invoices and bills (via the query endpoint, last 24 months).

The parser walks nested sections and uses only leaf rows, so sub-accounts are not double counted. It does not
depend on row order, which Intuit's modernised Reports API no longer guarantees.

## Zoho Books

1. Create a *Server-based application* at <https://api-console.zoho.com>, add the redirect URI, and enable
   **multi-DC** so users in every Zoho data centre can sign in.
2. Scopes: `ZohoBooks.reports.READ, ZohoBooks.invoices.READ, ZohoBooks.bills.READ, ZohoBooks.settings.READ,
   ZohoBooks.contacts.READ`. Offline access is requested for a refresh token.
3. The callback's `accounts-server` (or `location`) decides where the token is exchanged. The token response's
   `api_domain` decides where API calls go, so .com, .eu, .in, .com.au, .jp, .ca, .sa, .uk and .com.cn accounts
   all work. An unexpected accounts server is refused.
4. If the account has several organisations, choose one on the connection card.

Rate limits: Zoho allows about 100 requests per minute per organisation, and a daily quota that depends on the
customer's plan. A sync makes about 30 requests: 24 monthly P&L calls, one balance sheet call, and the invoice and
bill pages.

**Check on the first sync:** P&L and balance-sheet report responses are flattened generically (sections → accounts →
totals). Compare the first synced month with the same report in Zoho Books.

## Xero

1. Create a *Web app* at <https://developer.xero.com/app/manage> and add the redirect URI.
2. Apps created after 2 March 2026 must use **granular scopes**. OfinTech Desk requests:
   `openid profile email offline_access accounting.reports.profitandloss.read accounting.reports.balancesheet.read
   accounting.reports.aged.read accounting.invoices.read accounting.contacts.read accounting.settings.read`.
   Apps created earlier can override the scope list in the OAuth app options (until Xero retires broad scopes in
   September 2027).
3. **Connection limits:** uncertified apps are limited by Xero's developer pricing tier. Certification as a Xero App
   Partner becomes mandatory at higher tiers. Check Xero's current developer pricing before onboarding customers.

If a user authorises several organisations, choose one on the connection card. P&L is requested in 12-month chunks
(`periods`/`timeframe=MONTH`).

## Odoo

Odoo connects with an API key rather than an OAuth app, so nothing needs to be registered beforehand.

1. In Odoo, create a user for OfinTech Desk with read access to Accounting. *Accounting / Read-only* or *Billing*
   is enough.
2. Sign in as that user and open *My Preferences → Account Security → New API Key*. Give the key a name and a
   duration, then copy it.
3. In OfinTech Desk, open **Connections → Accounting → Odoo**. Enter the Odoo address and the key. Also enter the
   database name if the server hosts several databases, and the company id if the database has several companies.
   For Odoo 17 and 18, also enter the user's login.

Odoo 19 and later are reached through the JSON-2 API. Odoo 17 and 18 are reached through JSON-RPC, which Odoo
deprecated in version 19 and plans to remove. The connector picks the API automatically. Details and the account
mapping are in [ACCOUNTING_API.md](ACCOUNTING_API.md#odoo).

## Any other accounting system

Use the Open Accounting Connector: [ACCOUNTING_API.md](ACCOUNTING_API.md). It describes:

- an adapter that OfinTech Desk reads (pull), with a reference implementation in `examples/accounting-adapter`;
- an ingest API that your system writes to (push);
- one data format for both, published as OpenAPI 3.1 in `docs/openapi/accounting-connector.json`.

## Google: Gmail, Drive and Sheets

1. In Google Cloud Console, create an OAuth client of type **Web application** and add the redirect URI. Enable the
   Gmail API and the Google Drive API.
2. Scopes: `openid email gmail.readonly gmail.compose drive.readonly spreadsheets.readonly`, with PKCE and offline
   access.
3. **Verification:** `gmail.readonly` and `gmail.compose` are *restricted* scopes. Before external users can sign in
   without the "unverified app" warning (and beyond 100 test users), Google requires restricted-scope OAuth
   verification. If restricted-scope data is stored or transmitted on servers, Google also requires an annual
   third-party **security assessment**. Plan time for this before launch. To avoid it, drop Gmail and keep Drive
   import only, or keep the app in testing mode for internal use.

Gmail HTML bodies are converted to plain text. Sending and drafting are write actions and always need approval.

## Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and paste the token under **Connections → Telegram**.
2. Desktop app: the bot is polled while the app runs. Team Server with HTTPS: a webhook with a secret header is set.
3. Each member presses **Link my account**. They get a one-time code (valid for 15 minutes) and a `t.me` link that
   sends it. Only private chats are handled.

## WhatsApp Business (Cloud API)

Needs a public HTTPS address, so set it up on the Team Server.

1. In Meta for Developers, create an app with the WhatsApp product. Complete **business verification** to message
   customers at scale. Create a *system user* with a permanent token that has `whatsapp_business_messaging`.
2. Enter the phone number ID, display number, permanent token and **app secret** under **Connections → WhatsApp**.
3. Copy the callback URL and verify token that are shown into *WhatsApp → Configuration*, and subscribe to
   **messages**.
4. Incoming webhooks are accepted only with a valid `X-Hub-Signature-256` HMAC from the app secret.
5. Replies to a member's message are free-form, within the 24-hour customer service window. Workflow notifications
   sent outside that window need an **approved message template** with one body variable. Enter its name on the
   connection.

Graph API version: `v26.0` by default (`DESK_WHATSAPP_GRAPH_VERSION`).
