# Security

ASHWA is a reporting tool: it reads traces from a Langfuse ClickHouse you
control and writes a text or HTML report. It runs no service and accepts no
network connections. The example agents in `examples/` and the reference
experiment call model APIs with the key in your `.env`, which is ignored by git
and must never be committed.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
vulnerability reporting on this repository's **Security** tab ("Report a
vulnerability"). If that option is not available to you, open an issue titled
"Security" with no details, and the maintainer will reply with a private
channel.

You can expect an acknowledgement within a week. Fixes are released on the
`main` branch; there are no long-term support branches.

## Scope

In scope: the report script, the SQL files, the demo mode, the example agents
and the setup scripts in this repository.

Out of scope: Langfuse, ClickHouse, Google ADK and the model APIs themselves.
Please report problems in those to their own projects.
