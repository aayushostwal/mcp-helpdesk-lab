# ADR 0009: The three tool designs share one executor

Status: accepted

## Context

The benchmark compares three ways of exposing the same API to an agent: one tool per endpoint,
a few grouped tools with an `action`, and a search-then-call pair. The comparison is only fair
if the designs differ in how they are presented, not in what they do. If one variant sent
slightly different requests, parsed errors differently or skipped an idempotency key, accuracy
differences could come from the plumbing instead of the design.

## Decision

Every variant maps a tool call to `(operation_id, flat arguments)` and calls
`Executor.call`. The executor alone:

- checks argument names and builds the path, query string and JSON body from the operation;
- adds the API key, a request id, and an `Idempotency-Key` on every POST;
- retries connection failures and timeouts with the same idempotency key;
- turns the response into an `ExecResult`: parsed JSON, or a problem document with a status.

The variants add only presentation: which tools are listed, how they are described, and how the
tool arguments are unpacked (`action` and `params`, or `operation_id` and `params`). The tool
result text is `ExecResult.text()` in all cases, so the same operation returns byte-identical
JSON from any variant.

The server layer (`server.py`) handles `tools/list` and `tools/call` for all variants. FastMCP
registers tools from Python function signatures, which cannot express schemas generated from
OpenAPI. So `server.py` replaces FastMCP's two tool handlers with ones that delegate to the
selected variant, through `FastMCP._mcp_server` (the low-level server FastMCP itself uses).
Instructions, resources, prompts and both transports still come from FastMCP.

## Consequences

- A test runs the same 21-step scenario (reads, writes and errors) through each variant on a
  fresh database and asserts the results are equal after removing the per-request `request_id`.
- Adding a variant means writing a class with `tools()` and `call()`. Argument handling, errors,
  idempotency and logging come for free.
- Per-variant behaviour that benchmark readers might expect to differ (such as validation of
  `params`) does not: `toolsets` and `search-call` do not check types in the MCP layer either;
  the API answers with its own 422.
- The server depends on a private attribute of FastMCP (`_mcp_server`) and on the low-level
  `list_tools` and `call_tool` decorators. The `mcp` dependency is pinned to `>=1.30,<2`, and the
  server tests would fail on a change that breaks the replacement. The alternative, building the
  server on the low-level `Server` directly, would mean reimplementing resource templates,
  prompts and the HTTP transport wiring.
- Tool results are plain JSON text with no `structuredContent` and no `outputSchema`. This keeps
  the token cost of results the same in every variant. A client that wants typed output has to
  parse the text.
