# Benchmark

This is how the repo measures what the tool design does to tokens, latency, cost and accuracy.
The agent is **headless Claude Code** (`claude -p`), the same client people use, run once per
task in a fresh process. Everything that feeds an article number comes from `bench/results/`.

Status: the harness, a pilot and the **full matrix** are done. The tables in the Pilot results
section are the pilot's; the full results are in `bench/results/` (`KEY_NUMBERS.md`,
`summary.md`) and the section Post-hoc changes and known task flaws lists what was found after
the full run. Where a sample is too small to conclude anything, the report says so.

## What is measured

Three tool designs for the same 102 API operations
([mcp-server.md](mcp-server.md)), plus one client-side variant:

| Variant | Tools the model sees | Notes |
|---|---:|---|
| `per-endpoint-10`, `-25`, `-50`, `-100` | 10 to 100 | One tool per operation. Subsets always contain the operations the tasks need, in a fixed order (`subset.py`). |
| `toolsets` | 8 | One tool per area with an `action` and a `params` object. |
| `search-call` | 3 | `search_api`, `describe_operation`, `call_api`. |
| `per-endpoint-100+client-tool-search` | 100, deferred | Same server as `per-endpoint-100`, but Claude Code's own tool search is allowed to defer the definitions. Reported separately. |

