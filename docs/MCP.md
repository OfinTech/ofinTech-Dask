# Model Context Protocol

## Using MCP servers (client)

**Connections → MCP servers → Add**:

- **Remote (Streamable HTTP):** a URL and optional headers (for example `Authorization: Bearer …`). Headers are
  stored encrypted. On the Team Server, URLs must be public HTTPS.
- **Local command (desktop app only):** a program and its arguments, such as `npx -y @scope/server` or
  `uvx some-server`, with optional environment variables. It runs on your computer with your permissions and a
  minimal environment.

The server's tools appear to the assistant as `mcp_<server>__<tool>`. Each tool has a policy:

| Policy | Behaviour |
|---|---|
| Ask for approval (default) | Every call waits in the approvals inbox with its exact arguments |
| Run without asking | Allowed only for tools the server marks `readOnlyHint` |
| Don't use | Hidden from the assistant |

Each server has one persistent session, which closes after 10 idle minutes. **Refresh** reloads the tool list.

## Using this workspace from other apps (server)

The engine serves MCP (Streamable HTTP, stateless JSON) at `/mcp`:

- Desktop: `http://localhost:47821/mcp`
- Team Server: `https://<domain>/mcp`

Create a key under **Settings → MCP access**. Keys are stored hashed and the full key is shown once. Send it as
`Authorization: Bearer odk_…`. The key acts as its member in that workspace and exposes only **read-only** tools:
the finance calculators, `list_datasets`, `describe_dataset`, `query_dataset`, `accounting_report` and
`list_invoices`, `search_documents`, `list_documents` and `read_document`. Tools that send, write or run workflows are never exposed. Calls are audited.
