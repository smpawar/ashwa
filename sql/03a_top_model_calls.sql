-- ASHWA section 3a: Top model calls               (AWR: SQL ordered by Elapsed Time)
-- Question: which model calls consume the time, tokens and money?
--
-- Same roots/spans scope as 01_load_profile.sql (see the parameter list there).
-- The (call name, model) pair plays the role of SQL_ID.

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
SELECT call_name,
       provided_model_name                                  AS model,
       count()                                              AS executions,
       sum(ms) / 1000                                       AS total_s,
       quantilesExact(0.5, 0.95, 0.99)(ms)                  AS ms_p50_p95_p99,
       avg(in_tok)                                          AS avg_input_tokens,
       avg(out_tok)                                         AS avg_output_tokens,
       toFloat64(sum(total_cost))                           AS cost_usd,
       sum(ms) / (SELECT sum(turn_ms) FROM roots)           AS share_of_agent_time
FROM (
    SELECT *,
           usage_details['input']  AS in_tok,     -- includes cached tokens
           usage_details['output'] AS out_tok     -- includes reasoning tokens
    FROM spans WHERE type = 'GENERATION'
)
GROUP BY call_name, model
ORDER BY total_s DESC
LIMIT 10
