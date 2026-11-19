# Tool list sizes

Size of the serialised `tools/list` result for each variant, measured by `scripts/mcp_token_report.py`. Regenerate with `make tool-sizes`.

- **Tokens are approximate: bytes / 4.** They are not tokenizer counts. Phase 3 measures the real numbers; use this table as a sanity check against them.
- *Full list* is the whole `tools/list` JSON, annotations included. *Model-visible* keeps only name, description and input schema, which is what a client normally shows the model.
- Server instructions, resources and prompts are not included here.

| Variant | Tools | Full list (bytes) | Full list (~tokens) | Model-visible (bytes) | Model-visible (~tokens) |
|---|---:|---:|---:|---:|---:|
| per-endpoint, 10 tools | 10 | 7,332 | 1,833 | 6,290 | 1,572 |
| per-endpoint, 25 tools | 25 | 18,388 | 4,597 | 15,787 | 3,947 |
| per-endpoint, 50 tools | 50 | 35,692 | 8,923 | 30,499 | 7,625 |
| per-endpoint, 100 tools | 100 | 68,877 | 17,219 | 58,497 | 14,624 |
| per-endpoint, all tools | 102 | 70,288 | 17,572 | 59,699 | 14,925 |
| toolsets | 8 | 13,747 | 3,437 | 12,911 | 3,228 |
| search-call | 3 | 1,488 | 372 | 1,178 | 294 |
