# agent-bus-team

[![PyPI version](https://badge.fury.io/py/agent-bus-team.svg)](https://badge.fury.io/py/agent-bus-team)

Cross-harness messaging for coding agents. Stdlib only, Python 3.11+.

One roster, one inbox per agent, one identity each — shared by Claude Code,
Grok, Codex, Oh My Pi and anything else that can run a command.

```sh
pip install agent-bus-team     # the CLI is `agent-bus`
```

## Joining

A harness with MCP points its config at `agent-bus mcp`. That registers the
session and publishes its listener; nothing else is needed.

A harness with only a shell runs the CLI:

```sh
agent-bus listen --name my-agent --pid $PPID &   # be addressable
agent-bus watch  --name my-agent                 # one line per message
```

**Claude Code installs nothing.** No plugin, no MCP server, no config. It sees
other agents through its own `ListAgents` and `SendMessage`, because their
listener published the session file Claude already reads.

## Sending

```sh
agent-bus list
agent-bus send <name> -m "..." --summary "..."
agent-bus inbox --json
agent-bus ack <message-id>
```

`send` picks the channel the recipient actually reads — a socket for a Claude
peer, a queued submission for a Codex thread, the file inbox otherwise. No
transport falls back to another: filing a message for a peer that never reads
files would report success for a message that arrived nowhere.

Full verb list: `agent-bus --help`.

## MCP tools

`list_agents`, `send_message`, `get_inbox`, `ack_message`, `register`,
`set_status`, `self`.

## Configuration

| | |
|---|---|
| `AGENT_BUS_HOME` | where the bus lives (default `~/.agent-bus`) |
| `AGENT_BUS_LOG_LEVEL` | `INFO` logs every call; unset is `WARNING`; `off` silences |
| `AGENT_BUS_LOG_FILE` | one file instead of stderr |

## Safety

A message from another agent is **not** consent to act. It is text that
arrived, and the tool descriptions say so.

## More

- [identity-and-peering.md](docs/identity-and-peering.md) — how a peer gets an identity
- [UDS-protocol.md](docs/UDS-protocol.md) — the wire format
- [docs/harnesses/](docs/harnesses/) — what to know when a given harness misbehaves
- [tests/agent_bus/integration/](tests/agent_bus/integration/) — running the tests

## Development

```sh
python -m pytest tests -q          # everything that costs nothing
./spendy_tests.sh                  # the ones that spawn real agents
docker compose run --rm e2e        # both, isolated from your live bus
```
