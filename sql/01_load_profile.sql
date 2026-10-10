-- ASHWA section 1: Header and load profile          (AWR: report header + Load Profile)
-- Question: how busy was the system in this window, and is Agent Time high relative to elapsed time?
--
-- Every ASHWA query starts with the same two CTEs:
--   roots: one row per agent turn (the top-level span of each trace) in the window
--   spans: every span of those turns, latest version only
-- Parameters (clickhouse-client: --param_<name>=<value>):
--   project String, start DateTime64(6), end DateTime64(6), elapsed_s Float64,
--   session_prefixes Array(String)  ([] = all sessions)
--   root_names Array(String)        ([] = any top-level span counts as a turn)
--   tool_names Array(String)        (extra span names to treat as tool calls)

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
SELECT
    {elapsed_s:Float64}                                                   AS elapsed_s,
    (SELECT count() FROM roots)                                           AS turns,
    (SELECT uniqExactIf(session_id, session_id != '') FROM roots)         AS sessions,
    (SELECT sum(turn_ms) FROM roots) / 1000                               AS agent_time_s,
    agent_time_s / elapsed_s                                              AS avg_active_agents,
    (SELECT quantilesExact(0.5, 0.95, 0.99)(turn_ms) FROM roots)          AS turn_ms_p50_p95_p99,
    countIf(type = 'GENERATION')                                          AS llm_calls,
    countIf(is_tool)                                                      AS tool_calls,
    countIf(type = 'EMBEDDING')                                           AS embeddings,
    countIf(startsWith(name, 'sql:'))                                     AS sql_statements,
    sumIf(usage_details['input'], type = 'GENERATION')                   AS input_tokens,   -- includes cached tokens
    sumIf(usage_details['output'], type = 'GENERATION')                  AS output_tokens,  -- includes reasoning tokens
    toFloat64(sum(total_cost))                                            AS cost_usd,
    countIf(level = 'ERROR')                                              AS errors
FROM spans
