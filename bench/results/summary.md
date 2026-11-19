# Benchmark summary

Task runs: 780 (780 graded). Context baseline runs: 54. Statuses: ok=834.

Isolation check: 0 run(s) had unexpected tools, skills or MCP servers in the init event.

## Context added by the server (first request)

| variant | model | n | first request tokens | no-server tokens | added by server |
|---|---|---|---|---|---|
| none | haiku | 3 | 3838 | 3838 | 0 |
| none | sonnet | 3 | 3014 | 3014 | 0 |
| none+client-tool-search | haiku | 3 | 3838 | 3838 | 0 |
| none+client-tool-search | sonnet | 3 | 3014 | 3014 | 0 |
| per-endpoint-10 | haiku | 3 | 7519 | 3838 | 3681 |
| per-endpoint-10 | sonnet | 3 | 6693 | 3014 | 3679 |
| per-endpoint-100 | haiku | 3 | 31138 | 3838 | 27300 |
| per-endpoint-100 | sonnet | 3 | 30316 | 3014 | 27302 |
| per-endpoint-100+client-tool-search | haiku | 3 | 7135 | 3838 | 3297 |
| per-endpoint-100+client-tool-search | sonnet | 3 | 6313 | 3014 | 3299 |
| per-endpoint-25 | haiku | 3 | 11774 | 3838 | 7936 |
| per-endpoint-25 | sonnet | 3 | 10952 | 3014 | 7938 |
| per-endpoint-50 | haiku | 3 | 18471 | 3838 | 14633 |
| per-endpoint-50 | sonnet | 3 | 17649 | 3014 | 14635 |
| search-call | haiku | 3 | 5258 | 3838 | 1420 |
| search-call | sonnet | 3 | 4436 | 3014 | 1422 |
| toolsets | haiku | 3 | 9955 | 3838 | 6117 |
| toolsets | sonnet | 3 | 9133 | 3014 | 6119 |

36 run(s) carry a post-hoc re-grade (`regraded_from`); the latest grade is counted. See docs/benchmark.md, section Post-hoc changes and known task flaws.

## Pass rates: all tasks and excluding the task with a seed flaw

multi-04-merge-duplicate-customers has a flaw in its seed data (see docs/benchmark.md). The second pair of columns drops it. Intervals are 95% Wilson; most overlap.

| variant | model | all tasks: passed/n | all tasks: 95% interval | excluding task with seed flaw: passed/n | excluding task with seed flaw: 95% interval |
|---|---|---|---|---|---|
| per-endpoint-10 | haiku | 30/30 (100%) | 89% to 100% | 30/30 (100%) | 89% to 100% |
| per-endpoint-10 | sonnet | 30/30 (100%) | 89% to 100% | 30/30 (100%) | 89% to 100% |
| per-endpoint-10 | all | 60/60 (100%) | 94% to 100% | 60/60 (100%) | 94% to 100% |
| per-endpoint-25 | haiku | 56/60 (93%) | 84% to 97% | 56/57 (98%) | 91% to 100% |
| per-endpoint-25 | sonnet | 57/60 (95%) | 86% to 98% | 54/57 (95%) | 86% to 98% |
| per-endpoint-25 | all | 113/120 (94%) | 88% to 97% | 110/114 (96%) | 91% to 99% |
| per-endpoint-50 | haiku | 55/60 (92%) | 82% to 96% | 55/57 (96%) | 88% to 99% |
| per-endpoint-50 | sonnet | 57/60 (95%) | 86% to 98% | 54/57 (95%) | 86% to 98% |
| per-endpoint-50 | all | 112/120 (93%) | 87% to 97% | 109/114 (96%) | 90% to 98% |
| per-endpoint-100 | haiku | 57/60 (95%) | 86% to 98% | 57/57 (100%) | 94% to 100% |
| per-endpoint-100 | sonnet | 57/60 (95%) | 86% to 98% | 54/57 (95%) | 86% to 98% |
| per-endpoint-100 | all | 114/120 (95%) | 90% to 98% | 111/114 (97%) | 93% to 99% |
| per-endpoint-100+client-tool-search | haiku | 56/60 (93%) | 84% to 97% | 56/57 (98%) | 91% to 100% |
| per-endpoint-100+client-tool-search | sonnet | 59/60 (98%) | 91% to 100% | 56/57 (98%) | 91% to 100% |
| per-endpoint-100+client-tool-search | all | 115/120 (96%) | 91% to 98% | 112/114 (98%) | 94% to 100% |
| toolsets | haiku | 57/60 (95%) | 86% to 98% | 57/57 (100%) | 94% to 100% |
| toolsets | sonnet | 59/60 (98%) | 91% to 100% | 56/57 (98%) | 91% to 100% |
| toolsets | all | 116/120 (97%) | 92% to 99% | 113/114 (99%) | 95% to 100% |
| search-call | haiku | 56/60 (93%) | 84% to 97% | 56/57 (98%) | 91% to 100% |
| search-call | sonnet | 60/60 (100%) | 94% to 100% | 57/57 (100%) | 94% to 100% |
| search-call | all | 116/120 (97%) | 92% to 99% | 113/114 (99%) | 95% to 100% |

