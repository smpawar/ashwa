"""Drive the same scripted customer workload through the agent.

    .venv/bin/python workload.py --prompts v1 --limit 2             # smoke test
    .venv/bin/python workload.py --prompts v1 v2 --repeat 3          # the experiment

With several prompts and --repeat, phases run interleaved (v1, v2, v1, v2, ...)
so slow API periods do not all land on one version. Every phase starts from
the same database state (seed.reset_phase_state).

Each turn becomes one Langfuse trace (session = conversation). Each
conversation gets a task_success score from checks against the database and
the agent's replies, not from which tools were called, so a version that
calls more tools does not score better by construction. Phase windows are
appended to runs.json for ashwa_report.py --batch.
"""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone

from common import EFFORT, EMBED_MODEL, LLM_PROVIDER, MODEL, TRACE_SOURCE, instrument_adk, langfuse, sql_untraced

instrument_adk()

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from agent import build_agent  # noqa: E402
from seed import reset_phase_state  # noqa: E402

RUNS_FILE = os.path.join(os.path.dirname(__file__), "runs.json")

# Expectations:
#   refunded:      orders that must end with exactly one refund
#   not_refunded:  orders that must end with no refund
#   escalated:     a ticket must be created during the conversation
#   reply_all:     every group must match; a group matches if the replies
#                  (lower-cased, all turns joined) contain any of its strings
SCENARIOS = [
    ("order_status", "alice", ["Hi, where is my order 100101?"],
     {"reply_all": [["shipped", "transit", "on its way", "on the way"]]}),
    ("order_status", "bob", ["Order 100102 says shipped but tracking hasn't moved in over a week.",
                             "Can you just send me the tracking number again?"],
     {"reply_all": [["1z10010288"]]}),
    ("order_status", "carol", ["When will order 100106 ship?"],
     {"reply_all": [["processing", "not yet shipped", "hasn't shipped", "has not shipped"]]}),
    ("order_status", "dave", ["Status of 100108 please"],
     {"reply_all": [["shipped", "transit", "on its way", "on the way"]]}),
    ("order_status", "erin", ["Has my order 100112 shipped yet?", "Which carrier is it with?"],
     {"reply_all": [["ups"]]}),
    ("refund", "frank", ["I want a refund for order 100103, the blender arrived cracked."],
     {"refunded": ["100103"]}),
    ("refund", "grace", ["Please refund order 100104, I changed my mind.", "How long until I see the money?"],
     {"refunded": ["100104"], "reply_all": [["5-7", "5 to 7", "five to seven", "5–7"]]}),
    ("refund", "heidi", ["Can I get a refund on 100105? It stopped working."],
     {"not_refunded": ["100105"], "reply_all": [["warranty"]]}),
    ("refund", "ivan", ["Refund 100107 please, it's the wrong colour."],
     {"refunded": ["100107"]}),
    ("refund", "judy", ["I need to return order 100109 for a refund."],
     {"refunded": ["100109"]}),
    ("refund", "ken", ["Cancel and refund 100110."],
     {"not_refunded": ["100110"], "reply_all": [["cancel"]]}),
    ("product", "alice", ["Is the X200 blender dishwasher safe?"],
     {"reply_all": [["jar", "lid"]]}),
    ("product", "bob", ["Does the AirChef fryer work in the UK on 220V?"],
     {"reply_all": [["120"]]}),
    ("product", "carol", ["What's the warranty on the Q5 stand mixer?", "And on the SteamPro iron?"],
     {"reply_all": [["2-year", "2 year", "two-year", "two year"], ["1-year", "1 year", "one-year", "one year"]]}),
    ("product", "dave", ["How much space does the FrostLine mini fridge need behind it?"],
     {"reply_all": [["2 inch", "two inch", '2"', "2-inch"]]}),
    ("policy", "erin", ["Do you price match?"], {"reply_all": [["14"]]}),
    ("policy", "frank", ["Can I pay with PayPal?"], {"reply_all": [["paypal"]]}),
    ("policy", "grace", ["What are your support hours?"], {"reply_all": [["8am", "8 am", "8:00", "8 a.m"]]}),
    ("complaint", "heidi", ["This is the third time I'm contacting you about order 100111. I want a manager."],
     {"escalated": True}),
    ("complaint", "ivan", ["Order 100102 is lost and nobody helps. I'm filing a chargeback unless I get a manager now."],
     {"escalated": True}),
]


def ticket_count() -> int:
    return sql_untraced("SELECT count(*) AS n FROM tickets", fetch="one")["n"]


