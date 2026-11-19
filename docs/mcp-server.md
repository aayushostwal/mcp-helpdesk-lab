# MCP server

Status: **not built yet (phase 2).** The design is in [DESIGN.md](DESIGN.md) section 5.

The server will wrap the Helpdesk API described in [api.md](api.md). The API's `openapi.json`
is its input: tool names come from `operationId`, tool descriptions from each operation's
summary and description.
