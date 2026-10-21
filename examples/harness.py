"""Shared runner for the example agents: model settings, tracing, scoring.

Each example supplies an agent builder and a list of scripted conversations;
this module runs them, sends one Langfuse trace per turn (session =
conversation) and prints the ashwa_report.py commands for the run.

Settings come from the .env at the repository root (model, API key, Langfuse).
The examples need no database: their tools read in-memory data.
"""

import argparse
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO, ".env"))

from langfuse import Langfuse  # noqa: E402
from openinference.instrumentation.google_adk import GoogleADKInstrumentor  # noqa: E402

langfuse = Langfuse()
# The instrumentor disables ADK's built-in tracer itself, so steps are counted once.
GoogleADKInstrumentor().instrument()

from google.adk.models import Gemini  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

LLM_PROVIDER = os.environ.get("ASHWA_LLM_PROVIDER", "anthropic")
MODEL = os.environ.get("ASHWA_MODEL") or {"anthropic": "claude-opus-5-5", "gemini": "gemini-2.5-flash"}[LLM_PROVIDER]
EFFORT = os.environ.get("ASHWA_EFFORT", "medium")


def model_and_config():
    """Same model settings as the reference experiment (agent.py)."""
    if LLM_PROVIDER == "anthropic":
        # Imported here so a Gemini-only setup does not need the anthropic package.
        from anthropic import AsyncAnthropic
        from google.adk.models import AnthropicGenerateContentConfig
        from google.adk.models.anthropic_llm import AnthropicLlm
        model = AnthropicLlm(model=MODEL, max_tokens=16000, client=AsyncAnthropic(max_retries=6))
        return model, AnthropicGenerateContentConfig(effort=EFFORT)
    model = Gemini(model=MODEL, retry_options=types.HttpRetryOptions(attempts=6, initial_delay=2, max_delay=60))
    return model, types.GenerateContentConfig(temperature=0)


def evaluate(expect: dict, replies: list[str]) -> list[str]:
    """Return the list of failed checks (empty list = success).

    reply_all: every group must match; a group matches if the replies
               (lower-cased, all turns joined) contain any of its strings
    state:     a function returning a failure message, or None if the
               example's data ended in the expected state
    """
    failures = []
    text = " ".join(replies).lower()
    for group in expect.get("reply_all", []):
        if not any(s.lower() in text for s in group):
            failures.append(f"reply missing any of {group}")
    if "state" in expect:
        failure = expect["state"]()
        if failure:
            failures.append(failure)
    return failures


async def run_turn(runner, root_name, tags, metadata, user, session_id, text):
    with langfuse.start_as_current_observation(as_type="agent", name=root_name, input=text) as turn:
        turn.update_trace(name=root_name, user_id=user, session_id=session_id, tags=tags, metadata=metadata)
        called, reply = [], ""
        msg = types.Content(role="user", parts=[types.Part(text=text)])
        async for event in runner.run_async(user_id=user, session_id=session_id, new_message=msg):
            called += [fc.name for fc in event.get_function_calls()]
            if event.is_final_response() and event.content and event.content.parts:
                reply = "".join(p.text or "" for p in event.content.parts)
        turn.update(output=reply, metadata={"tools_called": called})
        turn.update_trace(output=reply)
        trace_id = langfuse.get_current_trace_id()
    return called, reply, trace_id


async def run_phase(example, root_name, build_agent, scenarios, reset, variant, pause):
    phase_id = f"{example}-{variant}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    runner = InMemoryRunner(agent=build_agent(variant), app_name=f"ashwa_{example}")
    print(f"\nPhase {phase_id}: variant {variant}, model {MODEL}")

    passed = 0
    for i, (scenario, user, turns, expect) in enumerate(scenarios, 1):
        if reset:
            reset()     # every conversation starts from the same data
        session_id = f"{phase_id}-s{i:02d}"
        await runner.session_service.create_session(app_name=f"ashwa_{example}", user_id=user, session_id=session_id)
        tags = [f"example:{example}", f"variant:{variant}", f"scenario:{scenario}"]
        metadata = {"example": example, "variant": variant, "phase_id": phase_id, "scenario": scenario,
                    "model": MODEL, "llm_provider": LLM_PROVIDER}
        replies = []
        for text in turns:
            t0 = time.time()
            called, reply, trace_id = await run_turn(runner, root_name, tags, metadata, user, session_id, text)
            replies.append(reply)
            print(f"  s{i:02d} {scenario:<12} {time.time() - t0:5.1f}s  tools={called}")
            if pause:
                await asyncio.sleep(pause)
        failures = evaluate(expect, replies)
        passed += not failures
        if failures:
            print(f"       FAILED: {'; '.join(failures)}")
        langfuse.create_score(trace_id=trace_id, name="task_success", value=0 if failures else 1,
                              data_type="BOOLEAN", comment="; ".join(failures) or None)

    langfuse.flush()
    print(f"Done: {phase_id}  task_success {passed}/{len(scenarios)}")
    return phase_id


def report_commands(example, root_name, phases, start, end):
    """The ashwa_report.py commands for this run (times are UTC)."""
    window = f'--root-name {root_name} --from "{start:%Y-%m-%d %H:%M:%S}" --to "{end:%Y-%m-%d %H:%M:%S}"'
    out = f"examples/{example}"
    lines = ["\nReports (run from the repository root, after Langfuse has ingested the traces):"]
    for variant, ids in phases.items():
        prefixes = " ".join(f"--prefix {p}" for p in ids)
        lines.append(f"  .venv/bin/python ashwa_report.py {window} {prefixes} --html {out}/report_{variant}.html")
    if len(phases) == 2:
        (va, a), (vb, b) = phases.items()
        lines.append(f"  .venv/bin/python ashwa_report.py {window} {' '.join(f'--prefix {p}' for p in a)} "
                     f"{' '.join(f'--compare-prefix {p}' for p in b)} --html {out}/report_{va}_vs_{vb}.html")
    return "\n".join(lines)


async def _main(example, root_name, build_agent, scenarios, reset, o):
    start = datetime.now(timezone.utc)
    phases = {v: [] for v in o.variants}
    for _ in range(o.repeat):       # interleaved, so a slow API period does not land on one variant
        for v in o.variants:
            phases[v].append(await run_phase(example, root_name, build_agent, scenarios[:o.limit], reset, v, o.pause))
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    print(report_commands(example, root_name, phases, start, end))


def run(example: str, root_name: str, build_agent, scenarios: list, variants: dict, reset=None):
    ap = argparse.ArgumentParser(description="; ".join(f"{k} = {v}" for k, v in variants.items()))
    ap.add_argument("--variants", nargs="+", choices=list(variants), default=[next(iter(variants))])
    ap.add_argument("--repeat", type=int, default=1, help="run the variant sequence N times, interleaved")
    ap.add_argument("--pause", type=float, default=0, help="seconds between turns (free-tier rate limits)")
    ap.add_argument("--limit", type=int, default=None, help="run only the first N conversations (smoke test)")
    asyncio.run(_main(example, root_name, build_agent, scenarios, reset, ap.parse_args()))
