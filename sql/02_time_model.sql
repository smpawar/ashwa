-- ASHWA section 2: Time model and top waits      (AWR: Time Model Statistics + Top 10 Foreground Events)
-- Question: where is Agent Time going: model, tools, retrieval, or the framework itself?
--
-- Same roots/spans scope as 01_load_profile.sql (see the parameter list there).
-- Self time = a span's duration minus its children's, so nested spans are not counted twice.
-- Orchestration = self time of everything that is not a model, tool, retrieval or embedding span.

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
child AS (SELECT parent_span_id AS pid, sum(ms) AS child_ms FROM spans GROUP BY parent_span_id),
classified AS (
    SELECT s.ms, greatest(s.ms - c.child_ms, 0) AS self_ms,
           multiIf(s.level IN ('ERROR', 'WARNING'),                       'error / retry',
                   s.type = 'GENERATION',                                  'model (LLM)',
                   s.type = 'EMBEDDING',                                   'embedding',
                   startsWith(s.name, 'sql:') OR s.type = 'RETRIEVER',     'retrieval / database',
                   s.is_tool,                                              'tool code',
                                                                           'orchestration') AS component
    FROM spans AS s LEFT JOIN child AS c ON s.span_id = c.pid
)
SELECT component,
       count()                                              AS spans,
       sum(self_ms) / 1000                                  AS time_s,
       sum(self_ms) / (SELECT sum(self_ms) FROM classified) AS share_of_agent_time
FROM classified
GROUP BY component
ORDER BY time_s DESC