Per-task pass rates by variant: `summary_by_task.csv`.

## Per variant and model (all bands)

| variant | model | n | pass (95% Wilson) | median input tokens | median wall s | median cost USD | mean wrong writes |
|---|---|---|---|---|---|---|---|
| per-endpoint-10 | haiku | 30 | 30/30 (89% to 100%) | 24080 | 6.6 | 0.0014 | 0.00 |
| per-endpoint-10 | sonnet | 30 | 30/30 (89% to 100%) | 21013 | 6.2 | 0.0198 | 0.00 |
| per-endpoint-25 | haiku | 60 | 56/60 (84% to 97%) | 36661 | 5.3 | 0.0015 | 0.07 |
| per-endpoint-25 | sonnet | 60 | 57/60 (86% to 98%) | 34008 | 5.9 | 0.0211 | 0.12 |
| per-endpoint-50 | haiku | 60 | 55/60 (82% to 96%) | 56988 | 5.9 | 0.0017 | 0.10 |
| per-endpoint-50 | sonnet | 60 | 57/60 (86% to 98%) | 54239 | 6.2 | 0.0245 | 0.00 |
| per-endpoint-100 | haiku | 60 | 57/60 (86% to 98%) | 95044 | 6.0 | 0.0021 | 0.05 |
| per-endpoint-100 | sonnet | 60 | 57/60 (86% to 98%) | 92172 | 6.2 | 0.0283 | 0.03 |
| per-endpoint-100+client-tool-search | haiku | 60 | 56/60 (84% to 97%) | 32335 | 7.2 | 0.0021 | 0.07 |
| per-endpoint-100+client-tool-search | sonnet | 60 | 59/60 (91% to 100%) | 28472 | 8.7 | 0.0336 | 0.05 |
| toolsets | haiku | 60 | 57/60 (86% to 98%) | 31571 | 6.1 | 0.0016 | 0.07 |
| toolsets | sonnet | 60 | 59/60 (91% to 100%) | 28900 | 6.6 | 0.0232 | 0.05 |
| search-call | haiku | 60 | 56/60 (84% to 97%) | 32576 | 9.0 | 0.0021 | 0.08 |
| search-call | sonnet | 60 | 60/60 (94% to 100%) | 22963 | 8.6 | 0.0283 | 0.07 |

## Failed runs

| variant | model | task | reason |
|---|---|---|---|
| per-endpoint-25 | sonnet | ambig-03-senior-eyes | unexpected write(s): tickets.assign |
| per-endpoint-25 | sonnet | ambig-03-senior-eyes | unexpected write(s): comments.add_note, tickets.assign |
| per-endpoint-25 | sonnet | ambig-03-senior-eyes | unexpected write(s): comments.add_note, tickets.assign |
| per-endpoint-25 | haiku | ambig-03-senior-eyes | unexpected write(s): comments.add_note |
| per-endpoint-25 | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-25 | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-25 | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-50 | sonnet | ambig-03-senior-eyes | T-2007 has escalation level 0, priority normal |
| per-endpoint-50 | sonnet | ambig-03-senior-eyes | T-2007 has escalation level 0, priority normal |
| per-endpoint-50 | sonnet | ambig-03-senior-eyes | T-2007 has escalation level 0, priority normal |
| per-endpoint-50 | haiku | ambig-03-senior-eyes | unexpected write(s): tickets.assign |
| per-endpoint-50 | haiku | ambig-03-senior-eyes | unexpected write(s): tickets.assign |
| per-endpoint-50 | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-50 | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-50 | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-100 | sonnet | ambig-03-senior-eyes | T-2007 has escalation level 0, priority normal |
| per-endpoint-100 | sonnet | ambig-03-senior-eyes | T-2007 has escalation level 0, priority normal |
| per-endpoint-100 | sonnet | ambig-03-senior-eyes | T-2007 has escalation level 0, priority normal |
| per-endpoint-100 | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-100 | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-100 | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| toolsets | sonnet | ambig-03-senior-eyes | T-2007 has escalation level 0, priority normal |
| toolsets | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| toolsets | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| toolsets | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| search-call | haiku | ambig-03-senior-eyes | unexpected write(s): tickets.assign |
| search-call | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| search-call | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| search-call | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-100+client-tool-search | sonnet | ambig-03-senior-eyes | T-2007 has escalation level 0, priority normal |
| per-endpoint-100+client-tool-search | haiku | ambig-03-senior-eyes | T-2007 has escalation level 0, priority normal |
| per-endpoint-100+client-tool-search | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-100+client-tool-search | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
| per-endpoint-100+client-tool-search | haiku | multi-04-merge-duplicate-customers | C-0500 was not merged into C-0499 |
