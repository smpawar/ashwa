-- ASHWA section 3b: Top tools, retrieval and SQL  (AWR: SQL ordered by Executions + Wait Event Histogram)
-- Question: which tools, retrievers, SQL statements and embeddings take the time, and how bad is the tail?
--
-- Same roots/spans scope as 01_load_profile.sql (see the parameter list there).
-- Elapsed is inclusive: a tool's time includes the SQL and embedding spans inside it.

WITH
roots AS (
    SELECT trace_id, any(session_id) AS session_id, any(tags) AS tags, any(name) AS root_name,
           any(dateDiff('millisecond', start_time, coalesce(end_time, start_time))) AS turn_ms
    FROM (SELECT * FROM events_core
          WHERE project_id = {project:String} AND parent_span_id = ''
            AND start_time >= {start:DateTime64(6)} AND start_time < {end:DateTime64(6)}
            AND (length({session_prefixes:Array(String)}) = 0
                 OR arrayExists(p -> startsWith(session_id, p), {session_prefixes:Array(String)}))
            AND (length({root_names:Array(String)}) = 0 OR has({root_names:Array(String)}, name))
          ORDER BY event_ts DESC LIMIT 1 BY span_id)
    GROUP BY trace_id
),
spans AS (
    SELECT trace_id, span_id, parent_span_id, name, type, level, start_time, provided_model_name,
           dateDiff('millisecond', start_time, coalesce(end_time, start_time)) AS ms,
           usage_details, total_cost, input, metadata_names, metadata_values,
           replaceRegexpOne(name, '^execute_tool ', '') AS call_name,
           (type = 'TOOL' OR startsWith(name, 'execute_tool') OR has({tool_names:Array(String)}, name))
             AND call_name != '(merged tools)' AS is_tool   -- ADK marker for parallel tool calls, not a call
    FROM events_core
    WHERE project_id = {project:String} AND trace_id IN (SELECT trace_id FROM roots)
    ORDER BY event_ts DESC
    LIMIT 1 BY span_id
)
SELECT component,
       call_name,
       count()                                              AS executions,
       count() / (SELECT count() FROM roots)                AS per_turn,
       sum(ms) / 1000                                       AS total_s,
       quantilesExact(0.5, 0.95, 0.99)(ms)                  AS ms_p50_p95_p99,
       countIf(level = 'ERROR') / count()                   AS error_rate
FROM (
    SELECT *,
           multiIf(type = 'EMBEDDING',                               'embedding',
                   startsWith(name, 'sql:') OR type = 'RETRIEVER',   'retrieval / database',
                                                                     'tool') AS component
    FROM spans
    WHERE is_tool OR type IN ('EMBEDDING', 'RETRIEVER') OR startsWith(name, 'sql:')
)
GROUP BY component, call_name
ORDER BY total_s DESC
LIMIT 15
