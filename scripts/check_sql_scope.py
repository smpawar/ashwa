"""Check that every report section uses the same scope.

All files in sql/ start with the same `roots` and `spans` CTEs, which decide
which traces and spans a report window covers. If one file drifts, its section
silently measures a different set of turns from the rest of the report.

    python scripts/check_sql_scope.py
"""

import sys
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def scope(path: Path) -> str:
    """The text of the roots and spans CTEs, from `roots AS (` to the end of `spans`."""
    lines = path.read_text().splitlines()
    start = lines.index("roots AS (")
    spans = lines.index("spans AS (", start)
    end = next(i for i in range(spans, len(lines)) if lines[i].startswith(")"))
    return "\n".join(lines[start:end])


def main() -> int:
    files = sorted(SQL_DIR.glob("*.sql"))
    if not files:
        print(f"no SQL files found in {SQL_DIR}")
        return 1
    reference = scope(files[0])
    drifted = [f.name for f in files[1:] if scope(f) != reference]
    if drifted:
        print(f"roots/spans CTEs differ from {files[0].name} in: {', '.join(drifted)}")
        return 1
    print(f"{len(files)} SQL files share the same roots/spans scope")
    return 0


if __name__ == "__main__":
    sys.exit(main())
