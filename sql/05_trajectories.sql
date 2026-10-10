-- ASHWA section 5: Trajectories                   (AWR: plan regressions - same SQL_ID, new plan hash value)
-- Question: is the agent taking a different, worse path for the same kind of task?
--
-- Same roots/spans scope as 01_load_profile.sql (see the parameter list there).
-- task_tag_prefix String: trace tag that names the task type (e.g. 'scenario:'); falls back to the root span name.
-- The trajectory signature is the ordered sequence of tool calls in a turn: the agent's execution plan.

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
           arrayMap(x -> x.2, arraySort(groupArrayIf((start_time, call_name), is_tool))) AS steps,
           toFloat64(sum(total_cost))                                                     AS cost
    FROM spans GROUP BY trace_id
),
sc AS (    -- success scores, latest version of each
    SELECT trace_id, value FROM scores
    WHERE project_id = {project:String} AND name = {success_score:String}
      AND trace_id IN (SELECT trace_id FROM roots)
    ORDER BY event_ts DESC LIMIT 1 BY id
),
per_session AS (
    SELECT r.session_id, toNullable(max(sc.value)) AS v
    FROM sc INNER JOIN roots AS r ON sc.trace_id = r.trace_id
    GROUP BY r.session_id
)
SELECT
    if(empty(arrayFilter(t -> startsWith(t, {task_tag_prefix:String}), r.tags)),
       r.root_name,
       substring(arrayFilter(t -> startsWith(t, {task_tag_prefix:String}), r.tags)[1],
                 length({task_tag_prefix:String}) + 1))                      AS task,
    if(empty(p.steps), '(no tools)', arrayStringConcat(p.steps, ' → '))      AS trajectory,
    count()                                                                  AS turns,
    avg(length(p.steps))                                                     AS avg_tool_steps,
    avg(r.turn_ms)                                                           AS avg_turn_ms,
    avg(p.cost)                                                              AS avg_cost_usd,
    avg(ps.v)                                                                AS success_rate
FROM roots AS r
INNER JOIN per_trace AS p ON r.trace_id = p.trace_id
LEFT JOIN per_session AS ps ON r.session_id = ps.session_id
GROUP BY task, trajectory
ORDER BY task, turns DESC
