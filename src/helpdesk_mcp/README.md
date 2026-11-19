# helpdesk_mcp (coming in phase 2)

The MCP server that exposes the Helpdesk API to AI agents. It is a thin layer over the HTTP API
and never touches the database. Planned: server instructions, resources and prompts, and three
tool designs (one tool per endpoint, grouped toolsets, search-and-call) generated from
`openapi.json`. See [docs/DESIGN.md](../../docs/DESIGN.md) section 5.
