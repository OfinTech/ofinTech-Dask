# Data boundaries

What OfinTech Desk will not do, however the assistant is asked, and how each limit is enforced in code. The
assistant's instructions say the same, but none of these limits depends on the language model obeying them.

| Limit | Enforced by | Tested in |
|---|---|---|
| No payments | The tool registry refuses any tool that initiates, approves, schedules or changes a payment, a transfer, a beneficiary or bank details. The check reads names in several wordings (`createPayment`, `transfer_funds`, `update_bank_details`, `virement`) and descriptions. Built-in tools are refused at start-up. MCP tools are always treated as acting, whatever the server claims: they are hidden from the model and refused if called. | `Payments` |
| Data stays inside (per workspace) | `egress_policy = sealed`, which only the owner can change. Tools that send data to outside services are hidden from the model and refused if called: e-mail, Telegram, WhatsApp, Google Drive and MCP servers. Google is refused where every Google call starts, so workflow e-mail steps, e-mail triggers and Drive collections stop too. Telegram and WhatsApp replies are not sent, and not counted as sent. Outgoing webhooks are refused and their queued deliveries are not sent. PipesHub and the language and embedding models must be on the local network. No MCP servers can be added or refreshed, no access keys can be made for outside clients, and the built-in MCP server shows such clients nothing. Sealing applies at once, also to runs already in progress. | `SealedWorkspace` |
| Data stays inside (whole server) | `DESK_SEALED=1`. Every workspace is sealed and cannot be reopened. The engine's HTTP client refuses hosts outside the private network before any DNS lookup or connection. Addresses are judged in any notation (`134744072`, `0x8080808`, IPv4-mapped, 6to4, NAT64). URL checks need no DNS lookup, and local (stdio) MCP servers are off. `server/docker-compose.sealed.yml` puts the engine on a network with no internet route. | `SealedServer`: watches the socket and DNS calls |
| Bank identifiers masked | Every message on its way to a language model passes one wrapper: user text, tool results, documents, memory and workflow steps. IBANs keep only their country, check digits and last four characters, in any case or separator. Card numbers keep only their last four, in any common grouping or in Arabic-Indic digits. Amounts, invoice numbers and VAT numbers are kept. | `Masking` |
| Data is not instructions | Text in tool results, documents, e-mails and bank transactions is treated as data (rule 13). An action the model is talked into still needs a person's approval, and in a sealed workspace it does not exist. | `Masking.test_the_model_sees_only_masked_numbers` |

The tests are in `engine/tests/test_security_boundaries.py`. Each protection was also switched off on purpose to
confirm that its tests then fail. An independent review then looked for ways around these limits and found eight. Each
is fixed, and each has a test that repeats the attempt.

## Open banking

OfinTech Desk is built to read account information (AIS): balances and transactions, through a licensed provider.
It never asks for payment-initiation (PIS) permissions. No open-banking connection ships yet.

In Saudi Arabia, open banking runs under SAMA's framework through licensed participants. OfinTech Desk makes no
claim of SAMA approval, PDPL compliance or any certification until a separate implementation and an independent
assessment exist.

## What a sealed workspace still allows

A sealed workspace still reads from the accounting system it is connected to (QuickBooks, Xero, Zoho Books, Odoo or
the Open Accounting Connector), because that is where its data comes from. Reading sends requests and credentials to
that system; no workspace data is sent the other way. To keep even that inside, run a sealed server and list only
internal systems in `DESK_EGRESS_ALLOW`.

## What a sealed server still needs from you

- **A model inside the network.** Run Ollama or vLLM on the `inside` network.
- **An internal mail relay, if any.** On a sealed server, e-mail (password resets and invitations) is switched off
  when `DESK_SMTP_HOST` is outside the private network; the app then shows the links instead. Point it at an
  internal relay to keep e-mail.
- **Certificates.** Caddy needs the internet to obtain certificates, unless you use an internal certificate
  authority.