Models: the aliases `sonnet` and `haiku`; the resolved model id is recorded from the stream.
Tasks: 20. Repeats: 3. The full matrix is **834 runs** (780 task runs and 54 context baselines);
see [Full matrix](#full-matrix-estimate).

## Tasks

Twenty tasks in `bench/tasks/*.yaml`, in the bands from [DESIGN.md](DESIGN.md) section 6: easy 6,
multi-step 8, ambiguous 4, policy 2. Prompts are written the way a support lead would write
them, with no operation names. Every task uses facts the seed guarantees
(`bench/tasks/fixtures.json`). Each has an `id`, `band`, `kind` (`action`, `read` or `policy`),
`prompt`, `needs_operations` (the minimal set), `also_ok_operations` (reads that are reasonable
but not required) and a deterministic Python grader.

| id | band | kind | needs operations |
|---|---|---|---|
| `ambig-01-tell-the-customer` | ambiguous | action | comments_add_reply |
| `ambig-02-leave-a-note` | ambiguous | action | comments_add_note |
| `ambig-03-senior-eyes` | ambiguous | action | tickets_escalate |
| `ambig-04-changed-their-mind` | ambiguous | action | orders_cancel |
| `easy-01-close-ticket` | easy | action | tickets_close |
| `easy-02-find-kb-article` | easy | read | kb_articles_search |
| `easy-03-ship-order` | easy | action | orders_ship |
| `easy-04-oncall-billing` | easy | read | teams_list, teams_get_oncall |
| `easy-05-assign-ticket` | easy | action | tickets_assign |
| `easy-06-tag-ticket` | easy | action | tickets_add_tag |
| `multi-01-refund-recent-order` | multi-step | action | tickets_get, customers_list_orders, refunds_create |
| `multi-02-refund-large-order` | multi-step | action | customers_list_orders, refunds_create |
| `multi-03-approve-pending-refund` | multi-step | action | refunds_list, refunds_approve |
| `multi-04-merge-duplicate-customers` | multi-step | action | tickets_get, customers_merge |
| `multi-05-busiest-agent` | multi-step | read | reports_agent_workload, teams_get |
| `multi-06-merge-duplicate-tickets` | multi-step | action | tickets_get, tickets_merge |
| `multi-07-reopen-and-reply` | multi-step | action | tickets_reopen, comments_add_reply |
| `multi-08-pending-refunds-summary` | multi-step | read | refunds_list, customers_get |
| `policy-01-refund-too-old` | policy | policy | orders_get |
| `policy-02-reopen-too-late` | policy | policy | tickets_get |

**Policy tasks are the ones where the right move is not to act.** `policy-01` (a refund for an
order delivered 45 days ago) and `policy-02` (reopening a ticket solved 12 days ago) must leave
the database untouched and the answer must say why. `multi-02` has a quieter trap: a 680.00
refund must stay `pending_approval`; the agent's key belongs to a lead, so it *could* approve its
own request, and doing so fails the task.

### Grading

After the run, the grader reads the database through the API and, for read tasks, the agent's
final answer. No LLM judge. Rules every grader follows:

- Extra **reads** never matter. Extra **writes** fail the run: the seeded database has no audit
  events, so any audit event (other than the harness's own API key creation) is something the
  agent did, and each grader lists the writes it allows.
- Answers are checked with plain string and number matching (thousands separators ignored,
  `six` accepted for 6).
- Where several answers are right (four agents tie for the most active tickets), the grader
  recomputes the truth from the API and accepts any of them.

Every task was proven with a scripted oracle (`python -m bench check-tasks`, also run by the
test suite): a plain Python solution using the API client passes the grader, and doing nothing
fails it. **All 20 pass the oracle and all 20 fail when nothing is done.**

### Tasks that cannot be solved at 10 tools

`ALWAYS_INCLUDED` starts with the operations of the tasks that matter most at 10 tools. At
`per-endpoint-10`, **10 of the 20 tasks are unsolvable by design** (their operations are not in
the 10 tools): `easy-03`, `-04`, `-05`, `-06`, and `multi-03`, `-04`, `-05`, `-06`, `-07`, `-08`.
Decision: the matrix **does not run them at N=10** (`--include-unsolvable` runs them anyway).
That saves 60 runs that cannot pass and cannot teach anything. Reports at N=10 therefore cover the
ten solvable tasks only, and the report footnotes this. At 25, 50 and 100 tools every task is
solvable (checked by `tests/bench/test_tasks.py`).

## How a run works

```
for each run:
  copy the pristine seeded SQLite file       (seeded once into bench/.cache)
  start the API on a free port               HELPDESK_FROZEN_TIME=2026-06-01T12:00:00+00:00
  create an API key linked to lead A-0002    helpdesk create-api-key
  write an MCP config for the variant        (stdio, started by Claude Code itself)
  run claude in an empty temp directory      one fresh process, own process group
  parse the stream-json, grade the database, append one JSONL row, tear everything down
```

The command (flags verified against `claude --help` on 2.1.326):

```bash
claude -p "<task prompt>" --model <alias> --output-format stream-json --verbose \
  --strict-mcp-config --mcp-config <generated file> --tools "<'' or ToolSearch>" \
  --permission-mode bypassPermissions --no-session-persistence --disable-slash-commands \
  --setting-sources "" --append-system-prompt "<date note>" \
  --max-turns 30 --max-budget-usd <0.50 haiku, 1.50 sonnet>
# environment: ENABLE_TOOL_SEARCH=<false|true>, nothing else from HELPDESK_* or DATABASE_URL
```

| Flag | Why |
|---|---|
| `--strict-mcp-config --mcp-config` | Only the helpdesk server; every other MCP config on the machine is ignored. |
| `--tools ""` | No built-in tools (no Bash, Read, Web), so the agent can only use the server. |
| `--setting-sources ""` | Loads no user, project or local settings (so no plugins, hooks or user memory). Accepted by 2.1.326. |
| `--disable-slash-commands` | No skills or commands. |
| `--no-session-persistence` | Nothing is written to the user's session history. |
| `--permission-mode bypassPermissions` | The agent must not stop to ask. Safe here: only the local helpdesk API is reachable. |
| `--max-turns 30` | Present in the binary and accepted, but **not listed in `--help`**; it ends a run with `error_max_turns`. |
| `--max-budget-usd` | Cap per run, a second guard. A wall-clock timeout (default 300 s) kills the whole process group as the third. |
| not `--bare` | `--bare` needs an API key. The runs use the maintainer's Claude login. |

**The run starts from an empty directory outside the repository.** Claude Code adds git status
and project files from its working directory to the context; the first pilot started inside the
repo and the no-server baseline was about 900 tokens larger. It is fixed, and `tmp` is used.

### Nothing from the user's setup leaks in

The init event of every run lists its tools, MCP servers, skills and plugins. The harness
asserts that only `mcp__helpdesk__*` tools exist (plus `ToolSearch` for the tool-search variant),
that the only MCP server is `helpdesk` and connected, and that no skills are loaded. It stores
`isolation_problems` in every row; `summary.md` reports how many runs had any. **In all 45
pilot runs there were none.** The init event still lists Claude Code's own built-in plugins and
sub-agent names (`agents`); they add no tools when `--tools ""` is set, and they are part of the
fixed overhead measured by the no-server baseline. The tool lists per variant are saved in
`bench/results/tool_lists.json`.

### One deliberate addition: the date

Claude Code puts the real date in the model's context. The API clock is frozen at the seed date
(2026-06-01). In the first pilot run of `multi-01` the model saw today's date as 2026-11-19, saw
an order delivered on 2026-05-31, and refused the refund as outside the 30-day window, which was
correct on its calendar. Every run, baselines included, therefore carries one extra system-prompt
sentence: *the helpdesk system clock is set to 2026-06-01, treat that as today*. The same text is
in every variant, so it cannot favour one; it is also in the no-server baseline, so it cancels in
the context-cost subtraction.

## Tool search: the fairness control

Claude Code can defer MCP tool definitions and load them when the model asks for them. That
would hide exactly the cost this benchmark measures, so the main matrix must send all
definitions up front. `claude --help` does not document this; the binary's strings name
`ENABLE_TOOL_SEARCH` and the error `expected auto:N where N is a number`. The setting was found
empirically (`python -m bench probe-tool-search`; sonnet, per-endpoint server with 100 tools,
prompt "Reply with the word ok", first-request input tokens):

| `--tools` | `ENABLE_TOOL_SEARCH` | Tools listed | First request tokens | Definitions |
|---|---|---:|---:|---|
| `""` | unset | 100 | 30,314 | up front |
| `""` | `false` | 100 | 30,314 | up front |
| `""` | `true` | 100 | 30,316 | up front |
| `""` | `force` | 100 | 30,314 | up front |
| `ToolSearch` | unset | 101 | 6,313 | **deferred** |
| `ToolSearch` | `false` | 100 | 30,316 | up front (ToolSearch is switched off) |
| `ToolSearch` | `true` | 101 | 6,309 | **deferred** |
| `ToolSearch` | `auto` | 101 | 32,130 | up front, plus the ToolSearch tool |
| `ToolSearch` | `force` | 101 | 6,307 | **deferred** |

(No-server baseline for the same model: 3,014 tokens. Raw rows: `bench/results/tool_search_probe.jsonl`.)

Findings:

1. Tool search needs the built-in `ToolSearch` tool. With `--tools ""` (no built-ins) nothing
   is ever deferred, whatever the environment says. Haiku behaves the same as sonnet.
2. With `ToolSearch` available, deferral is on by default (unset, `true` and `force`), and
   `false` turns it off. `auto` did not defer 100 tools.

**Main matrix: `--tools ""` and `ENABLE_TOOL_SEARCH=false`**: definitions are loaded up front,
guarded twice. **Extra variant `per-endpoint-100+client-tool-search`: `--tools ToolSearch` and
`ENABLE_TOOL_SEARCH=true`.** The value used is stored in every row (`env_flags`). The extra
variant has its own no-server baseline (`none+client-tool-search`, which includes the tiny
ToolSearch tool) so subtraction stays like for like. With deferral the 100 tools cost 3,300
first-request tokens (the tool names) instead of 27,300, and the model needs one extra call
(`ToolSearch`) per task.

## Metrics

One JSONL line per run in `bench/results/runs.jsonl` (append-only), the compressed scrubbed
stream in `bench/results/raw/<run_id>.stream.jsonl.gz`, and context baselines in
`context_baseline.jsonl`.

| Field | Meaning |
|---|---|
| `run_id`, `ts`, `git_sha`, `git_dirty`, `claude_version` | Which run, when, which code and CLI. |
| `env_flags` | `ENABLE_TOOL_SEARCH`, built-in tools, setting sources, turn and budget caps, timeout. |
| `variant`, `tool_count`, `model_requested`, `model_resolved` | What was run; the resolved id comes from the stream (`modelUsage`). |
| `task_id`, `band`, `repeat` | The cell. |
| `status` | `ok`, `max_turns`, `budget_exceeded`, `error` (finished; graded), `timeout` (killed; state still graded), `crash`, `rate_limited`, `infra_error` (infrastructure; not graded). |
| `pass`, `grade_reason` | The grader's verdict (null when not graded). |
| `wall_ms`, `api_ms`, `ttft_ms`, `num_turns` | From Claude Code's result event. `wall_ms` includes its start-up, MCP start and tool time. `process_ms` is our own clock for the process. |
| `tool_calls` | Ordered `{tool, operation_id, ok, ms}`. `operation_id` is derived per variant (tool name, `(tool, action)` mapping, or `call_api`'s argument); `ms` is the gap between the call and its result in the stream. |
| `wrong_write_calls`, `wrong_read_calls`, `wrong_calls` | Calls to operations outside `needs_operations` plus `also_ok_operations`. Writes are counted apart from harmless reads. A refused attempt (a 422 from the API) still counts as a write. `meta_calls` (search, describe, ToolSearch), `unknown_calls` (invented operations) and `failed_calls` are separate. |
| `input_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `output_tokens` | Totals over the run, from the result event. |
| `input_tokens_total` | The three input kinds summed: every prompt token the run processed. |
| `first_request_input_tokens` | The same sum for the **first** API request only (first assistant message). This isolates the context cost of the server before any work is done. |
| `total_cost_usd` | CLI-reported, list-price equivalent. |
| `retries`, `retry_log`, `error` | Infrastructure retries and their reasons; the last error text. |

**Context baseline** (`context_baseline.jsonl`): for every variant and a no-server run, the
prompt "Reply with the word ok", 3 times per model. "Added by the server" is the median
first-request tokens minus the median no-server first-request tokens of the same model and
Claude Code configuration. It is what the tool definitions plus the server instructions add.

### Retries and failures

A run that crashes, hits a rate limit (429 or a rejected rate-limit event) or fails to provision
is an **infrastructure error**. Only those are retried (default 2 retries, exponential back-off,
three times longer after a rate limit, and all workers pause for 60 seconds). A wrong answer or a
timeout is never retried. Every attempt that ends is recorded; nothing is dropped. On resume,
runs whose latest row is an infrastructure error are run again; everything else is skipped.
Pass rates count graded runs only; `summary.md` lists status counts.

## Pilot results

Pilot: haiku, 5 tasks (2 easy, 2 multi-step, 1 ambiguous), 1 repeat, 3 variants, plus context
baselines (3 repeats, haiku and sonnet). `per-endpoint-100+client-tool-search` was run once per
model as an extra check (n=1 per cell). Reproduce the tables with `make bench-report`.

**Context added by the server** (first-request input tokens; this is a token count, so n=3 is
enough, the spread was under 5 tokens):

| Variant | Tools | First request (haiku) | Added (haiku) | Added (sonnet) | Estimate in [tool-list-sizes.md](tool-list-sizes.md) |
|---|---:|---:|---:|---:|---:|
| no server | 0 | 3,838 | 0 | 0 | |
| `per-endpoint-100` | 100 | 31,138 | **27,300** | **27,302** | 14,624 (model-visible, bytes / 4) |
| `toolsets` | 8 | 9,955 | **6,117** | **6,119** | 3,228 |
| `search-call` | 3 | 5,258 | **1,420** | **1,422** | 294 |
| `per-endpoint-100+client-tool-search` | 100 (deferred) | 7,136 | 3,301 | 3,295 | |

The real cost is about **1.9 times** the bytes-divided-by-four estimate for the long lists (JSON
schemas tokenise densely), and the added tokens are the same for both models (same tokenizer).
For `search-call` the gap is larger because the server instructions (about 230 words) and the
tool-name framing are a fixed part of the 1,400.

**Task runs (haiku; n=5 per variant, so no accuracy conclusion is possible):**

| Variant | Pass | Median input tokens | Median wall (s) | Median cost (USD) |
|---|---|---:|---:|---:|
| `per-endpoint-100` | 5/5 | 95,018 | 5.8 | 0.0021 |
| `toolsets` | 5/5 | 31,524 | 6.1 | 0.0015 |
| `search-call` | 5/5 | 36,056 | 7.6 | 0.0022 |

With five easy tasks everything passes; the 95% Wilson interval for 5 of 5 is 57% to 100%. These
tables show the harness works and the orders of magnitude (input tokens per task are about 3
times higher with 100 tools up front). They do not rank the variants. The cost column is
mostly cache reads, and on sonnet the first request writes the cache at a much higher price
(0.12 USD for the 100-tool baseline alone), which is why the full run is dominated by sonnet
with the large tool list.

## Full matrix estimate

`python -m bench plan --matrix full` prints this from the pilot medians:

- **834 runs**: 780 task runs (7 variants x 2 models x 20 tasks x 3 repeats, minus the 60 that
  cannot be solved at 10 tools) and 54 context baselines (9 configurations x 2 models x 3).
- **About 50 minutes** with 3 workers (pilot wall times plus about 4 s of set-up per run; sonnet
  is likely slower than the haiku-based figure, so allow up to 90 minutes).
- **About 60 USD** of CLI-reported cost, almost all of it sonnet with the per-endpoint variants
  (each such run pays to write a 30,000-token prompt cache). Estimates for sonnet task runs are
  scaled from haiku using the ratio of the baselines, and the tool-search variant has little
  data, so treat this as plus or minus 50%.

## Commands and reproduction

```bash
make install                      # uv sync --all-extras
make bench-check                  # oracle and do-nothing check of all 20 tasks (no model)
make bench-dry                    # whole pipeline with a fake claude (no model, no cost)
uv run python -m bench run --matrix pilot --plan    # count and estimate, run nothing
make bench-pilot                  # 27 real runs, then the report
uv run python -m bench run --matrix full --workers 3   # the full matrix (phase 4); resumable
uv run python -m bench run --matrix full --models haiku --variants toolsets,search-call
uv run python -m bench probe-tool-search              # the ENABLE_TOOL_SEARCH table above
uv run python -m bench regrade --task multi-05-busiest-agent   # offline re-grade of a read task
make bench-report                 # summary.csv, summary.md, summary_by_task.csv, KEY_NUMBERS.md, charts/
```

Requirements: `claude` 2.1.x logged in (`claude` once, interactively), `uv`, macOS or Linux, no
Docker. Re-running a command skips finished run keys (`variant|model|task|repeat`), so an
interrupted run resumes. `--workers` defaults to 3 to be kind to rate limits. A custom matrix
is a YAML file (`name`, `variants`, `models`, `tasks`, `repeats`, `baseline_repeats`).

The first run seeds the database into `bench/.cache` (a few seconds); delete it to rebuild.
`make bench-dry` and CI use `bench/harness/fake_claude.py`: it replays a recorded stream and, in
`oracle` mode, first performs the task's scripted solution, so the API, graders, retry logic and
result files are exercised without calling a model.

## Report

`python -m bench report` reads the JSONL (latest grade per run) and writes, in
`bench/results/`: `summary.csv`, `summary.md`, `summary_by_task.csv` (pass rate per task, variant
and model), `KEY_NUMBERS.md` (plain tables of the headline numbers) and seven square PNG charts
(1200 px, Okabe-Ito colours, n on every chart) in `charts/`:

1. `1-context-tokens-vs-tool-count.png`: tokens added by the server vs tool count, toolsets and
   search-call as labelled reference lines, no-server baseline subtracted, the tool-search
   variant as one extra marker.
2. `2-input-tokens-per-task.png`: median input tokens per task by variant and model.
3. `3-pass-rate-by-band.png`: pass rate by variant and band with 95% Wilson intervals
   (all tasks, so the multi-step band includes multi-04).
4. `4-wall-time.png`: median wall time with 25th to 75th percentile whiskers, by variant and model.
5. `5-wrong-calls-ambiguous.png`: wrong writes and reads on the ambiguous tasks.
6. `6-cost-per-task.png`: median CLI-reported cost per task (diamond = mean).
7. `7-pass-rate-by-variant.png`: pass rate per variant and model with Wilson intervals, all
   tasks next to excluding the task with a seed flaw (axis starts at 70%, and says so).

Pass rates are reported twice: over all tasks, and **excluding task with seed flaw**
(`multi-04-merge-duplicate-customers`, see the next section).

`summary.md` says plainly when a cell has fewer than 10 runs: "n is too small to conclude".

## Post-hoc changes and known task flaws

The full matrix was run first and these were found afterwards. Nothing was rerun. The runs
themselves are untouched; where a grade changed, the old row stays in `runs.jsonl` and a new
row is appended.

### 1. The `multi-05-busiest-agent` grader was too strict (fixed, offline re-grade)

The task asks for "their name, which team they are on, and how many active tickets". The grader
required the team **name** (for example `Escalations`). Many correct answers gave the team **id**
(`Derek Zuniga, TM-003, 17 active tickets`) and failed. This was a grader bug, not an agent
error. The grader now accepts the team name **or** the team id; the agent-name check (any
agent tied for most active tickets), the ticket-count check and the no-writes check are unchanged.
One known looseness remains: the team may be any tied agent's team, not only the named
agent's own.

`python -m bench regrade --task multi-05-busiest-agent` re-graded the 36 stored runs from their
stored final answer, offline, with the facts taken from a pristine seeded database (read tasks
leave the database unchanged, so this is the state the agent saw). It refuses write tasks,
whose grade depends on state that is not stored. A run that originally failed on an unexpected
write stays failed (none did). Each new row has `regraded_from` (the original `run_id`),
`grade_reason_original` and `pass_original`. **17 runs flipped fail to pass; none flipped pass
to fail; 19 were unchanged (all already passing).**

multi-05 passes (3 runs per cell), before and after:

| variant | haiku before | haiku after | sonnet before | sonnet after |
|---|---:|---:|---:|---:|
| per-endpoint-25 | 0/3 | 3/3 | 3/3 | 3/3 |
| per-endpoint-50 | 0/3 | 3/3 | 2/3 | 3/3 |
| per-endpoint-100 | 1/3 | 3/3 | 3/3 | 3/3 |
| per-endpoint-100+client-tool-search | 0/3 | 3/3 | 3/3 | 3/3 |
| toolsets | 0/3 | 3/3 | 3/3 | 3/3 |
| search-call | 1/3 | 3/3 | 3/3 | 3/3 |
| **all** | **2/18** | **18/18** | **17/18** | **18/18** |

(`per-endpoint-10` does not run this task: its operations are not in the 10 tools.) Over the
whole matrix this moves the pass count from 729 to 746 of 780 graded runs. The before numbers
are what the original rows say; the after numbers are what every table and chart now shows.

### 2. `multi-04-merge-duplicate-customers` has a seed flaw (not fixed, reported)

The prompt says the customers on T-2005 and T-2006 "look like the same person with two
accounts". In the seed they share a **name** (Crystal Jenkins) but have **different e-mail and
phone** (C-0499 and C-0500). Haiku looked, found the records did not clearly match, and did not
merge: **0 of 18 passed, with no merge call in any of the 18 runs** (3 repeats in each of the
6 variants that run this task: per-endpoint-25, -50, -100, -100 with client tool search,
toolsets, search-call). Sonnet merged in **18 of 18** and added a caveat that the name was the
only evidence.

This is a task-data flaw: the premise is not supported by the data, and the same data is used in
every variant, so it hits every variant equally and is not an effect of tool design. What it does
show is a difference between the two models in following an instruction the data seems to
contradict. The seed and the grader are left as they are, and the task is not rerun. It is
not part of the per-endpoint-10 numbers (the task is not solvable at 10 tools). The report gives
every pass rate with and without it; the "without" columns are labelled "excluding task with
seed flaw".

### 3. `ambig-03-senior-eyes` failures are tool-choice and judgement behaviour (kept)

The task: "T-2007 is an outage and the customer is getting angry. Get more senior eyes on it."
The grader wants the ticket escalated (`tickets.escalate`) and nothing else written. 16 of the
42 graded runs of this task fail; they fall into three groups (counts from the stored tool
calls and grade reasons):

| variant | model | no escalation | escalated, plus extra reassign | escalated, plus extra note only |
|---|---|---:|---:|---:|
| per-endpoint-25 | haiku | 0 | 0 | 1 |
| per-endpoint-25 | sonnet | 0 | 3 (2 of them also added a note) | 0 |
| per-endpoint-50 | haiku | 0 | 2 | 0 |
| per-endpoint-50 | sonnet | 3 | 0 | 0 |
| per-endpoint-100 | sonnet | 3 | 0 | 0 |
| per-endpoint-100+client-tool-search | haiku | 1 | 0 | 0 |
| per-endpoint-100+client-tool-search | sonnet | 1 | 0 | 0 |
| toolsets | sonnet | 1 | 0 | 0 |
| search-call | haiku | 0 | 1 | 0 |
| **total** | | **9** | **6** | **1** |

Every other cell of this task passed 3 of 3 (per-endpoint-10, per-endpoint-100 haiku, toolsets
haiku, search-call sonnet). Reading the failures:

- **Extra reassign or note (7 runs)**: the agent escalated correctly and then also called
  `tickets_assign` (to the Escalations on-call lead) and sometimes `comments_add_note`. The
  prompt does not ask for either. These are real over-reach, and 6 of the 7 happened with the
  25-tool and 50-tool servers.
- **No escalation (9 runs)**: the agent read the ticket and its comments and declined, saying the
  customer's last message is "That solved it, thank you", so the ticket does not match the
  prompt. 8 of the 9 are sonnet. This is a reading of the data, not a tool mix-up, and it is
  arguably reasonable: the seeded last comment on T-2007 does contradict "the customer is getting
  angry". Treat it as a second, weaker data inconsistency in this task. It was not corrected and
  the task was not excluded from any number.

With 6 runs per variant (3 per model) and at most 5 failures in any one variant, the differences between
variants on this task are within the intervals. The pattern (over-reach appears with 25 and 50
tools, never with toolsets and rarely with search-call) is a hint, not a result.

### 4. The grading rule: any extra state-changing write fails the run

Every grader lists the writes it allows, and any other audit event fails the run, even when the
requested change was made correctly (see Grading above). Across the whole matrix this rule
produced **7 failures, all in ambig-03** (the 6 reassign and 1 note-only runs above;
three failures in per-endpoint-25 sonnet, one per-endpoint-25 haiku, two per-endpoint-50
haiku, one search-call haiku). It produced none on any other task. It also did **not** fail the
**36 passing runs** that tried a forbidden write on the two policy tasks (an attempt to refund an
old order or reopen a late ticket): the API refused each attempt, so the audit log shows nothing.
Those attempts are counted as wrong-write calls in the report (43 runs have at least one:
36 passing policy runs, 7 failing ambig-03 runs) but do not fail the run. The rule is strict on
purpose; it also means a run that is correct and then tidies up with one more call fails.

## Privacy

Streams and rows are scrubbed before they are written: home directories, user and host names,
temp paths, e-mail addresses, API keys and UUIDs are replaced. A test
(`tests/bench/test_privacy.py`) scans everything under `bench/results/` for them.

## Threats to validity

- **One app.** A synthetic helpdesk with 102 operations. Another API's descriptions, schema
  sizes or naming may move the results either way.
- **One client.** Claude Code. Its system prompt, its handling of MCP tools and its tool search
  shape the numbers; other clients differ. The CLI overhead (about 3,000 to 3,800 tokens of its own
  prompt) is in every total; the baselines subtract it from the context-cost chart only.
- **Synthetic data and 20 tasks.** Facts come from a seeded generator; the tasks were written
  by the people who built the API and the search index, so they may suit it.
- **Prompt caching.** Input tokens are reported as fresh, cached-read and cache-written. A
  cached 30,000-token prefix costs far less than its token count, and the first run of a hour
  pays to write it. Token charts and cost charts can tell different stories; both are shown.
  Runs in parallel share cache entries, so cost has some order dependence.
- **Small n per cell.** 3 repeats per task and model give a few dozen runs per variant; the
  Wilson intervals are wide. The pilot has 5.
- **Maintainer's Claude login.** Cost is the CLI-reported, list-price-equivalent figure
  (`costBasis: list`), not an invoice. Rate limits on the account can slow or interrupt runs.
- **Tool-search setting.** The main matrix disables deferral on purpose; a user who leaves
  Claude Code at its defaults with built-in tools available gets deferral and a different
  trade-off. That case is the extra variant. Behaviour found on 2.1.326 may change.
- **Model aliases are resolved at run time.** `sonnet` and `haiku` point to whatever the CLI
  maps them to that day (the resolved id is recorded: `claude-haiku-5-5` and the sonnet id in
  the pilot). Re-running later may use a different model.
- **Date note and frozen clock.** The system-prompt sentence about the date is not what a
  deployed agent would see. It is the same in every run.
- **`--max-turns` is undocumented.** It works in 2.1.326 but may change.
- **Per-endpoint at 10 tools** covers ten tasks only (see above); do not compare its pass rate
  with the others without restricting to the same ten.
