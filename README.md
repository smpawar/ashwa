# ASHWA: Agentic System Health & Workload Analysis

**An AWR-style diagnostic report for AI agents.** If you can read an Oracle AWR
report, you can read this: a load profile, a time model, top calls, efficiency
ratios, execution plans and a compare-periods report, built from the traces
your agent already sends to [Langfuse](https://langfuse.com).

Each report section is one plain SQL file you can run on its own. A small
Python script runs them for one time window (like `awrrpt.sql`) or two (like
`awrddrpt.sql`) and lays the results out in AWR reading order.

![Refund trajectories before and after a one-sentence prompt change](figures/fig3_refund_trajectories.png)

*The same refund task, before (A) and after (B) one sentence was added to the
agent's instructions. A new tool sequence for a familiar task is the agent
equivalent of a new plan hash value for the same SQL_ID.*

## Why

Agent tracing tools collect plenty of data but offer dashboards, not a method.
AWR gives a DBA an accounting identity (where did the time go), a fixed reading
order, and a way to compare a good period with a bad one. ASHWA applies that
structure to agents:

**Agent Time = model time + tool time + retrieval time + orchestration time + time waiting on rate limits, retries or humans**

| AWR concept | ASHWA equivalent |
|---|---|
| DB Time | Agent Time: the sum of all turn durations |
| Average Active Sessions | Average active agents: Agent Time ÷ elapsed time |
| Wait class | Span type: model call, tool call, retrieval, embedding, framework |
| SQL_ID | A model call plus its model (ideally, a prompt template) |
| Plan hash value | Trajectory signature: the ordered tool calls in a turn |
| Snapshot interval | A time window over stored traces |
| `DBA_HIST_*` repository | Langfuse's ClickHouse tables |
| `awrrpt.sql` / `awrddrpt.sql` | `ashwa_report.py` and `sql/*.sql` |

## What it found: one "harmless" sentence

This repository includes the experiment ASHWA was built on. A customer-support
agent ran the same 20 scripted conversations under two versions of its
instructions. Version 2 adds one well-intentioned sentence:

> *Accuracy matters: before answering or taking any action, check the knowledge
> base for the relevant policy so your answer always reflects current policy.*

Same model, same tools, same workload, three interleaved runs of each version
(144 turns, about 11 minutes, about $2.50):

| Per turn | v1 | v2 | Change |
|---|---|---|---|
| Agent Time (s) | 4.35 | 4.40 | within run-to-run noise |
| Tool calls | 1.38 | 1.74 | **+26%** |
| Embedding calls | 0.49 | 0.90 | **+86%** |
| Cost ($) | 0.0165 | 0.0184 | **+12%** |
| Cost per successful conversation ($) | 0.0198 | 0.0237 | **+20%** |
| Task success | 100% | 93.3% | −6.7 points |

Latency did not move: the extra retrieval calls take milliseconds, and model
time is 99% of Agent Time in both versions. The change shows up only in the
load profile and the trajectories:
refunds went from `lookup_order → issue_refund` to
`lookup_order → search_knowledge_base → issue_refund`, and one refund request
stopped being processed because the agent, having read the returns policy,
asked the customer a question instead. A latency dashboard would have shown
nothing.

![Every metric that moved 5% or more](figures/fig1_biggest_changes.png)

The full reports are in the repository: [`report.html`](report.html) (compare)
and [`report_latest_run.html`](report_latest_run.html) (single run), with the
run records in [`runs.json`](runs.json).

## The report sections

| File | Section | AWR counterpart |
|---|---|---|
| `sql/01_load_profile.sql` | Header and load profile: turns, Agent Time, average active agents, latency percentiles, calls, tokens, cost | Report header + Load Profile |
| `sql/02_time_model.sql` | Agent Time split into model, tool code, retrieval/database, embedding, orchestration, error/retry (self time) | Time Model Statistics + Top Foreground Events |
| `sql/03a_top_model_calls.sql` | Model calls ranked by elapsed time, with p50/p95/p99 and tokens | SQL ordered by Elapsed Time |
| `sql/03b_top_tools.sql` | Tools, retrieval, SQL and embeddings ranked by elapsed time, with percentiles and error rate | SQL ordered by Executions + Wait Event Histogram |
| `sql/04a_efficiency.sql` | Steps, tokens and cost per turn; tool error, duplicate-call, re-embedding and prompt-cache rates | Instance Efficiency Percentages |
| `sql/04b_quality.sql` | Task success rate | (no AWR equivalent) |
| `sql/05_trajectories.sql` | Tool sequence per turn for each task type, with steps, latency, cost and success | Plan regressions (new plan hash value) |

The compare report adds **6. Biggest changes** (every metric that moved 5% or
more, largest first, marked better or worse) and, in batch mode, **Run-to-run
spread** (each run measured separately; overlapping ranges mean noise).

## Use it on your own agent

You need a self-hosted Langfuse **v4** (the queries read its `events_core` and
`scores` tables) with ClickHouse reachable from where you run the report, and
an agent that sends traces to it. You do not need the experiment in this
repository.

```bash
git clone https://github.com/smpawar/ashwa && cd ashwa
python3 -m venv .venv && .venv/bin/pip install clickhouse-connect python-dotenv
cp .env.example .env        # set the CLICKHOUSE_* values for your Langfuse
```

One window, like `awrrpt.sql` (times are UTC):

```bash
.venv/bin/python ashwa_report.py --project <langfuse-project-id> \
  --root-name <turn-span-name> --from "2026-10-09 14:00" --to "2026-10-09 15:00" --html report.html
```

Two windows, like `awrddrpt.sql`:

```bash
.venv/bin/python ashwa_report.py --project <langfuse-project-id> --root-name <turn-span-name> \
  --from "..." --to "..." --compare-from "..." --compare-to "..."
```

Four settings adapt the report to your system:

| Flag | Meaning | Default (this repo's experiment) |
|---|---|---|
| `--root-name` | Name of the top-level span that represents one agent turn | `support_turn` |
| `--tool` | Extra span names to count as tool calls (spans Langfuse already types as tools are found automatically); repeatable | the experiment's four tools |
| `--success-score` | Name of a 0-to-1 Langfuse score that marks a successful conversation | `task_success` |
| `--task-tag` | Trace tag prefix that names the type of task, used to group trajectories | `scenario:` |

`--prefix` and `--compare-prefix` restrict a window to sessions whose ID starts
with a given prefix.

Any section can also be run directly, the way a DBA runs a script. With a local
Langfuse clone next to this repository:

```bash
cd ../langfuse && docker compose exec -T clickhouse clickhouse-client --password clickhouse \
  --param_project=local-project --param_start='2026-10-09 14:00:00' --param_end='2026-10-09 15:00:00' \
  --param_elapsed_s=3600 --param_session_prefixes="[]" --param_root_names="['support_turn']" \
  --param_tool_names="[]" --format PrettyCompact < ../ashwa/sql/02_time_model.sql
```

`04b_quality.sql` and `05_trajectories.sql` also take `--param_success_score`,
and `05_trajectories.sql` takes `--param_task_tag_prefix`.

## Reproduce the experiment

A Google ADK agent on Claude (Gemini also supported), backed by Postgres +
pgvector, traced into a local Langfuse.

### Hypothesis, written before the first run

| ASHWA section | Expected change v1 → v2 | What happened |
|---|---|---|
| Load profile | Same turns; more tool calls, embeddings and SQL per turn; more tokens per turn | As predicted |
| Time model | Embedding + database (retrieval) share of Agent Time rises | **Not as predicted:** retrieval is milliseconds; model time stayed at 99% |
| Top calls | `kb_vector_search` and `embed` climb the ranking | Partly: they were already the top non-model calls; their calls per turn nearly doubled (0.49 → 0.90) |
| Trajectories | Refunds gain a `search_knowledge_base` step before `issue_refund` | As predicted (the search came after `lookup_order`, not before it) |
| Hard-parse analogue | The same policy queries get embedded again and again (repeated `content_hash`) | About 50% across the batch, but 0% within one run: the repeats come from re-running the same scripted conversations |
| Quality | `task_success` roughly unchanged, i.e. the change costs more without helping | **Not as predicted:** 100% → 93.3% |

### Components

| File | Role |
|---|---|
| `docker-compose.yml` | Postgres 17 + pgvector on port 5433 (with `pg_stat_statements`) |
| `seed.py` | Schema, 312 orders (12 scenario orders + 300 background), 24 knowledge-base articles with embeddings (local by default); `reset_phase_state()` |
| `prompts.py` | v1 and v2 prompts; the only difference between the phases |
| `agent.py` | ADK agent: `lookup_order`, `search_knowledge_base`, `issue_refund` (30-day and duplicate checks), `escalate_to_human` |
| `common.py` | Settings (model provider, effort, embeddings); Langfuse spans around every SQL statement (`sql:*`) and embedding call (`embed`, with `content_hash`); choice of ADK trace source |
| `workload.py` | 20 scripted conversations (24 turns); one trace per turn; outcome-based `task_success` score per conversation; interleaved repeats; phases recorded in `runs.json` |
| `ashwa_report.py` | The ASHWA report: runs the section SQL for one window or two; text to the terminal, optional HTML |
| `sql/*.sql` | One query per report section, runnable on their own in `clickhouse-client` |
| `make_figures.py` | Cuts report sections out of `report.html` unchanged and renders them to PNG in `figures/` (headless Chrome) |
| `preflight.py` | Checks credentials, model, a one-line test call, embeddings, Langfuse, app database (and that its embeddings match the configured model) and ClickHouse |
| `requirements.txt` / `requirements.lock` | Pinned direct dependencies / full lock (Python 3.12) |
| `docs/langfuse.docker-compose.override.example.yml` | Local Langfuse settings: pre-creates the project and API keys the experiment expects |

### Model and embedding setup

| Setting (`.env`) | Default | Notes |
|---|---|---|
| `ASHWA_LLM_PROVIDER` | `anthropic` | `gemini` also supported |
| `ANTHROPIC_API_KEY` | (empty) | Get one at <https://platform.claude.com/settings/keys> |
| `ASHWA_MODEL` | `claude-opus-5-5` | Or `claude-sonnet-5-5` / `claude-haiku-4-5` to cut cost; `gemini-2.5-flash` for Gemini |
| `ASHWA_EFFORT` | `medium` | Claude reasoning effort (`low` … `max`); set explicitly so results don't depend on per-model defaults |
| `ASHWA_EMBED_PROVIDER` | `local` | Anthropic has no embeddings API. `local` runs `BAAI/bge-small-en-v1.5` on this machine via fastembed (no key; first use downloads ~130 MB). `gemini` uses Gemini embeddings |

Claude runs through ADK's own `AnthropicLlm` class, which calls the official
Anthropic Python SDK. There is no `temperature` setting for Claude: current
Claude models reject sampling parameters, so run-to-run variation is handled by
repeats, not by forcing determinism. Server-side refusal fallbacks are
deliberately **not** enabled: a fallback would switch models mid-run and break
the like-for-like comparison. A refusal shows up as a failed turn instead.

Measured cost on `claude-opus-5-5` at medium effort: about $0.017 per turn, so
about $2.50 for the full `--repeat 3` experiment (144 turns). Cheaper models
cost less in proportion to their token prices.

### Fairness controls

- **Same starting state.** Every phase begins with `reset_phase_state()`: refunds and tickets are cleared, and scenario order dates are re-based on "now", so refund eligibility cannot drift between phases run on different days.
- **Quality judged on outcomes, not tool calls.** `task_success` checks the database (eligible orders refunded exactly once, ineligible ones not refunded, complaints produce a ticket) and the replies (the right tracking number, warranty length, and so on). A version that calls more tools gets no credit for it.
- **One trace source.** `ASHWA_TRACE_SOURCE=openinference` (default) uses the OpenInference instrumentor, which switches off ADK's built-in tracer itself; `adk` uses ADK's built-in tracing instead. Never both, or LLM and tool calls are counted twice.
- **Interleaved repeats.** `--prompts v1 v2 --repeat 3` runs v1, v2, v1, v2, v1, v2, so slow API periods do not all land on one version. The report shows each metric's range across runs; a change inside both ranges is noise.
- **Harness queries are not traced.** Evaluation and reset SQL use `sql_untraced`, so they do not appear as agent workload.

### Run it

You need Docker, Python 3.12 and a Claude API key.

1. Start a local Langfuse next to this repository, using the example settings
   (they create the project and API keys that `.env.example` expects):
   ```bash
   git clone https://github.com/langfuse/langfuse.git ../langfuse
   cp docs/langfuse.docker-compose.override.example.yml ../langfuse/docker-compose.override.yml
   (cd ../langfuse && docker compose up -d)
   ```
   Langfuse takes two to three minutes to come up at <http://localhost:3000>.
2. Start the agent's database and create the Python environment:
   ```bash
   docker compose up -d --wait
   python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env
   ```
3. Put your Claude API key in `.env` (`ANTHROPIC_API_KEY=...`), then check
   credentials and models before spending anything:
   ```bash
   .venv/bin/python preflight.py --before-seed
   ```
4. Seed the data (24 local embedding calls; no API cost):
   ```bash
   .venv/bin/python seed.py
   ```
5. Full preflight, then a smoke test with 2 conversations (a few cents):
   ```bash
   .venv/bin/python preflight.py && .venv/bin/python workload.py --prompts v1 --limit 2
   ```
6. The experiment: v1 and v2, interleaved, three times each:
   ```bash
   .venv/bin/python workload.py --prompts v1 v2 --repeat 3
   ```
7. The ASHWA compare report for the batch (v1 runs vs v2 runs), with an HTML copy:
   ```bash
   .venv/bin/python ashwa_report.py --batch --html report.html
   ```
   For the latest run on its own: `.venv/bin/python ashwa_report.py --run --html report_latest_run.html`

Your figures will differ from run to run, because the model is not
deterministic and providers change models and prices over time. The direction
of the effects is what should reproduce.

If you hit rate limits (low API tiers, or Gemini's free tier), add `--pause 6`
to step 6. 429 retries are handled automatically and show up as extra model
time, which is itself something ASHWA should be able to spot.

### Where to look in Langfuse

<http://localhost:3000> (log in with the user from the override file) → Agent
Test → filter by tag `prompt:v1` / `prompt:v2`, or by session ID prefix (the
phase id). Each turn shows the ADK agent run, model calls, tool calls, and the
`embed` / `sql:*` spans under each tool. Scores → `task_success` shows which
conversations failed and why (in the comment).

## Known limitations (v0.1)

- Tested with Langfuse 4.54 (self-hosted), ClickHouse 25.12, Google ADK 2.11 and Python 3.12. Other agent frameworks should work if their spans reach Langfuse, but have not been tested.
- Rate-limit retries happen inside one LLM call, so they appear as extra model time, not a separate throttling component.
- No per-phase `pg_stat_statements` snapshots yet; database timing comes from the agent-side `sql:*` spans.
- Model calls are grouped by call name and model. ADK names every model call `call_llm`, so section 3a cannot yet tell one prompt template from another; a prompt-version attribute on the span would give a true SQL_ID equivalent.
- Reply checks are keyword-based: good enough to catch wrong facts, not a judge of tone or helpfulness.
- ADK records a zero-length `(merged tools)` span when the model calls tools in parallel. The SQL excludes it; other frameworks may have similar markers that need excluding.

## Roadmap

Ideas, roughly in order of usefulness. Contributions to any of them are welcome.

- A prompt-version attribute on model spans, to give the report a true SQL_ID.
- A database-side section: `pg_stat_statements` snapshots per window, joined to the agent's SQL spans.
- A throttling component in the time model (rate limits and retries as their own wait class).
- Context-window utilisation and compaction events (the SGA/PGA sizing analogue).
- Multi-agent hand-offs (the RAC analogue): hand-off latency and context passed between agents.
- Worked examples for other frameworks (LangGraph, CrewAI, OpenAI Agents SDK).
- Other trace stores (Arize Phoenix, OpenLIT, SigNoz).

## Contributing

Issues and pull requests are welcome. Two things help most:

- **Try it on a different agent or framework** and open an issue with what worked, what did not, and which span names or types needed special handling.
- **Keep each report section a single readable SQL file.** The aim is that a DBA can open one file and see exactly how a number is calculated.

For a change to how a metric is calculated, include a before-and-after of the
report on the committed experiment data, so reviewers can see the effect.

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

This is a personal project. It is not an official product of, or endorsed by,
any employer of the author. Oracle and AWR are trademarks of Oracle
Corporation; this project is not affiliated with or endorsed by Oracle.
