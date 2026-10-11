# Example agents

Two small Google ADK agents to point ASHWA at. Neither needs a database: the
tools read in-memory data, so the only services required are Langfuse (for the
traces) and a model API key.

| Example | Shape | Variant `a` | Variant `b` |
|---|---|---|---|
| [`trip_planner/`](trip_planner) | One agent, four tools (weather, flights, hotels, currency) | Base prompt | Base prompt + "always check the weather first" |
| [`helpdesk_team/`](helpdesk_team) | Coordinator that hands off to an account agent and a device agent (three tools each) | The three-agent team | One agent holding all six tools |

Each runs 8 scripted conversations (9 turns) and scores every conversation
with a `task_success` score from checks on the replies and on the data the
tools changed.

## Run

From the repository root, with Langfuse running and `.env` filled in (see
[`docs/EXPERIMENT.md`](../docs/EXPERIMENT.md)). Each run calls the model, so it
costs money: a few cents for a smoke test.

```bash
.venv/bin/python examples/trip_planner/run.py --limit 2          # smoke test
.venv/bin/python examples/trip_planner/run.py --variants a b     # both variants
.venv/bin/python examples/helpdesk_team/run.py --variants a b
```

`--repeat N` runs the variants N times, interleaved. When a run ends it prints
the `ashwa_report.py` commands for it: one single-window report per variant
and, with two variants, a compare report. For example:

```bash
.venv/bin/python ashwa_report.py --root-name trip_turn --from "2026-10-11 14:00:00" --to "2026-10-11 14:05:00" \
  --prefix trip_planner-a-20261011T140000 --compare-prefix trip_planner-b-20261011T140130 \
  --html examples/trip_planner/report_a_vs_b.html
```

## On a fresh VM

`vm_setup.sh` does the whole setup for Gemini on RHEL 10 (or Debian/Ubuntu):
Docker, a local Langfuse, the Python environment and `.env`. It makes no model
calls.

```bash
git clone --branch examples/adk-agents https://github.com/smpawar/ashwa && cd ashwa
GEMINI_AUTH=vertex bash examples/vm_setup.sh                      # the VM's service account
GEMINI_AUTH=apikey GOOGLE_API_KEY=... bash examples/vm_setup.sh   # or a Gemini API key
```

## What to look for

- **trip_planner**: section 5 (trajectories) for the `trip_plan` and `flights`
  tasks, where variant `b` should add `get_weather` steps; section 4a for tool
  calls and cost per turn.
- **helpdesk_team**: hand-offs appear as `transfer_to_agent` tool calls in
  sections 3b and 5, and as extra model calls per turn in section 4a. The
  `mixed` task needs both specialists.

## Layout

`harness.py` holds what the two examples share: model settings, tracing, the
run loop and scoring. Each example is an `agent.py` (tools, prompts,
`build_agent`) and a `run.py` (the scripted conversations and their checks).
