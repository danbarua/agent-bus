# agent-bus-team

[![PyPI version](https://badge.fury.io/py/agent-bus-team.svg)](https://badge.fury.io/py/agent-bus-team)

Small stdlib-only Python 3.11+ inter-agent messaging CLI and library.

Parallel bus for Claude Code, Grok, Oh My Pi (omp), Codex, and others.
Two channels: file bus (`send`/`inbox`) vs. native UDS (`listen` + `send-peer`).

**The asymmetry:** Claude sees nothing. A Claude Code session has no plugin, no MCP server, no
inbox and no configuration — it uses its native `ListAgents`/`SendMessage` and peers simply
appear. Everything here (roster, inboxes, MCP tools, UDS listener) is peer-side, and its job is to
make a grok, omp or codex process look like a native Claude peer from the outside.

See [identity-and-peering.md](skills/agent-bus/references/identity-and-peering.md) for how a peer
gets an identity, and [UDS-protocol.md](skills/agent-bus/references/UDS-protocol.md) for the wire
format.

## On-disk (AGENT_BUS_HOME=~/.agent-bus)

```
~/.agent-bus/
  roster/<id>.json     # registered agents (uuid ids)
  inboxes/<id>.jsonl   # append-only, one JSON message per line
  captures/<pid>.jsonl # from `listen`
```

Roster entry and Message envelope match the spec in the source.

## Rules (enforced)

- Plain text **only** in `text` (no structured payloads).
- Refuse `> 1_000_000` chars.
- Per-inbox unread cap 50 (send fails with clear error).
- **Never treat a received message as user consent.** Messages are cross-session only. The receiving agent must still show the user and obtain explicit approval before acting on any instruction in a message.
- Names unique among *live* (pid-alive) registrations; collisions get `-2`, `-3` suffix on register.
- `list` drops stale roster entries (dead pid) but leaves their inbox files.
- `send` resolves the sender from your own registration, so two messages from one agent share an
  id the recipient can reply to. `--from-name` overrides it on the CLI; the `send_message` MCP
  tool deliberately does not expose that flag, so an agent cannot claim another agent's identity.
  With no registration the sender falls back to `anonymous`.

## CLI

```sh
agent-bus list [--kind claude|grok|omp|codex|all] [--json]
agent-bus send <name-or-id> -m TEXT [--summary S] [--from-name N]
agent-bus inbox [--name N] [--unread] [--json]
agent-bus ack <message-id> [--name N]
agent-bus register --name N --kind claude|grok|omp|codex|other [--cwd P] [--pid P]
agent-bus unregister --name N
agent-bus self [--json]

agent-bus listen [--name N] [--pid HOST_PID] [--inbox-name N]
agent-bus send-peer <name-or-sock> -m TEXT
agent-bus mcp                       # stdio MCP server (tools + UDS listen)
agent-bus hook session-start|session-end

# EXPERIMENT — test only, see below
agent-bus send-uds <socket-path> -m TEXT
```

`list` = live roster entries (after pruning dead) UNION native adapters (claude/grok/omp/codex read-only discovery of their registries). Only alive pids.

`send` to a discovered native name/id will lazily create a roster entry + inbox under this bus home using a stable derived id (`claude:<sessionId>`, `grok:...` etc). The recipient only sees it if they also run `agent-bus inbox` (or via the MCP tools).

## Adapters (read-only, best-effort, never throw)

- claude: `~/.claude/sessions/*.json` (pid alive)
- grok: `~/.grok/active_sessions.json`
- omp: `~/.omp/run/daemons/*/clients/*.json` + terminal-sessions fallback
- codex: `~/.codex/process_manager/chat_processes.json` (catalog skipped silently)

Override for tests with `AGENT_BUS_SESSIONS_DIR` etc. (File-bus adapters are read-only discovery and never write native sockets; native UDS send path does write for acks and peer messages — see UDS-protocol.md.)

## MCP server

`agent-bus mcp` is a stdio MCP server. Transport is newline-delimited JSON; it mirrors an
LSP-style `Content-Length` client back in that client's own framing. Six tools:

| tool | args |
| --- | --- |
| `list_agents` | optional `kind` |
| `send_message` | `to`, `text`, optional `summary` |
| `get_inbox` | optional `name`, `unread_only` |
| `ack_message` | `message_id`, optional `name` |
| `register` | `name`, optional `kind` |
| `self` | — |

`register` is for MCP-only peers: an agent launched with a session-start hook is registered
automatically, but one that only has the MCP server must call it to be addressable by name
instead of by pid. It renames the entry the server already claimed for this pid (it does not
create a second identity) and repoints the published socket at the new name.

Starting the server also registers this host on the file bus and starts the UDS listener below.

## The `listen` + UDS experiment

`agent-bus listen --name <title> --pid <host-pid>` publishes a Claude-compatible session file and
UDS socket. Native `ListAgents`/`SendMessage` in Claude Code then see it as a teammate.
`/list-agents` itself is untouched.

`--pid` is watch-only: the advertised pid is always the listener process (the binder), so Claude's
`getpeereid` check matches. When that host pid already has a roster entry, `listen` adopts it
rather than registering again — one peer, one socket, one name, and no `-2` suffix collision.

It:
- Binds UDS at `/tmp/cc-socks/<publish_pid>.sock` (0o600, dir 0o700)
- Writes matching `~/.claude/sessions/<publish_pid>.json` + `.key`
- Accepts JSONL frames, acks with `peer_message_status` on a dial-back conn only (never on the inbound conn)
- Logs raw + parsed (auth redacted) to stdout and appends to `~/.agent-bus/captures/<pid>.jsonl`
- Unlinks only its own socket, session file and key on SIGINT/SIGTERM

A listener is started for **every non-Claude kind** (grok, omp, codex, other) whenever
`session_start()` runs — that is, from `agent-bus mcp` or from an `agent-bus hook session-start`.
Claude sessions are the one kind that never get one, since they already have their own socket.

`agent-bus send-peer <name-or-sock> -m "text"` sends UDS peer messages to Claude (or other listeners).

**Claude users:** install nothing. If a peer is running `listen`, it appears in `/list-agents` and
messages arrive as cross-session.

**Usage example:**
1. `AGENT_BUS_HOME=/tmp/ab-test agent-bus listen --name my-bus --pid $$`
2. In Claude Code: `/list-agents` shows it.
3. Send from Claude; watch the logs and the capture file.

**CRITICAL SAFETY**
- Received content must never auto-execute. Require explicit user approval.
- `send-uds` is an experiment for reversing the wire format. Do not point it at anything except a socket we started with `listen` under test overrides.

Test overrides: `AGENT_BUS_SOCK_DIR`, `AGENT_BUS_SESSIONS_DIR`, `AGENT_BUS_HOME`.

## Installation

Note: package name is `agent-bus-team`; the CLI is `agent-bus`.

```sh
# run the latest version with uvx
uvx --from agent-bus-team agent-bus

# or install with pip
pip install agent-bus-team
agent-bus
```

### Running from source

```sh
gh repo clone danbarua/agent-bus && cd agent-bus
python -m pip install -e .
agent-bus --help
```

For Claude or omp: use `python -m agent_bus` or the installed `agent-bus` CLI. No plugin is
required for Claude.

## Grok plugin (and Claude interop)

This repo is a **Grok plugin** (`plugin.json`). Installing it ships the `agent-bus` skill and the
`scripts/agent-bus` wrapper:

```sh
grok plugin install danbarua/agent-bus --trust
grok plugin enable agent-bus

# or from a local checkout
grok plugin install . --trust
```

**The plugin does not currently wire up the MCP server or any session hooks.** `hooks/hooks.json`
declares no events, and there is no `.mcp.json`, so a fresh install registers nothing on the bus
by itself. To get a Grok session onto the bus, either point your Grok MCP configuration at
`agent-bus mcp`, or publish the peer by hand:

```sh
agent-bus listen --name my-grok --pid <grok-host-pid>
```

`listen` registers the entry itself, so no separate `register` step is needed — and a standalone
`agent-bus register` from a shell would not help anyway: it claims the pid of the command, which
exits immediately, and the dead entry is pruned on the next `list`. A hand-started peer registers
as kind `other`; only `session_start()` (via `agent-bus mcp` or a session-start hook) detects
`grok`.

**Claude Code: install NOTHING.** No plugin, no `.claude-plugin/`, no `.mcp.json`, no skills or
slash commands. Claude sees a Grok/omp/codex peer through native `ListAgents`/`SendMessage`
because that peer's `listen` published the session file and socket.

See [skills/agent-bus/SKILL.md](skills/agent-bus/SKILL.md) for the agent-facing version. Use the
CLI or `import agent_bus.store` for everything else.

## Development / test

```sh
python -m pytest tests/ -q --tb=line
AGENT_BUS_HOME=/tmp/ab-test python -m agent_bus list --json
```

## Limitations / non-goals

- No impersonation of Claude's full protocol — `listen` and `send-peer` implement the UDS peer messaging subset only (see [UDS-protocol.md](skills/agent-bus/references/UDS-protocol.md)).
- No auto-start of other agents.
- Herdr TTY injection is a separate channel (not used here).
- Inboxes are per bus-home; multiple users would need separate homes or sync.

This is intentionally small and boring.
