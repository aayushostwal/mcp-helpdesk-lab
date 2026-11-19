# ADR 0008: The MCP server is a thin wrapper over the HTTP API

Status: accepted

## Context

The helpdesk has real rules: a refund window, an approval threshold, a ticket status machine,
SLA maths, scoped keys, idempotency, an audit trail. An MCP server needs to expose all of it.
There were two ways to build it: import the services and call them in-process, or call the HTTP
API like any other client.

The server is also the thing the benchmark measures. It must behave like something a team would
actually put in front of its API, and tools must be generated from a contract that is stable and
public.

## Decision

`helpdesk_mcp` talks to the API only through HTTP, using an async `httpx` client. It does not
import `helpdesk.services`, repositories, models or the database layer. Its inputs are:

- `openapi.json`, for operation names, descriptions and schemas;
- the API's base URL and an API key (`HELPDESK_MCP_API_URL`, `HELPDESK_MCP_API_KEY`).

Business rules, validation, authorisation, idempotency storage and auditing stay in the API.
The MCP server adds only what MCP needs: tool definitions, instructions, resources, prompts,
translation of arguments to requests, translation of failures to `isError` results, and logging.

## Consequences

- One implementation of every rule. A change to the refund policy is made once and is visible to
  HTTP clients and agents alike. There is no way for the two to drift.
- The MCP server's permissions are exactly the permissions of its API key. Scopes, the lead-only
  refund approval and the audit trail work without extra code, and every MCP write appears in the
  audit log with the key that made it.
- The server can run anywhere that can reach the API, against SQLite or PostgreSQL, without
  database credentials.
- Each call costs an HTTP round trip. For this workload the cost is small next to the model's
  response time, and the benchmark records `api_ms` separately.
- The resource text and server instructions describe the rules in prose, so they can go stale.
  They are kept in one file (`content.py`), written against the implemented rules, and the API's
  own descriptions remain the authority.
- Tests need a running API. They start the real one in-process through the ASGI transport on a
  temporary database, which is the same setup the integration suite uses.
- Validation errors come from the API, not from the MCP server, so the agent sees the API's own
  messages. The server checks only what it needs to build a request.
