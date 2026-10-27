"""A small stdio MCP server used by the tests (a pretend ledger)."""

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

server = MCPServer("ledger")


@server.tool(description="Exchange rate for a currency pair such as EURUSD.",
             annotations=ToolAnnotations(readOnlyHint=True))
def fx_rate(pair: str) -> str:
    rates = {"EURUSD": "1.0850", "GBPUSD": "1.2710"}
    return f"{pair.upper()} = {rates.get(pair.upper(), 'unknown')} (test data)"


@server.tool(description="Post a journal entry to the ledger.", annotations=ToolAnnotations(destructiveHint=True))
def post_journal(memo: str, amount: float) -> str:
    return f"Posted journal '{memo}' for {amount:.2f}"


if __name__ == "__main__":
    server.run("stdio")
