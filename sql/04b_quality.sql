-- ASHWA section 4b: Quality                       (no AWR equivalent: a database that returns wrong rows is a bug)
-- Question: did the agent get the job done? Fast and cheap is worthless if the answer is wrong.
--
-- Same roots/spans scope as 01_load_profile.sql (see the parameter list there).
-- success_score String: name of a BOOLEAN/NUMERIC score (0..1). A conversation counts once (max score).

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
sc AS (    -- success scores, latest version of each
    SELECT trace_id, value FROM scores
    WHERE project_id = {project:String} AND name = {success_score:String}
      AND trace_id IN (SELECT trace_id FROM roots)
    ORDER BY event_ts DESC LIMIT 1 BY id
),
per_unit AS (
    SELECT if(r.session_id = '', r.trace_id, r.session_id) AS unit, max(sc.value) AS v
    FROM sc INNER JOIN roots AS r ON sc.trace_id = r.trace_id
    GROUP BY unit
)
SELECT count() AS scored_conversations, sum(v) AS successes, avg(v) AS success_rate
FROM per_unit
