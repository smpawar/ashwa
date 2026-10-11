"""Run the scripted IT-helpdesk conversations and trace them to Langfuse.

    .venv/bin/python examples/helpdesk_team/run.py --limit 2             # smoke test
    .venv/bin/python examples/helpdesk_team/run.py --variants a b        # team vs single agent, for a compare report
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import run  # noqa: E402

import agent  # noqa: E402

NOT_FOUND = ["no account", "not find", "n't find", "not found", "no user", "n't exist", "not exist"]
EXPIRED = ["expired", "out of warranty", "no longer", "not under", "ended", "not covered"]


def unlocked(email):
    return lambda: None if not agent.USERS[email]["locked"] else f"{email} is still locked"


def reset_sent(email):
    return lambda: None if agent.USERS[email]["reset_sent"] else f"no reset link sent to {email}"


def ticketed(tag, expected=True):
    def check():
        found = any(t["asset_tag"] == tag for t in agent.TICKETS)
        if found != expected:
            return f"{tag}: expected {'a' if expected else 'no'} repair ticket"
    return check


def both(*checks):
    return lambda: "; ".join(f for f in (c() for c in checks) if f) or None


# (scenario, user, turns, expectations); see harness.evaluate for the checks.
SCENARIOS = [
    ("account", "maria", ["I'm locked out of my account. My email is maria@example.com."],
     {"state": unlocked("maria@example.com")}),
    ("account", "raj", ["I forgot my password. My email is raj@example.com."],
     {"state": reset_sent("raj@example.com")}),
    ("account", "ghost", ["Can you unlock ghost@example.com?"],
     {"reply_all": [NOT_FOUND]}),
    ("device", "maria", ["The screen on my laptop LT-1001 keeps flickering. Please get it repaired."],
     {"state": ticketed("LT-1001")}),
    ("device", "raj", ["Is LT-1002 still under warranty?"],
     {"reply_all": [EXPIRED]}),
    ("device", "raj", ["The battery in LT-1002 is dead. Open a repair ticket."],
     {"state": ticketed("LT-1002", expected=False), "reply_all": [["replace"]]}),
    ("device", "raj", ["What model is asset LT-1004?", "When does its warranty end?"],
     {"reply_all": [["thinkpad"], ["2027"]]}),
    ("mixed", "sam", ["I'm locked out (sam@example.com) and my laptop LT-1003 won't charge. Can you sort out both?"],
     {"state": both(unlocked("sam@example.com"), ticketed("LT-1003"))}),
]

VARIANTS = {"a": "coordinator + account and device specialists", "b": "one agent with all six tools"}

if __name__ == "__main__":
    run("helpdesk_team", "helpdesk_turn", agent.build_agent, SCENARIOS, VARIANTS, reset=agent.reset)
