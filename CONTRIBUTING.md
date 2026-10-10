# Contributing to ASHWA

Issues and pull requests are welcome. ASHWA is a small project with one aim: a
DBA should be able to open one SQL file and see exactly how a number in the
report is calculated. Contributions that keep it that way are the easiest to
merge.

## The most useful contributions

- **Try it on a different agent or framework** (LangGraph, CrewAI, OpenAI
  Agents SDK, your own) and open a
  [framework report](https://github.com/smpawar/ashwa/issues/new?template=framework_report.yml)
  with what worked, what did not, and which span names or types needed special
  handling.
- **Pick something from the [roadmap](README.md#roadmap).** Open an issue first
  for anything that adds a report section or changes a metric, so the design
  can be agreed before you write the SQL.
- **Report a number that looks wrong.** Include the section, the value you
  expected, and the trace or span that shows it.

## Setup

To work on the report and SQL you need Python 3.12. For most changes the
bundled demo data is enough (`pip install chdb`, then
`python ashwa_report.py --demo`). To run against live traces you need a
self-hosted Langfuse v4 with ClickHouse reachable. You do not need an API key.

```bash
git clone https://github.com/smpawar/ashwa && cd ashwa
python3.12 -m venv .venv && .venv/bin/pip install clickhouse-connect python-dotenv
cp .env.example .env        # set the CLICKHOUSE_* values for your Langfuse
```

To work on the reference experiment (agent, workload, scoring), follow
[`docs/EXPERIMENT.md`](docs/EXPERIMENT.md). Running the workload calls a paid
model API: about $0.017 per turn on the default model.

## Rules for changes

1. **One report section, one readable SQL file.** No section logic hidden in
   Python; `ashwa_report.py` runs the files and lays out the results.
2. **All SQL files share the same `roots` and `spans` CTEs.** These decide
   which turns and spans a window covers. If you change them, change them in
   every file. CI checks this; run it locally with:
   ```bash
   python scripts/check_sql_scope.py
   ```
3. **A change to how a metric is calculated needs a before-and-after.** Run
   `python ashwa_report.py --demo` (the committed experiment data; needs
   `pip install chdb`, no Langfuse) before and after your change and put both
   in the pull request, so reviewers can see the effect. The published
   figures in the README must still reproduce, or the pull request must say
   why they changed.
4. **Never commit `.env`** or anything else containing a key.

## Pull requests

- Keep each pull request to one change.
- Say what you tested it against: Langfuse version, ClickHouse version, agent
  framework.
- New flags and sections need a line in the README.

## Licence

By contributing you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