def evaluate(expect: dict, replies: list[str], tickets_before: int) -> list[str]:
    """Return the list of failed checks (empty list = success)."""
    failures = []
    for oid in expect.get("refunded", []):
        n = sql_untraced("SELECT count(*) AS n FROM refunds WHERE order_id = %s", (oid,), fetch="one")["n"]
        if n != 1:
            failures.append(f"order {oid}: expected 1 refund, found {n}")
    for oid in expect.get("not_refunded", []):
        n = sql_untraced("SELECT count(*) AS n FROM refunds WHERE order_id = %s", (oid,), fetch="one")["n"]
        if n:
            failures.append(f"order {oid}: should not be refunded, found {n} refund(s)")
    if expect.get("escalated") and ticket_count() <= tickets_before:
        failures.append("expected escalation, no ticket created")
    text = " ".join(replies).lower()
    for group in expect.get("reply_all", []):
        if not any(s.lower() in text for s in group):
            failures.append(f"reply missing any of {group}")
    return failures


async def run_turn(runner, prompt_version, phase_id, scenario, user, session_id, text):
    with langfuse.start_as_current_observation(as_type="agent", name="support_turn", input=text) as turn:
        turn.update_trace(
            name="support_turn", user_id=user, session_id=session_id,
            tags=["ashwa-experiment", f"prompt:{prompt_version}", f"scenario:{scenario}"],
            metadata={"prompt_version": prompt_version, "phase_id": phase_id, "scenario": scenario,
                      "model": MODEL, "llm_provider": LLM_PROVIDER, "effort": EFFORT,
                      "embed_model": EMBED_MODEL, "trace_source": TRACE_SOURCE},
        )
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


async def run_phase(prompt_version: str, batch: str, pause: float, limit: int | None) -> dict:
    phase_id = f"{prompt_version}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    reset_phase_state()
    agent = build_agent(prompt_version)
    runner = InMemoryRunner(agent=agent, app_name="ashwa_support")
    started = datetime.now(timezone.utc)
    print(f"\nPhase {phase_id}: prompt {prompt_version}, model {MODEL} (effort {EFFORT}), "
          f"embeddings {EMBED_MODEL}, trace source {TRACE_SOURCE}")

    passed = 0
    scenarios = SCENARIOS[:limit]
    for i, (scenario, user, turns, expect) in enumerate(scenarios, 1):
        session_id = f"{phase_id}-s{i:02d}"
        await runner.session_service.create_session(app_name="ashwa_support", user_id=user, session_id=session_id)
        tickets_before = ticket_count()
        replies = []
        for text in turns:
            t0 = time.time()
            called, reply, trace_id = await run_turn(runner, prompt_version, phase_id, scenario, user, session_id, text)
            replies.append(reply)
            print(f"  s{i:02d} {scenario:<12} {time.time() - t0:5.1f}s  tools={called}")
            if pause:
                await asyncio.sleep(pause)
        failures = evaluate(expect, replies, tickets_before)
        passed += not failures
        if failures:
            print(f"       FAILED: {'; '.join(failures)}")
        langfuse.create_score(trace_id=trace_id, name="task_success", value=0 if failures else 1,
                              data_type="BOOLEAN", comment="; ".join(failures) or None)

    langfuse.flush()
    ended = datetime.now(timezone.utc)
    record = {"phase_id": phase_id, "batch": batch, "prompt_version": prompt_version, "model": MODEL,
              "llm_provider": LLM_PROVIDER, "effort": EFFORT, "embed_model": EMBED_MODEL,
              "trace_source": TRACE_SOURCE, "start": started.isoformat(), "end": ended.isoformat(),
              "sessions": len(scenarios), "task_success": f"{passed}/{len(scenarios)}"}
    runs = json.load(open(RUNS_FILE)) if os.path.exists(RUNS_FILE) else []
    runs.append(record)
    json.dump(runs, open(RUNS_FILE, "w"), indent=2)
    print(f"Done: {phase_id}  task_success {passed}/{len(scenarios)}  ({(ended - started).seconds}s)")
    return record


async def main(prompts: list[str], repeat: int, pause: float, limit: int | None):
    batch = f"batch-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    for r in range(repeat):
        for p in prompts:
            await run_phase(p, batch, pause, limit)
    print(f"\nBatch {batch} complete. Report with: .venv/bin/python ashwa_report.py --batch {batch} --html report.html")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", nargs="+", choices=["v1", "v2"], required=True)
    ap.add_argument("--repeat", type=int, default=1, help="run the prompt sequence N times, interleaved")
    ap.add_argument("--pause", type=float, default=0, help="seconds between turns (free-tier rate limits)")
    ap.add_argument("--limit", type=int, default=None, help="run only the first N conversations (smoke test)")
    a = ap.parse_args()
    asyncio.run(main(a.prompts, a.repeat, a.pause, a.limit))
