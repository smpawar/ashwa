"""ASHWA report: Agentic System Health & Workload Analysis.

An AWR-style report over agent traces stored in Langfuse's ClickHouse. Each
section is a plain SQL file in sql/, runnable on its own with clickhouse-client;
this script runs them for one or two windows and lays the results out in AWR
reading order.

Single window (like awrrpt.sql):
    .venv/bin/python ashwa_report.py --from "2026-10-09 10:00" --to "2026-10-09 11:00"

Compare periods (like awrddrpt.sql):
    .venv/bin/python ashwa_report.py --from ... --to ... --compare-from ... --compare-to ...

One run of the experiment in this folder (a phase in runs.json; default: the latest):
    .venv/bin/python ashwa_report.py --run --html report.html

The experiment in this folder (v1 vs v2 phases of a batch in runs.json):
    .venv/bin/python ashwa_report.py --batch                # latest batch
    .venv/bin/python ashwa_report.py --batch BATCH_ID --html report.html

Times are UTC. --prefix / --compare-prefix restrict a window to sessions whose
id starts with a prefix. Defaults for --root-name, --tool, --success-score and
--task-tag match this experiment; override them for other agentic systems.
"""

import argparse
import html
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import clickhouse_connect
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

SECTIONS = ["01_load_profile", "02_time_model", "03a_top_model_calls", "03b_top_tools",
            "04a_efficiency", "04b_quality", "05_trajectories"]


@dataclass
class Scope:
    label: str
    start: datetime
    end: datetime
    elapsed_s: float
    prefixes: list[str] = field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------- data

