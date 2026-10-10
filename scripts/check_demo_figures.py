"""Check that the demo report still shows the figures published in the README.

    python ashwa_report.py --demo > demo-report.txt
    python scripts/check_demo_figures.py demo-report.txt

If a change to the SQL or the report moves one of these on purpose, update the
list here, the README table and the committed report*.html together.
"""

import sys

# (metric label, prompt v1, prompt v2) as printed in the compare report
PUBLISHED = [
    ("Steps per turn (LLM + tool calls)", "3.653", "3.931"),
    ("Tool calls per turn", "1.375", "1.736"),
    ("Embeddings", "0.486", "0.903"),
    ("Cost per turn ($)", "0.0165", "0.0184"),
    ("Cost per successful conversation ($)", "0.0198", "0.0237"),
    ("Task success rate", "100.0%", "93.3%"),
]


def main() -> int:
    lines = [line.split() for line in open(sys.argv[1])]
    missing = []
    for label, v1, v2 in PUBLISHED:
        want = label.split() + [v1, v2]
        if not any(line[:len(want)] == want for line in lines):
            missing.append(f"{label}: expected {v1} -> {v2}")
    if missing:
        print("Demo report no longer shows the published figures:\n  " + "\n  ".join(missing))
        return 1
    print(f"{len(PUBLISHED)} published figures reproduced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
