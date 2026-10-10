"""Export one experiment batch from Langfuse's ClickHouse into demo/ for `ashwa_report.py --demo`.

Writes demo/events_core.parquet, demo/scores.parquet and demo/runs.json. Only
what the report SQL reads is exported:
  - the latest version of each span of the batch's traces, and only the columns the SQL uses
  - `input` is replaced by its hash (the SQL only compares inputs to find duplicate tool calls)
  - metadata is reduced to the `content_hash` entry (the only one the SQL reads)
So the files hold no prompt or reply text, and no host or process details.

    .venv/bin/python scripts/export_demo_data.py            # latest batch in runs.json
    .venv/bin/python scripts/export_demo_data.py BATCH_ID
"""

import json
import os
import sys

import clickhouse_connect
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(HERE, "demo")
load_dotenv(os.path.join(HERE, ".env"))

TRACES = """
    SELECT DISTINCT trace_id FROM events_core
    WHERE project_id = {project:String} AND parent_span_id = ''
      AND arrayExists(p -> startsWith(session_id, p), {prefixes:Array(String)})
"""

EVENTS = f"""
    SELECT project_id, trace_id, span_id, parent_span_id, start_time, end_time, name, type, session_id, tags,
           level, provided_model_name, usage_details, total_cost,
           input_hash AS input, kept_names AS metadata_names, kept_values AS metadata_values, event_ts
    FROM (
        SELECT project_id, trace_id, span_id, parent_span_id, start_time, end_time, name, type, session_id,
               tags, level, provided_model_name, usage_details, total_cost, event_ts,
               toString(cityHash64(input)) AS input_hash,
               arrayFilter(n -> n = 'content_hash', metadata_names) AS kept_names,
               arrayFilter((v, n) -> n = 'content_hash', metadata_values, metadata_names) AS kept_values
        FROM events_core
        WHERE project_id = {{project:String}} AND trace_id IN ({TRACES})
        ORDER BY event_ts DESC LIMIT 1 BY span_id
    )
"""

SCORES = f"""
    SELECT id, project_id, trace_id, name, value, event_ts
    FROM scores
    WHERE project_id = {{project:String}} AND trace_id IN ({TRACES})
    ORDER BY event_ts DESC LIMIT 1 BY id
"""


def main():
    runs = json.load(open(os.path.join(HERE, "runs.json")))
    batch_id = sys.argv[1] if len(sys.argv) > 1 else runs[-1]["batch"]
    batch = [r for r in runs if r.get("batch") == batch_id]
    if not batch:
        raise SystemExit(f"Batch {batch_id} not found in runs.json.")
    params = dict(project=os.environ.get("ASHWA_PROJECT", "local-project"),
                  prefixes=[r["phase_id"] + "-" for r in batch])
    ch = clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"), port=int(os.environ.get("CLICKHOUSE_PORT", 8123)),
        username=os.environ.get("CLICKHOUSE_USER", "clickhouse"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", "clickhouse"))
    os.makedirs(DEMO, exist_ok=True)
    for name, query in (("events_core", EVENTS), ("scores", SCORES)):
        path = os.path.join(DEMO, f"{name}.parquet")
        data = ch.raw_query(query, parameters=params, fmt="Parquet")
        open(path, "wb").write(data)
        print(f"{path}: {len(data) / 1024:,.0f} KB")
    json.dump(batch, open(os.path.join(DEMO, "runs.json"), "w"), indent=2)
    print(f"{os.path.join(DEMO, 'runs.json')}: {len(batch)} runs of {batch_id}")


if __name__ == "__main__":
    main()
