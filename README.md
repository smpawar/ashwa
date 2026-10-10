# ASHWA (Agentic System Health & Workload Analysis) experiment: a "harmless" prompt change

A real Google ADK agent on Claude (Gemini also supported), backed by Postgres +
pgvector, traced into the local Langfuse. The same scripted workload runs twice: once with prompt **v1**, once
with **v2**, which adds one well-intentioned sentence:

> *Accuracy matters: before answering or taking any action, check the knowledge
> base for the relevant policy so your answer always reflects current policy.*

Same model, same tools, same 20 conversations. The question for the article:
**can an AWR-style reading of the workload show what that sentence did?**

## Hypothesis (write it down before running)

| ASHWA section | Expected change v1 → v2 |
|---|---|
| Load profile | Same turns; more tool calls, embeddings and SQL per turn; more tokens per turn |
| Time model | Embedding + database (retrieval) share of Agent Time rises |
| Top calls | `kb_vector_search` and `embed` climb the ranking |
| Trajectories | `refund`: `lookup_order → issue_refund` becomes `search_knowledge_base → lookup_order → issue_refund` |
| Hard-parse analogue | The same policy queries get embedded again and again (repeated `content_hash`) |
| Quality | `task_success` roughly unchanged, i.e. the change costs more without helping |

## Components

| File | Role |
|---|---|
| `docker-compose.yml` | Postgres 17 + pgvector on port 5433 (with `pg_stat_statements`) |
| `seed.py` | Schema, 312 orders (12 scenario orders + 300 background), 24 knowledge-base articles with embeddings (local by default); `reset_phase_state()` |
| `prompts.py` | v1 and v2 prompts; the only difference between the phases |
| `agent.py` | ADK agent: `lookup_order`, `search_knowledge_base`, `issue_refund` (30-day and duplicate checks), `escalate_to_human` |
| `common.py` | Settings (model provider, effort, embeddings); Langfuse spans around every SQL statement (`sql:*`) and embedding call (`embed`, with `content_hash`); choice of ADK trace source |
| `workload.py` | 20 scripted conversations (24 turns); one trace per turn; outcome-based `task_success` score per conversation; interleaved repeats; phases recorded in `runs.json` |
| `ashwa_report.py` | The ASHWA report: runs the section SQL for one window (like `awrrpt.sql`) or two (like `awrddrpt.sql`); text to the terminal, optional HTML |
| `sql/*.sql` | One query per report section, runnable on their own in `clickhouse-client` |
| `make_figures.py` | Cuts report sections out of `report.html` unchanged and renders them to PNG in `figures/` (headless Chrome) |
| `preflight.py` | Checks credentials, model, a one-line test call, embeddings, Langfuse, app database (and that its embeddings match the configured model) and ClickHouse |
| `requirements.txt` / `requirements.lock` | Pinned direct dependencies / full lock (Python 3.12) |

## Model and embedding setup

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
repeats (below), not by forcing determinism. Server-side refusal fallbacks are
deliberately **not** enabled: a fallback would switch models mid-run and break
the like-for-like comparison. A refusal shows up as a failed turn instead.

Rough cost on `claude-opus-5-5` (estimate, not measured): a few cents per turn,
so a few dollars for the full `--repeat 3` experiment (144 turns). Sonnet 5.5 is
about half that, Haiku 4.5 about a quarter. The first real run gives the actual
figure in Langfuse.

## Fairness controls

- **Same starting state.** Every phase begins with `reset_phase_state()`: refunds and tickets are cleared, and scenario order dates are re-based on "now", so refund eligibility cannot drift between phases run on different days.
- **Quality judged on outcomes, not tool calls.** `task_success` checks the database (eligible orders refunded exactly once, ineligible ones not refunded, complaints produce a ticket) and the replies (the right tracking number, warranty length, and so on). A version that calls more tools gets no credit for it.
- **One trace source.** `ASHWA_TRACE_SOURCE=openinference` (default) uses the OpenInference instrumentor, which switches off ADK's built-in tracer itself; `adk` uses ADK's built-in tracing instead. Never both, or LLM and tool calls are counted twice.
- **Interleaved repeats.** `--prompts v1 v2 --repeat 3` runs v1, v2, v1, v2, v1, v2, so slow API periods do not all land on one version. The report shows each metric's range across runs; a change inside both ranges is noise.
- **Harness queries are not traced.** Evaluation and reset SQL use `sql_untraced`, so they do not appear as agent workload.

## Run it

1. Put your Claude API key in `.env` (`ANTHROPIC_API_KEY=...`).
2. Make sure Langfuse (`../langfuse`) and the app database are up:
   ```bash
   docker compose up -d --wait
   ```
3. Check credentials and models before spending anything:
   ```bash
   .venv/bin/python preflight.py --before-seed
   ```
4. Seed the data (24 local embedding calls; no API cost):
   ```bash
   .venv/bin/python seed.py
   ```
5. Full preflight, then a smoke test with 2 conversations:
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

If you hit rate limits (low API tiers, or Gemini's free tier), add `--pause 6` to step 6. 429 retries are handled automatically and show up as
extra model time, which is itself something ASHWA should be able to spot.

To reproduce elsewhere: Python 3.12, then `pip install -r requirements.txt`
(or `requirements.lock` for the exact environment).

## The ASHWA report

`ashwa_report.py` lays the sections out in AWR reading order. Each section is a
plain SQL file over Langfuse's ClickHouse tables:

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

```bash
# The latest experiment run on its own (or --run PHASE_ID from runs.json)
.venv/bin/python ashwa_report.py --run --html report_latest_run.html
# One window, like awrrpt.sql (times are UTC)
.venv/bin/python ashwa_report.py --from "2026-10-09 14:00" --to "2026-10-09 15:00"
# Two windows, like awrddrpt.sql
.venv/bin/python ashwa_report.py --from "..." --to "..." --compare-from "..." --compare-to "..."
```

For another agentic system, override the experiment defaults: `--root-name`
(the top-level span of a turn), `--tool` (span names to count as tool calls),
`--success-score` (a 0–1 score name) and `--task-tag` (the tag prefix naming the
task type).

Any section can also be run directly, the way a DBA runs a script:

```bash
cd ../langfuse && docker compose exec -T clickhouse clickhouse-client --password clickhouse \
  --param_project=local-project --param_start='2026-10-09 14:00:00' --param_end='2026-10-09 15:00:00' \
  --param_elapsed_s=3600 --param_session_prefixes="[]" --param_root_names="['support_turn']" \
  --param_tool_names="[]" --format PrettyCompact < ../ashwa-experiment/sql/02_time_model.sql
```

## Where to look in Langfuse

http://localhost:3000 → Agent Test → filter by tag `prompt:v1` / `prompt:v2`, or by
session ID prefix (the phase id). Each turn shows the ADK agent run, model calls,
tool calls, and the `embed` / `sql:*` spans under each tool. Scores → `task_success`
shows which conversations failed and why (in the comment).

## Known limitations (v0.1)

- Rate-limit retries happen inside one LLM call, so they appear as extra model time, not a separate throttling component.
- No per-phase `pg_stat_statements` snapshots yet; database timing comes from the agent-side `sql:*` spans.
- Model calls are grouped by call name and model. ADK names every model call `call_llm`, so section 3a cannot yet tell one prompt template from another; a prompt-version attribute on the span would give a true SQL_ID equivalent.
- Reply checks are keyword-based: good enough to catch wrong facts, not a judge of tone or helpfulness.
