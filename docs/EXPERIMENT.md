# Reproduce the experiment

The experiment ASHWA was built on: a customer-support agent runs the same 20
scripted conversations under two versions of its instructions, and the ASHWA
compare report shows what changed. Results are summarised in the
[README](../README.md#what-it-found-one-harmless-sentence).

All commands run from the repository root.

A Google ADK agent on Claude (Gemini also supported), backed by Postgres +
pgvector, traced into a local Langfuse.

## Hypothesis, written before the first run

| ASHWA section | Expected change v1 → v2 | What happened |
|---|---|---|
| Load profile | Same turns; more tool calls, embeddings and SQL per turn; more tokens per turn | As predicted |
| Time model | Embedding + database (retrieval) share of Agent Time rises | **Not as predicted:** retrieval is milliseconds; model time stayed at 99% |
| Top calls | `kb_vector_search` and `embed` climb the ranking | Partly: they were already the top non-model calls; their calls per turn nearly doubled (0.49 → 0.90) |
| Trajectories | Refunds gain a `search_knowledge_base` step before `issue_refund` | As predicted (the search came after `lookup_order`, not before it) |
| Hard-parse analogue | The same policy queries get embedded again and again (repeated `content_hash`) | About 50% across the batch, but 0% within one run: the repeats come from re-running the same scripted conversations |
| Quality | `task_success` roughly unchanged, i.e. the change costs more without helping | **Not as predicted:** 100% → 93.3% |

## Components

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
repeats, not by forcing determinism. Server-side refusal fallbacks are
deliberately **not** enabled: a fallback would switch models mid-run and break
the like-for-like comparison. A refusal shows up as a failed turn instead.

Measured cost on `claude-opus-5-5` at medium effort: about $0.017 per turn, so
about $2.50 for the full `--repeat 3` experiment (144 turns). Cheaper models
cost less in proportion to their token prices.

## Fairness controls

- **Same starting state.** Every phase begins with `reset_phase_state()`: refunds and tickets are cleared, and scenario order dates are re-based on "now", so refund eligibility cannot drift between phases run on different days.
- **Quality judged on outcomes, not tool calls.** `task_success` checks the database (eligible orders refunded exactly once, ineligible ones not refunded, complaints produce a ticket) and the replies (the right tracking number, warranty length, and so on). A version that calls more tools gets no credit for it.
- **One trace source.** `ASHWA_TRACE_SOURCE=openinference` (default) uses the OpenInference instrumentor, which switches off ADK's built-in tracer itself; `adk` uses ADK's built-in tracing instead. Never both, or LLM and tool calls are counted twice.
- **Interleaved repeats.** `--prompts v1 v2 --repeat 3` runs v1, v2, v1, v2, v1, v2, so slow API periods do not all land on one version. The report shows each metric's range across runs; a change inside both ranges is noise.
- **Harness queries are not traced.** Evaluation and reset SQL use `sql_untraced`, so they do not appear as agent workload.

## Run it

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

## Where to look in Langfuse

<http://localhost:3000> (log in with the user from the override file) → Agent
Test → filter by tag `prompt:v1` / `prompt:v2`, or by session ID prefix (the
phase id). Each turn shows the ADK agent run, model calls, tool calls, and the
`embed` / `sql:*` spans under each tool. Scores → `task_success` shows which
conversations failed and why (in the comment).
