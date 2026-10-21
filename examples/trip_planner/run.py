"""Run the scripted trip-planning conversations and trace them to Langfuse.

    .venv/bin/python examples/trip_planner/run.py --limit 2              # smoke test
    .venv/bin/python examples/trip_planner/run.py --variants a b         # both prompts, for a compare report
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import run  # noqa: E402

from agent import build_agent  # noqa: E402

NOT_FOUND = ["no flight", "not find", "n't find", "not found", "no results", "not available", "unable", "n't have"]

# (scenario, user, turns, expectations); see harness.evaluate for the checks.
SCENARIOS = [
    ("weather", "alice", ["What's the weather like in Lisbon this week?"],
     {"reply_all": [["22"]]}),
    ("weather", "bob", ["Is it warmer in Tokyo or Paris right now?"],
     {"reply_all": [["tokyo"], ["19"]]}),
    ("flights", "carol", ["Find me the cheapest flight from Orlando to Lisbon."],
     {"reply_all": [["612"]]}),
    ("flights", "dave", ["Any flights from Orlando to Atlantis?"],
     {"reply_all": [NOT_FOUND]}),
    ("hotels", "erin", ["I need a hotel in Tokyo under $150 a night."],
     {"reply_all": [["sakura inn"]]}),
    ("currency", "frank", ["How much is 200 US dollars in euros?"],
     {"reply_all": [["184"]]}),
    ("trip_plan", "grace", ["Plan a 3-night trip from Orlando to Tokyo: the cheapest flight and the cheapest hotel "
                            "under $200 a night. What is the total?",
                            "What is that hotel's nightly price in yen?"],
     {"reply_all": [["980"], ["sakura inn"], ["1,340", "1340"], ["18,000", "18000"]]}),
    ("trip_plan", "heidi", ["I'm flying from Orlando to Paris next week. What's the cheapest flight, "
                            "and should I pack an umbrella?"],
     {"reply_all": [["655"], ["rain", "umbrella"]]}),
]

VARIANTS = {"a": "base prompt", "b": "base prompt + 'always check the weather first'"}

if __name__ == "__main__":
    run("trip_planner", "trip_turn", build_agent, SCENARIOS, VARIANTS)
