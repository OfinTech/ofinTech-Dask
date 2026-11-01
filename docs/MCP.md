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
| Run without asking | Allowed only for tools the server marks `readOnlyHint`, and only in the form an admin approved: saving the policy records the tool's name, description, input schema and read-only flag. If a refresh shows the tool changed under the same name, it asks again (the card says *changed since approval*) until an admin saves the policy again |
| Don't use | Hidden from the assistant |

Each server has one persistent session, which closes after 10 idle minutes. **Refresh** reloads the tool list.
After upgrading to this version, tools that were already set to *run without asking* ask once more until an
admin re-saves their policy.

## Using this workspace from other apps (server)

The engine serves MCP (Streamable HTTP, stateless JSON) at `/mcp`:

- Desktop: `http://localhost:47821/mcp`
- Team Server: `https://<domain>/mcp`

Create a key under **Settings → MCP access**. Keys are stored hashed and the full key is shown once. Send it as
`Authorization: Bearer odk_…`. The key acts as its member in that workspace and exposes only **read-only** tools:
the finance calculators, `list_datasets`, `describe_dataset`, `query_dataset`, `accounting_report` and
`list_invoices`, `search_documents`, `list_documents` and `read_document`. Tools that send, write or run workflows are never exposed. Calls are audited.
