# Benchmark summary

Task runs: 17 (17 graded). Context baseline runs: 28. Statuses: ok=45.

**n is too small to conclude anything.** The largest variant x model cell has 5 task runs (fewer than 10). Treat the pass rates and medians below as a smoke test of the harness, not as results.

Isolation check: 0 run(s) had unexpected tools, skills or MCP servers in the init event.

## Context added by the server (first request)

| variant | model | n | first request tokens | no-server tokens | added by server |
|---|---|---|---|---|---|
| none | haiku | 3 | 3838 | 3838 | 0 |
| none | sonnet | 3 | 3014 | 3014 | 0 |
| none+client-tool-search | haiku | 1 | 3835 | 3835 | 0 |
| none+client-tool-search | sonnet | 1 | 3012 | 3012 | 0 |
| per-endpoint-100 | haiku | 3 | 31138 | 3838 | 27300 |
| per-endpoint-100 | sonnet | 3 | 30316 | 3014 | 27302 |
| per-endpoint-100+client-tool-search | haiku | 1 | 7136 | 3835 | 3301 |
| per-endpoint-100+client-tool-search | sonnet | 1 | 6307 | 3012 | 3295 |
| search-call | haiku | 3 | 5258 | 3838 | 1420 |
| search-call | sonnet | 3 | 4436 | 3014 | 1422 |
| toolsets | haiku | 3 | 9955 | 3838 | 6117 |
| toolsets | sonnet | 3 | 9133 | 3014 | 6119 |

## Per variant and model (all bands)

| variant | model | n | pass (95% Wilson) | median input tokens | median wall s | median cost USD | mean wrong writes |
|---|---|---|---|---|---|---|---|
| per-endpoint-100 | haiku | 5 | 5/5 (57% to 100%) | 95018 | 5.8 | 0.0021 | 0.00 |
| per-endpoint-100+client-tool-search | haiku | 1 | 1/1 (21% to 100%) | 32100 | 5.9 | 0.0019 | 0.00 |
| per-endpoint-100+client-tool-search | sonnet | 1 | 1/1 (21% to 100%) | 28498 | 7.8 | 0.0316 | 0.00 |
| toolsets | haiku | 5 | 5/5 (57% to 100%) | 31524 | 6.1 | 0.0015 | 0.00 |
| search-call | haiku | 5 | 5/5 (57% to 100%) | 36056 | 7.6 | 0.0022 | 0.00 |
