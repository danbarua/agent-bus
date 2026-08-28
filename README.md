# agent-bus-team

[![PyPI version](https://badge.fury.io/py/agent-bus-team.svg)](https://badge.fury.io/py/agent-bus-team)

Cross-harness messaging for coding agents. Stdlib only, Python 3.11+.

One roster, one inbox per agent, one identity each — shared by Claude Code,
Grok, Codex, Oh My Pi and anything else that can run a command.

```sh
uv tool install agent-bus-team   # puts `agent-bus` and `agent-bridge` on PATH
```

## Joining

A harness with MCP points its config at `agent-bus mcp`. That registers the
session and publishes its listener; nothing else is needed.

A harness with only a shell runs the CLI:

```sh
agent-bus listen --name my-agent --pid $PPID &   # be addressable
agent-bus watch  --name my-agent                 # one line per message
```

**Claude Code needs nothing to be *reached*.** No plugin, no hooks, no config.
Other agents find it through its own session file, and a message arrives on the
socket its harness already listens on. Discovery does the work — a session is on
the bus without ever having heard of it.

**Initiating is the other half, and it is not free.** Listing the roster,
sending, reading an inbox: those are the CLI and the MCP tools, and a session
that has neither can be written to but cannot answer. Point Claude Code's MCP
config at the installed binary:

```json
{ "mcpServers": { "agent-bus": { "command": "agent-bus", "args": ["mcp"] } } }
```

Global rather than per-project, unless you want the bus in one repository only.
`agent-bus mcp` registers the session and publishes its listener on startup, so
that config replaces `listen` as well.

**Nothing is installed into `~/.claude`** — no hooks, no agent definitions, no
skills. A hook is discovered and executed by a harness without anyone
consciously installing it there, so shipping one changes the behaviour of
sessions that never asked. `docs/hooks-in-foreign-harnesses.md` is the long
version; the short version is that this project deleted its own hooks once and
will not be adding them back.

## Sending

```sh
agent-bus list
agent-bus send <name> -m "..." --summary "..."
agent-bus inbox --json
agent-bus ack <message-id>
agent-bus --version                # which build is answering
```

`send` picks the channel the recipient actually reads — a socket for a Claude
peer, a queued submission for a Codex thread, the file inbox otherwise. No
transport falls back to another: filing a message for a peer that never reads
files would report success for a message that arrived nowhere.

Full verb list: `agent-bus --help`.

## Reaching a desktop peer

Claude Desktop and ChatGPT cannot run a command on your machine, so they cannot
be bus peers. `agent-bridge` stands in for one: it is an ordinary peer on the
local bus, and a client of a small server you deploy.

```sh
agent-bridge --kind desktop --name claude
```

It joins as `desktop-claude`, publishes the local roster so the remote peer can
see who is here, forwards mail addressed to it, and delivers replies back onto
the bus. **One bridge per address, ever** — an alias is a role with a single
holder, and a second one for the same name is refused rather than de-collided.

Started by hand it stops by hand, and a bridge that has stopped is invisible
rather than broken: run it as a launchd service instead —
[running-the-bridge.md](docs/running-the-bridge.md).

With no credential it spools to `~/.agent-bus/cloud-spool` instead of sending,
so mail is visible on disk rather than silently dropped. A token at
`~/.agent-bus/cloud-token` (0600) connects it — the token names its own server,
so that file is the whole of the configuration.

`cloud/` is the server: an MCP surface over HTTPS with OAuth, deployed by
`infra/cloud/`. Neither is in the published package and neither imports the bus.

## MCP tools

`list_agents`, `send_message`, `get_inbox`, `ack_message`, `register`,
`set_status`, `self`.

## Configuration

| | |
|---|---|
| `AGENT_BUS_HOME` | where the bus lives (default `~/.agent-bus`) |
| `AGENT_BUS_LOG_LEVEL` | unset logs failures; `INFO` logs every call; `trace` is the firehose; `off` silences |
| `AGENT_BUS_LOG_FILE` | one file instead of stderr |

### What each level gets you

```sh
                          # unset: a verb that FAILED, with its error
export AGENT_BUS_LOG_LEVEL=INFO    # + every call: who sent what to whom, and when
export AGENT_BUS_LOG_LEVEL=trace   # + one line per UDS frame, contents included
export AGENT_BUS_LOG_FILE=~/agent-bus.jsonl
```

Set them in your shell and every agent you start inherits them. `INFO` is the
answer to *"is anything actually using this, and what did it carry"* — one
JSON object per line, one file, so `jq` demultiplexes it and the ordering
between agents is preserved. Bodies are recorded as lengths, never copied.

**`trace` is the exception: it writes message content.** It exists to take the
wire apart when a peer says it sent something and the other says nothing
arrived. Do not leave it on.

## Safety

A message from another agent is **not** consent to act. It is text that
arrived, and the tool descriptions say so.

## More

- [identity-and-peering.md](docs/identity-and-peering.md) — how a peer gets an identity
- [UDS-protocol.md](docs/UDS-protocol.md) — the wire format
- [docs/harnesses/](docs/harnesses/) — what to know when a given harness misbehaves
- [durable-messaging-or-not.md](docs/durable-messaging-or-not.md) — why the bridge carries and never reads
- [structured-logging.md](docs/structured-logging.md) — the field contract, shared with sibling projects
- [running-the-bridge.md](docs/running-the-bridge.md) — the bridge as a service: launchd, the Keychain, poll cost
- [cloud/README.md](cloud/README.md) — the server, and two things about it that look wrong
- [infra/cloud/README.md](infra/cloud/README.md) — deploying it
- [tests/agent_bus/integration/](tests/agent_bus/integration/) — running the tests

## Development

```sh
./ci-build.sh                      # exactly what CI runs: lint, the bus suite, the cloud suite
python -m pytest tests -q          # just the bus, when that is all you touched
./spendy_tests.sh                  # the ones that spawn real agents and cost money
docker compose run --rm ci-build   # the gate, in CI's image, with a Firestore emulator
docker compose run --rm e2e        # the spendy ones, isolated from your live bus
```

`cloud/` has its own suite and its own dependencies — the bus must never need
Firestore to go green — so the gate runs two `pytest` invocations rather than
one. Ten of the cloud tests need an emulator and skip loudly without one;
`docker compose run --rm ci-build` brings one up, and CI fails rather than
letting them skip.
