-- ASHWA section 4a: Efficiency ratios             (AWR: Instance Efficiency Percentages)
-- Question: is the system doing its work economically? A sanity check, not a diagnosis.
--
-- Same roots/spans scope as 01_load_profile.sql (see the parameter list there).
-- reembedding_rate is the hard-parse analogue: embeddings of text already embedded in this window.
-- prompt_cache_hit_rate is NULL when the provider reports no cache token counts.

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
),
per_trace AS (
    SELECT trace_id,
           countIf(type = 'GENERATION')                              AS llm,
           countIf(is_tool)                                          AS tools,
           sumIf(usage_details['total'], type = 'GENERATION')        AS tokens,
           toFloat64(sum(total_cost))                                AS cost
    FROM spans GROUP BY trace_id
)
SELECT
    (SELECT avg(llm + tools) FROM per_trace)                                         AS steps_per_turn,
    (SELECT avg(llm) FROM per_trace)                                                 AS llm_calls_per_turn,
    (SELECT avg(tools) FROM per_trace)                                               AS tool_calls_per_turn,
    (SELECT avg(tokens) FROM per_trace)                                              AS tokens_per_turn,
    (SELECT avg(cost) FROM per_trace)                                                AS cost_per_turn_usd,
    (SELECT countIf(level = 'ERROR') / count() FROM spans WHERE is_tool)             AS tool_error_rate,
    (SELECT 1 - uniqExact(trace_id, call_name, cityHash64(input)) / count()
       FROM spans WHERE is_tool)                                                     AS duplicate_tool_call_rate,
    (SELECT 1 - uniqExact(metadata_values[indexOf(metadata_names, 'content_hash')]) / count()
       FROM spans WHERE type = 'EMBEDDING')                                          AS reembedding_rate,
    (SELECT if(countIf(arrayExists(k -> positionCaseInsensitive(k, 'cache') > 0, mapKeys(usage_details))) = 0,
               NULL,   -- provider reports no cache counts
               sum(arraySum(mapValues(mapFilter(
                   (k, v) -> positionCaseInsensitive(k, 'input_cached') > 0 OR positionCaseInsensitive(k, 'cache_read') > 0,
                   usage_details)))) / sum(usage_details['input']))
       FROM spans WHERE type = 'GENERATION')                                         AS prompt_cache_hit_rate
