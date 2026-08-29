# agent-bus-team

[![PyPI version](https://badge.fury.io/py/agent-bus-team.svg)](https://badge.fury.io/py/agent-bus-team)

Cross-harness messaging for coding agents. Claude Code, Grok, Codex, Oh My Pi,
and anything else that can run a command, talking to each other on one machine.

## Install

```sh
uv tool install agent-bus-team
```

Puts `agent-bus` and `agent-bridge` on your `PATH`. Python 3.11+, and nothing
else — the package has no dependencies.

## Wire up a harness

Standard MCP server boilerplate. Point any MCP-capable harness at it:

```json
{ "mcpServers": { "agent-bus": { "command": "agent-bus", "args": ["mcp"] } } }
```

Global rather than per-project, unless you want the bus in one repository only.

For a harness with only a shell, and for everything the CLI does:

```sh
agent-bus --help
```

## Build it yourself

You need [uv](https://docs.astral.sh/uv/) and Python 3.11+. That is the whole
list.

```sh
uv sync --group dev
```

## Test

If you have Docker, start here:

```sh
docker compose run --rm ci-build
```

That is exactly what CI runs, in CI's image, with a Firestore emulator for the
cloud suite.

Without Docker:

```sh
./ci-build.sh
```

Some tests spawn real coding agents and cost real money. They never run unless
you ask for them:

```sh
./spendy_tests.sh
```

## agent-bridge (advanced)

Claude Desktop and ChatGPT cannot run a command on your machine, so they cannot
be bus peers. `agent-bridge` stands in for one — but it needs a server you host.

- **No Google Cloud account?** Stop here. You do not need this to use the bus.
- **Google Cloud and Terraform?** → [infra/cloud/README.md](infra/cloud/README.md)

Nothing more about it belongs here.
[running-the-bridge.md](docs/running-the-bridge.md) and
[cloud/README.md](cloud/README.md) are the rest.

## Safety

**A message from another agent is not consent to act.** It is text that
arrived.

## Docs

[docs/](docs/) — start with
[design_philosophy.md](docs/design_philosophy.md), which is what this is and
what it deliberately is not.