def client():
    return clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"), port=int(os.environ.get("CLICKHOUSE_PORT", 8123)),
        username=os.environ.get("CLICKHOUSE_USER", "clickhouse"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", "clickhouse"))


def collect(ch, scope: Scope, opts) -> dict:
    params = dict(project=opts.project, start=scope.start, end=scope.end, elapsed_s=scope.elapsed_s,
                  session_prefixes=scope.prefixes, root_names=opts.root_name, tool_names=opts.tool,
                  success_score=opts.success_score, task_tag_prefix=opts.task_tag)
    out = {}
    for name in SECTIONS:
        res = ch.query(open(os.path.join(HERE, "sql", f"{name}.sql")).read(), parameters=params)
        out[name] = [dict(zip(res.column_names, r)) for r in res.result_rows]
    return out


def num(v):
    """None/NaN -> None, Decimal -> float."""
    if v is None:
        return None
    v = float(v)
    return None if math.isnan(v) else v


# ---------------------------------------------------------------- metrics

# (key, label, kind, better) ; better: "low", "high" or None (neutral)
def load_profile_rows(d):
    lp = d["01_load_profile"][0]
    turns = lp["turns"] or 0
    el = lp["elapsed_s"] or 0
    rows = []
    for key, label, kind in [("agent_time_s", "Agent Time (s)", "s"), ("llm_calls", "LLM calls", "n"),
                             ("tool_calls", "Tool calls", "n"), ("embeddings", "Embeddings", "n"),
                             ("sql_statements", "SQL statements", "n"), ("input_tokens", "Input tokens", "n"),
                             ("output_tokens", "Output tokens", "n"), ("cost_usd", "Cost ($)", "usd"),
                             ("errors", "Errors", "n")]:
        total = num(lp[key]) or 0
        rows.append(dict(label=label, kind=kind, total=total,
                         per_s=total / el if el else None, per_turn=total / turns if turns else None))
    return lp, rows


def headline(d):
    lp = d["01_load_profile"][0]
    p = lp["turn_ms_p50_p95_p99"] or [None, None, None]
    return [("Turns", lp["turns"], "n", None), ("Sessions", lp["sessions"], "n", None),
            ("Elapsed (s)", lp["elapsed_s"], "s", None), ("Agent Time (s)", lp["agent_time_s"], "s", "low"),
            ("Average active agents", lp["avg_active_agents"], "f", None),
            ("Turn latency p50 (ms)", p[0], "ms", "low"), ("Turn latency p95 (ms)", p[1], "ms", "low"),
            ("Turn latency p99 (ms)", p[2], "ms", "low")]


def efficiency(d):
    e = d["04a_efficiency"][0]
    q = d["04b_quality"][0] if d["04b_quality"] else {}
    lp = d["01_load_profile"][0]
    successes = num(q.get("successes"))
    return [("Steps per turn (LLM + tool calls)", e["steps_per_turn"], "f", "low"),
            ("LLM calls per turn", e["llm_calls_per_turn"], "f", "low"),
            ("Tool calls per turn", e["tool_calls_per_turn"], "f", "low"),
            ("Tokens per turn", e["tokens_per_turn"], "n", "low"),
            ("Cost per turn ($)", e["cost_per_turn_usd"], "usd", "low"),
            ("Tool error rate", e["tool_error_rate"], "pct", "low"),
            ("Duplicate tool calls (same tool, same input, same turn)", e["duplicate_tool_call_rate"], "pct", "low"),
            ("Re-embedding rate (hard-parse analogue)", e["reembedding_rate"], "pct", "low"),
            ("Prompt-cache hit rate (soft-parse analogue)", e["prompt_cache_hit_rate"], "pct", "high"),
            ("Task success rate", q.get("success_rate"), "pct", "high"),
            ("Cost per successful conversation ($)",
             (num(lp["cost_usd"]) / successes) if successes else None, "usd", "low")]


def fmt(v, kind):
    v = num(v) if not isinstance(v, list) else v
    if v is None:
        return "n/a"
    if kind == "n":
        return f"{v:,.0f}" if abs(v) >= 10 or v == int(v) else f"{v:,.1f}"
    if kind == "s":
        return f"{v:,.1f}"
    if kind == "ms":
        return f"{v:,.0f}"
    if kind == "usd":
        return "0" if v == 0 else f"{v:,.6f}" if abs(v) < 0.01 else f"{v:,.4f}" if abs(v) < 1 else f"{v:,.2f}"
    if kind == "pct":
        return f"{v * 100:.1f}%"
    return f"{v:,.3f}" if abs(v) < 10 else f"{v:,.1f}"


def change(a, b):
    a, b = num(a), num(b)
    if a is None or b is None:
        return None
    if a == 0:
        return None if b == 0 else math.inf
    return (b - a) / abs(a)


def fmt_change(c):
    if c is None:
        return ""
    if c == math.inf:
        return "new"
    return f"{c * 100:+.0f}%"


def verdict(c, better):
    if c is None or better is None or abs(c) < 0.05:
        return ""
    worse = (c > 0) == (better == "low")
    return "worse" if worse else "better"


# ---------------------------------------------------------------- text output

def table(headers, rows, aligns=None):
    aligns = aligns or ["<"] + [">"] * (len(headers) - 1)
    cols = list(zip(*([headers] + rows))) if rows else [[h] for h in headers]
    widths = [max(len(str(x)) for x in c) for c in cols]
    line = lambda r: "  " + "  ".join(f"{str(x):{a}{w}}" for x, a, w in zip(r, aligns, widths))
    sep = "  " + "  ".join("-" * w for w in widths)
    return "\n".join([line(headers), sep] + [line(r) for r in rows])


def title(t):
    return f"\n{t}\n{'=' * len(t)}"


def ms3(v):
    return " / ".join(fmt(x, "ms") for x in v) if v else "n/a"


def text_single(s: Scope, d) -> str:
    out = [title("ASHWA Report: Agentic System Health & Workload Analysis"),
           f"  Window   {s.start:%Y-%m-%d %H:%M:%S} to {s.end:%Y-%m-%d %H:%M:%S} UTC   {s.note}"]
    out.append(table(["Header", "Value"], [[k, fmt(v, kind)] for k, v, kind, _ in headline(d)]))

    lp, rows = load_profile_rows(d)
    out += [title("1. Load profile"),
            table(["Metric", "Total", "Per second", "Per turn"],
                  [[r["label"], fmt(r["total"], r["kind"]), fmt(r["per_s"], r["kind"] if r["kind"] == "usd" else "f"),
                   fmt(r["per_turn"], r["kind"] if r["kind"] == "usd" else "f")] for r in rows])]

    out += [title("2. Time model (self time; nested spans not double counted)"),
            table(["Component", "Time (s)", "% Agent Time", "Spans"],
                  [[r["component"], fmt(r["time_s"], "s"), fmt(r["share_of_agent_time"], "pct"), r["spans"]]
                   for r in d["02_time_model"]])]

    out += [title("3a. Top model calls (call + model = SQL_ID)"),
            table(["Call", "Model", "Execs", "Total (s)", "p50/p95/p99 (ms)", "Avg in", "Avg out", "Cost ($)", "% Agent Time"],
                  [[r["call_name"], r["model"] or "-", r["executions"], fmt(r["total_s"], "s"), ms3(r["ms_p50_p95_p99"]),
                    fmt(r["avg_input_tokens"], "n"), fmt(r["avg_output_tokens"], "n"), fmt(r["cost_usd"], "usd"),
                    fmt(r["share_of_agent_time"], "pct")] for r in d["03a_top_model_calls"]])]

    out += [title("3b. Top tools, retrieval and SQL (elapsed is inclusive)"),
            table(["Component", "Call", "Execs", "Per turn", "Total (s)", "p50/p95/p99 (ms)", "Error rate"],
                  [[r["component"], r["call_name"], r["executions"], fmt(r["per_turn"], "f"), fmt(r["total_s"], "s"),
                    ms3(r["ms_p50_p95_p99"]), fmt(r["error_rate"], "pct")] for r in d["03b_top_tools"]])]

    out += [title("4. Efficiency ratios and quality"),
            table(["Ratio", "Value"], [[k, fmt(v, kind)] for k, v, kind, _ in efficiency(d)])]

    out += [title("5. Trajectories (tool sequence per turn = execution plan)"),
            table(["Task", "Trajectory", "Turns", "Avg steps", "Avg turn (ms)", "Avg cost ($)", "Success"],
                  [[r["task"], r["trajectory"], r["turns"], fmt(r["avg_tool_steps"], "f"), fmt(r["avg_turn_ms"], "ms"),
                    fmt(r["avg_cost_usd"], "usd"), fmt(r["success_rate"], "pct")] for r in d["05_trajectories"]])]
    return "\n".join(out)


def compare_metrics(da, db):
    """All comparable scalar metrics: (section, label, a, b, kind, better)."""
    m = []
    for (k, a, kind, better), (_, b, _, _) in zip(headline(da), headline(db)):
        m.append(("Header", k, a, b, kind, better))
    _, ra = load_profile_rows(da)
    _, rb = load_profile_rows(db)
    for x, y in zip(ra, rb):
        m.append(("1. Load profile (per turn)", x["label"], x["per_turn"], y["per_turn"],
                  "usd" if x["kind"] == "usd" else "f", "low"))
    tm_a = {r["component"]: r for r in da["02_time_model"]}
    tm_b = {r["component"]: r for r in db["02_time_model"]}
    for c in sorted(set(tm_a) | set(tm_b), key=lambda c: -num(tm_b.get(c, tm_a.get(c))["time_s"])):
        m.append(("2. Time model (% Agent Time)", c, (tm_a.get(c) or {}).get("share_of_agent_time", 0.0),
                  (tm_b.get(c) or {}).get("share_of_agent_time", 0.0), "pct", None))
    for (k, a, kind, better), (_, b, _, _) in zip(efficiency(da), efficiency(db)):
        m.append(("4. Efficiency and quality", k, a, b, kind, better))
    return m


def biggest(metrics, limit=15):
    """Metrics that moved >= 5%, largest first; the same pair of values is listed once."""
    seen, out = set(), []
    rows = [(sec, label, a, b, kind, better, change(a, b)) for sec, label, a, b, kind, better in metrics
            if sec != "Header" and change(a, b) is not None and abs(change(a, b)) >= 0.05]
    rows.sort(key=lambda x: -1e9 if x[6] == math.inf else -abs(x[6]))
    for r in rows:
        key = (round(num(r[2]), 9), round(num(r[3]), 9))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out[:limit]


SPREAD_METRICS = [("Agent Time per turn (s)", lambda d: num(d["01_load_profile"][0]["agent_time_s"]) / max(1, d["01_load_profile"][0]["turns"]), "f", "low"),
                  ("Turn latency p95 (ms)", lambda d: (d["01_load_profile"][0]["turn_ms_p50_p95_p99"] or [None] * 3)[1], "ms", "low"),
                  ("Steps per turn", lambda d: d["04a_efficiency"][0]["steps_per_turn"], "f", "low"),
                  ("Tool calls per turn", lambda d: d["04a_efficiency"][0]["tool_calls_per_turn"], "f", "low"),
                  ("Tokens per turn", lambda d: d["04a_efficiency"][0]["tokens_per_turn"], "n", "low"),
                  ("Cost per turn ($)", lambda d: d["04a_efficiency"][0]["cost_per_turn_usd"], "usd", "low"),
                  ("Task success rate", lambda d: (d["04b_quality"][0] if d["04b_quality"] else {}).get("success_rate"), "pct", "high")]


def spread(ch, runs, opts):
    """Per metric: (label, kind, A values, B values, verdict). Ranges that overlap are noise."""
    if not runs or (len(runs[0]) < 2 and len(runs[1]) < 2):
        return None
    data = [[collect(ch, sc, opts) for sc in side] for side in runs]
    rows = []
    for label, fn, kind, _ in SPREAD_METRICS:
        va = [x for x in (num(fn(d)) for d in data[0]) if x is not None]
        vb = [x for x in (num(fn(d)) for d in data[1]) if x is not None]
        if not va or not vb:
            continue
        overlap = min(vb) <= max(va) and min(va) <= max(vb)
        rows.append((label, kind, va, vb, "within noise" if overlap else "real change"))
    return rows


def rng(vals, kind):
    return fmt(vals[0], kind) if len(vals) == 1 else f"{fmt(min(vals), kind)} – {fmt(max(vals), kind)}"


def col(scope, side):
    return side if scope.label == side else f"{side}: {scope.label}"


def trajectory_changes(da, db):
    """Per task: trajectories that appeared or disappeared, and avg steps."""
    def by_task(d):
        t = {}
        for r in d["05_trajectories"]:
            t.setdefault(r["task"], {})[r["trajectory"]] = r
        return t
    ta, tb = by_task(da), by_task(db)
    rows = []
    for task in sorted(set(ta) | set(tb)):
        a, b = ta.get(task, {}), tb.get(task, {})
        steps = lambda d: (sum(r["avg_tool_steps"] * r["turns"] for r in d.values()) / sum(r["turns"] for r in d.values())) if d else None
        new, gone = sorted(set(b) - set(a)), sorted(set(a) - set(b))
        rows.append(dict(task=task, steps_a=steps(a), steps_b=steps(b), new=new, gone=gone,
                         a=sorted(a.values(), key=lambda r: -r["turns"]), b=sorted(b.values(), key=lambda r: -r["turns"])))
    return rows


def text_compare(sa: Scope, da, sb: Scope, db, spread_rows=None) -> str:
    out = [title("ASHWA Compare Periods Report: Agentic System Health & Workload Analysis"),
           f"  {col(sa, 'A'):<14} {sa.start:%Y-%m-%d %H:%M:%S} to {sa.end:%Y-%m-%d %H:%M:%S} UTC  {sa.note}",
           f"  {col(sb, 'B'):<14} {sb.start:%Y-%m-%d %H:%M:%S} to {sb.end:%Y-%m-%d %H:%M:%S} UTC  {sb.note}"]
    metrics = compare_metrics(da, db)
    heads = ["Metric", col(sa, "A"), col(sb, "B"), "Change", ""]

    def metric_section(sec, heading):
        rows = [[label, fmt(a, kind), fmt(b, kind), fmt_change(change(a, b)), verdict(change(a, b), better)]
                for s_, label, a, b, kind, better in metrics if s_ == sec]
        return [title(heading), table(heads, rows)]

    if spread_rows:
        out.append(title("Run-to-run spread (each run separately; overlapping ranges mean the change is noise)"))
        out.append(table(["Metric", f"{col(sa, 'A')} range", f"{col(sb, 'B')} range", "Verdict"],
                         [[label, rng(va, kind), rng(vb, kind), v] for label, kind, va, vb, v in spread_rows]))
    out += metric_section("Header", "Header")
    out += metric_section("1. Load profile (per turn)", "1. Load profile (per turn)")
    out += metric_section("2. Time model (% Agent Time)", "2. Time model (% Agent Time, self time)")
    out.append(title("3. Top calls (ranked by B, A for reference)"))
    ref = {(r["call_name"], r["model"]): r for r in da["03a_top_model_calls"]}
    out.append(table(["Call", "Model", "Execs A", "Execs B", "Total (s) A", "Total (s) B", "p95 ms A", "p95 ms B"],
                     [[r["call_name"], r["model"] or "-", (ref.get((r["call_name"], r["model"])) or {}).get("executions", 0),
                       r["executions"], fmt((ref.get((r["call_name"], r["model"])) or {}).get("total_s"), "s"),
                       fmt(r["total_s"], "s"),
                       fmt(((ref.get((r["call_name"], r["model"])) or {}).get("ms_p50_p95_p99") or [None] * 3)[1], "ms"),
                       fmt(r["ms_p50_p95_p99"][1], "ms")] for r in db["03a_top_model_calls"]]))
    ref = {(r["component"], r["call_name"]): r for r in da["03b_top_tools"]}
    out.append("")
    out.append(table(["Component", "Call", "Per turn A", "Per turn B", "Total (s) A", "Total (s) B", "p95 ms A", "p95 ms B"],
                     [[r["component"], r["call_name"], fmt((ref.get((r["component"], r["call_name"])) or {}).get("per_turn"), "f"),
                       fmt(r["per_turn"], "f"), fmt((ref.get((r["component"], r["call_name"])) or {}).get("total_s"), "s"),
                       fmt(r["total_s"], "s"),
                       fmt(((ref.get((r["component"], r["call_name"])) or {}).get("ms_p50_p95_p99") or [None] * 3)[1], "ms"),
                       fmt(r["ms_p50_p95_p99"][1], "ms")] for r in db["03b_top_tools"]]))

    out += metric_section("4. Efficiency and quality", "4. Efficiency ratios and quality")

    out.append(title("5. Trajectories by task (new plans in B are the regressions to read first)"))
    for t in trajectory_changes(da, db):
        flag = "  <-- NEW TRAJECTORY" if t["new"] else ""
        out.append(f"  {t['task']}: avg tool steps {fmt(t['steps_a'], 'f')} -> {fmt(t['steps_b'], 'f')}{flag}")
        for lbl, rows in (("A", t["a"]), ("B", t["b"])):
            for r in rows:
                mark = " (new)" if lbl == "B" and r["trajectory"] in t["new"] else ""
                out.append(f"      {lbl}  {r['turns']:>3}x  {r['trajectory']}{mark}   "
                           f"avg {fmt(r['avg_turn_ms'], 'ms')} ms, ${fmt(r['avg_cost_usd'], 'usd')}, success {fmt(r['success_rate'], 'pct')}")

    out.append(title("6. Biggest changes (|change| >= 5%, largest first)"))
    out.append(table(["Section", "Metric", "A", "B", "Change", ""],
                     [[sec.split(".", 1)[0], label, fmt(a, kind), fmt(b, kind), fmt_change(c), verdict(c, better)]
                      for sec, label, a, b, kind, better, c in biggest(metrics)]))
    return "\n".join(out)


# ---------------------------------------------------------------- HTML output

CSS = """
:root{--bg:#fbfaf7;--fg:#1f2328;--muted:#5d6670;--line:#d9dde3;--head:#1f3a5f;--headfg:#fff;--alt:#f2f4f7;
--worse:#b42318;--better:#1a7f37;--accent:#0f6e6e;--chip:#e7f3f2}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15181c;--fg:#e6e8eb;--muted:#9aa4ae;--line:#2c323a;
--head:#24405f;--headfg:#e6e8eb;--alt:#1b1f24;--worse:#ff7b72;--better:#56d364;--accent:#5fc7c0;--chip:#1d3332}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:24px 16px}
main{max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px;color:var(--accent)} h2{font-size:17px;margin:28px 0 8px;border-bottom:2px solid var(--line);padding-bottom:4px}
.sub{color:var(--muted);margin:0 0 16px} .scope{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:var(--muted)}
.wrap{overflow-x:auto} table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0 4px}
th{background:var(--head);color:var(--headfg);text-align:left;font-weight:600;padding:6px 8px;white-space:nowrap}
td{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top} tr:nth-child(even) td{background:var(--alt)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap} .worse{color:var(--worse);font-weight:600}
.better{color:var(--better);font-weight:600} .new{background:var(--chip);color:var(--accent);border-radius:4px;padding:0 6px;font-size:12px}
.traj{font-family:ui-monospace,Menlo,monospace;font-size:12.5px} .note{color:var(--muted);font-size:12.5px}
"""


def h_table(headers, rows, num_cols=None):
    num_cols = set(range(1, len(headers))) if num_cols is None else set(num_cols)
    th = "".join(f"<th{' class=\"n\"' if i in num_cols else ''}>{html.escape(str(x))}</th>" for i, x in enumerate(headers))
    trs = []
    for r in rows:
        tds = []
        for i, x in enumerate(r):
            cls = ' class="n"' if i in num_cols else ""
            tds.append(f"<td{cls}>{x}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="wrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>'


def e(x):
    return html.escape(str(x))


def html_report(sa, da, sb=None, db=None, spread_rows=None) -> str:
    parts = []
    if sb is None:
        parts.append(f"<h1>ASHWA Report</h1><p class='sub'>Agentic System Health &amp; Workload Analysis</p>"
                     f"<p class='scope'>{sa.start:%Y-%m-%d %H:%M:%S} to {sa.end:%Y-%m-%d %H:%M:%S} UTC {e(sa.note)}</p>")
        parts.append("<h2>Header</h2>" + h_table(["", "Value"], [[e(k), fmt(v, kind)] for k, v, kind, _ in headline(da)]))
        _, rows = load_profile_rows(da)
        parts.append("<h2>1. Load profile</h2>" + h_table(["Metric", "Total", "Per second", "Per turn"],
                     [[e(r["label"]), fmt(r["total"], r["kind"]), fmt(r["per_s"], r["kind"] if r["kind"] == "usd" else "f"),
                      fmt(r["per_turn"], r["kind"] if r["kind"] == "usd" else "f")] for r in rows]))
        parts.append("<h2>2. Time model</h2>" + h_table(["Component", "Time (s)", "% Agent Time", "Spans"],
                     [[e(r["component"]), fmt(r["time_s"], "s"), fmt(r["share_of_agent_time"], "pct"), r["spans"]] for r in da["02_time_model"]]))
        parts.append("<h2>3a. Top model calls</h2>" + h_table(
            ["Call", "Model", "Execs", "Total (s)", "p50/p95/p99 (ms)", "Avg in", "Avg out", "Cost ($)", "% Agent Time"],
            [[e(r["call_name"]), e(r["model"] or "-"), r["executions"], fmt(r["total_s"], "s"), ms3(r["ms_p50_p95_p99"]),
              fmt(r["avg_input_tokens"], "n"), fmt(r["avg_output_tokens"], "n"), fmt(r["cost_usd"], "usd"),
              fmt(r["share_of_agent_time"], "pct")] for r in da["03a_top_model_calls"]], num_cols=range(2, 9)))
        parts.append("<h2>3b. Top tools, retrieval and SQL</h2>" + h_table(
            ["Component", "Call", "Execs", "Per turn", "Total (s)", "p50/p95/p99 (ms)", "Error rate"],
            [[e(r["component"]), e(r["call_name"]), r["executions"], fmt(r["per_turn"], "f"), fmt(r["total_s"], "s"),
              ms3(r["ms_p50_p95_p99"]), fmt(r["error_rate"], "pct")] for r in da["03b_top_tools"]], num_cols=range(2, 7)))
        parts.append("<h2>4. Efficiency ratios and quality</h2>" + h_table(["Ratio", "Value"],
                     [[e(k), fmt(v, kind)] for k, v, kind, _ in efficiency(da)]))
        parts.append("<h2>5. Trajectories</h2>" + h_table(
            ["Task", "Trajectory", "Turns", "Avg steps", "Avg turn (ms)", "Avg cost ($)", "Success"],
            [[e(r["task"]), f"<span class='traj'>{e(r['trajectory'])}</span>", r["turns"], fmt(r["avg_tool_steps"], "f"),
              fmt(r["avg_turn_ms"], "ms"), fmt(r["avg_cost_usd"], "usd"), fmt(r["success_rate"], "pct")]
             for r in da["05_trajectories"]], num_cols=range(2, 7)))
        name = "ASHWA Report"
    else:
        parts.append(f"<h1>ASHWA Compare Periods Report</h1><p class='sub'>Agentic System Health &amp; Workload Analysis</p>"
                     f"<p class='scope'>{e(col(sa, 'A'))}: {sa.start:%Y-%m-%d %H:%M:%S} to {sa.end:%Y-%m-%d %H:%M:%S} UTC {e(sa.note)}<br>"
                     f"{e(col(sb, 'B'))}: {sb.start:%Y-%m-%d %H:%M:%S} to {sb.end:%Y-%m-%d %H:%M:%S} UTC {e(sb.note)}</p>")
        metrics = compare_metrics(da, db)
        big = biggest(metrics)
        cell = lambda c, better: f"<span class='{verdict(c, better)}'>{fmt_change(c)}</span>"
        parts.append("<h2>6. Biggest changes</h2><p class='note'>Read this first. |change| ≥ 5%, largest first; "
                     "red = worse, green = better.</p>" + h_table(
            ["Section", "Metric", "A", "B", "Change"],
            [[e(sec.split('.', 1)[0]), e(label), fmt(a, kind), fmt(b, kind), cell(c, better)]
             for sec, label, a, b, kind, better, c in big], num_cols=[2, 3, 4]))
        def metric_section(sec, heading):
            rows = [[e(label), fmt(a, kind), fmt(b, kind), cell(change(a, b), better)]
                    for s_, label, a, b, kind, better in metrics if s_ == sec]
            return f"<h2>{e(heading)}</h2>" + h_table(["Metric", col(sa, "A"), col(sb, "B"), "Change"], rows)

        if spread_rows:
            parts.append("<h2>Run-to-run spread</h2><p class='note'>Each run measured separately. If the ranges overlap, "
                         "the difference between A and B is within run-to-run noise.</p>" + h_table(
                ["Metric", f"{col(sa, 'A')} range", f"{col(sb, 'B')} range", "Verdict"],
                [[e(label), rng(va, kind), rng(vb, kind),
                  f"<span class='{'worse' if v == 'real change' else ''}'>{v}</span>"] for label, kind, va, vb, v in spread_rows],
                num_cols=[1, 2]))
        parts.append(metric_section("Header", "Header"))
        parts.append(metric_section("1. Load profile (per turn)", "1. Load profile (per turn)"))
        parts.append(metric_section("2. Time model (% Agent Time)", "2. Time model (% Agent Time, self time)"))
        ref = {(r["call_name"], r["model"]): r for r in da["03a_top_model_calls"]}
        g = lambda r, k: (ref.get((r["call_name"], r["model"])) or {}).get(k)
        parts.append("<h2>3a. Top model calls</h2><p class='note'>Ranked by B; A for reference.</p>" + h_table(
            ["Call", "Model", "Execs A", "Execs B", "Total (s) A", "Total (s) B", "p95 ms A", "p95 ms B"],
            [[e(r["call_name"]), e(r["model"] or "-"), g(r, "executions") or 0, r["executions"], fmt(g(r, "total_s"), "s"),
              fmt(r["total_s"], "s"), fmt((g(r, "ms_p50_p95_p99") or [None] * 3)[1], "ms"), fmt(r["ms_p50_p95_p99"][1], "ms")]
             for r in db["03a_top_model_calls"]], num_cols=range(2, 8)))
        ref3 = {(r["component"], r["call_name"]): r for r in da["03b_top_tools"]}
        g3 = lambda r, k: (ref3.get((r["component"], r["call_name"])) or {}).get(k)
        parts.append("<h2>3b. Top tools, retrieval and SQL</h2>" + h_table(
            ["Component", "Call", "Per turn A", "Per turn B", "Total (s) A", "Total (s) B", "p95 ms A", "p95 ms B"],
            [[e(r["component"]), e(r["call_name"]), fmt(g3(r, "per_turn"), "f"), fmt(r["per_turn"], "f"),
              fmt(g3(r, "total_s"), "s"), fmt(r["total_s"], "s"), fmt((g3(r, "ms_p50_p95_p99") or [None] * 3)[1], "ms"),
              fmt(r["ms_p50_p95_p99"][1], "ms")] for r in db["03b_top_tools"]], num_cols=range(2, 8)))
        parts.append(metric_section("4. Efficiency and quality", "4. Efficiency ratios and quality"))
        parts.append("<h2>5. Trajectories by task</h2><p class='note'>A new trajectory for a familiar task is the agent "
                     "equivalent of a new plan hash value for the same SQL_ID.</p>")
        trows = []
        for t in trajectory_changes(da, db):
            for lbl, rs in (("A", t["a"]), ("B", t["b"])):
                for r in rs:
                    tag = " <span class='new'>new</span>" if lbl == "B" and r["trajectory"] in t["new"] else ""
                    trows.append([e(t["task"]), lbl, f"<span class='traj'>{e(r['trajectory'])}</span>{tag}", r["turns"],
                                  fmt(r["avg_turn_ms"], "ms"), fmt(r["avg_cost_usd"], "usd"), fmt(r["success_rate"], "pct")])
        parts.append(h_table(["Task", "", "Trajectory", "Turns", "Avg turn (ms)", "Avg cost ($)", "Success"], trows,
                             num_cols=range(3, 7)))
        name = "ASHWA Compare Report"
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{name}</title><style>{CSS}</style></head>"
            f"<body><main>{''.join(parts)}<p class='note'>Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
            f"from Langfuse ClickHouse by ashwa_report.py. Section SQL: sql/*.sql.</p></main></body></html>")


# ---------------------------------------------------------------- scopes

def parse_time(s):
    return datetime.fromisoformat(s).replace(tzinfo=None)


def batch_scopes(batch_id):
    runs = json.load(open(os.path.join(HERE, "runs.json")))
    batch_id = batch_id or runs[-1].get("batch")
    scopes, per_run = [], []
    for version in ("v1", "v2"):
        phases = [r for r in runs if r.get("batch") == batch_id and r["prompt_version"] == version]
        if not phases:
            raise SystemExit(f"Batch {batch_id} has no {version} runs.")
        starts = [parse_time(r["start"]) for r in phases]
        ends = [parse_time(r["end"]) for r in phases]
        configs = {(r.get("model"), r.get("effort"), r.get("embed_model")) for r in phases}
        per_run.append([Scope(label=r["phase_id"], start=parse_time(r["start"]), end=parse_time(r["end"]) + timedelta(seconds=1),
                              elapsed_s=(parse_time(r["end"]) - parse_time(r["start"])).total_seconds(),
                              prefixes=[r["phase_id"] + "-"]) for r in phases])
        scopes.append(Scope(label=f"prompt {version}", start=min(starts), end=max(ends) + timedelta(seconds=1),
                            elapsed_s=sum((b - a).total_seconds() for a, b in zip(starts, ends)),
                            prefixes=[r["phase_id"] + "-" for r in phases],
                            note=f"{len(phases)} run(s), {', '.join('/'.join(map(str, c)) for c in configs)}"))
    a_cfg, b_cfg = scopes[0].note.split(", ", 1)[1], scopes[1].note.split(", ", 1)[1]
    if a_cfg != b_cfg:
        print(f"WARNING: v1 and v2 used different configurations ({a_cfg} vs {b_cfg}); not like for like.")
    return scopes, per_run


def run_scope(phase_id):
    runs = json.load(open(os.path.join(HERE, "runs.json")))
    r = next((r for r in runs if r["phase_id"] == phase_id), None) if phase_id else runs[-1]
    if r is None:
        raise SystemExit(f"Run {phase_id} not found in runs.json.")
    start, end = parse_time(r["start"]), parse_time(r["end"])
    return Scope(label=r["phase_id"], start=start, end=end + timedelta(seconds=1),
                 elapsed_s=(end - start).total_seconds(), prefixes=[r["phase_id"] + "-"],
                 note=f"run {r['phase_id']}: prompt {r['prompt_version']}, {r.get('model')} "
                      f"(effort {r.get('effort')}), embeddings {r.get('embed_model')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start"), ap.add_argument("--to", dest="end")
    ap.add_argument("--prefix", action="append", default=[], help="session id prefix (repeatable)")
    ap.add_argument("--compare-from"), ap.add_argument("--compare-to")
    ap.add_argument("--compare-prefix", action="append", default=[])
    ap.add_argument("--run", nargs="?", const="", help="single report for one run in runs.json (default: latest)")
    ap.add_argument("--batch", nargs="?", const="", help="experiment batch from runs.json (default: latest)")
    ap.add_argument("--html", help="also write the report as HTML to this path")
    ap.add_argument("--project", default="local-project")
    ap.add_argument("--root-name", action="append", default=None, help="top-level span name of a turn (repeatable)")
    ap.add_argument("--tool", action="append", default=None, help="span names to treat as tool calls (repeatable)")
    ap.add_argument("--success-score", default="task_success")
    ap.add_argument("--task-tag", default="scenario:", help="trace tag prefix that names the task type")
    o = ap.parse_args()
    o.root_name = o.root_name if o.root_name is not None else ["support_turn"]
    o.tool = o.tool if o.tool is not None else ["lookup_order", "search_knowledge_base", "issue_refund", "escalate_to_human"]

    if o.run is not None:
        sa, sb, runs = run_scope(o.run or None), None, None
    elif o.batch is not None:
        (sa, sb), runs = batch_scopes(o.batch or None)
    else:
        runs = None
        if not (o.start and o.end):
            ap.error("give --from and --to, or --batch")
        a0, a1 = parse_time(o.start), parse_time(o.end)
        sa = Scope("A", a0, a1, (a1 - a0).total_seconds(), o.prefix, f"sessions {o.prefix}" if o.prefix else "")
        sb = None
        if o.compare_from or o.compare_to or o.compare_prefix:
            b0 = parse_time(o.compare_from) if o.compare_from else a0
            b1 = parse_time(o.compare_to) if o.compare_to else a1
            sb = Scope("B", b0, b1, (b1 - b0).total_seconds(), o.compare_prefix,
                       f"sessions {o.compare_prefix}" if o.compare_prefix else "")

    ch = client()
    da = collect(ch, sa, o)
    if not da["01_load_profile"][0]["turns"]:
        raise SystemExit(f"No turns found for A ({sa.start} to {sa.end}, prefixes {sa.prefixes}, roots {o.root_name}).")
    if sb is None:
        print(text_single(sa, da))
        page = html_report(sa, da)
    else:
        db = collect(ch, sb, o)
        if not db["01_load_profile"][0]["turns"]:
            raise SystemExit(f"No turns found for B ({sb.start} to {sb.end}, prefixes {sb.prefixes}).")
        spread_rows = spread(ch, runs, o)
        print(text_compare(sa, da, sb, db, spread_rows))
        page = html_report(sa, da, sb, db, spread_rows)
    if o.html:
        open(o.html, "w").write(page)
        print(f"\nHTML report written to {o.html}")


if __name__ == "__main__":
    main()
