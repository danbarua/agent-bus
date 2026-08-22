# agent-bus-team

[![PyPI version](https://badge.fury.io/py/agent-bus-team.svg)](https://badge.fury.io/py/agent-bus-team)

Small stdlib-only Python 3.11+ inter-agent messaging CLI and library.

Parallel bus for Claude Code, Grok, Oh My Pi (omp), Codex, and others.
Two channels: file bus (`send`/`inbox`) vs. native UDS (`listen` + `send-peer`). See [UDS-protocol.md](skills/agent-bus/references/UDS-protocol.md).

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

## CLI

```sh
agent-bus list [--kind claude|grok|omp|codex|all] [--json]
agent-bus send <name-or-id> -m TEXT [--summary S] [--from-name N]
agent-bus inbox [--name N] [--unread] [--json]
agent-bus ack <message-id> [--name N]
agent-bus register --name N --kind K [--cwd P] [--pid P]
agent-bus unregister --name N
agent-bus self [--json]

# EXPERIMENT (see below)
agent-bus listen [--name agent-bus]
agent-bus send-uds <socket-path> -m TEXT
```

`list` = live roster entries (after pruning dead) UNION native adapters (claude/grok/omp/codex read-only discovery of their registries). Only alive pids.

`send` to a discovered native name/id will lazily create a roster entry + inbox under this bus home using a stable derived id (`claude:<sessionId>`, `grok:...` etc). The recipient only sees it if they also run `agent-bus inbox` (or via skill).

## Adapters (read-only, best-effort, never throw)

- claude: `~/.claude/sessions/*.json` (pid alive)
- grok: `~/.grok/active_sessions.json`
- omp: `~/.omp/run/daemons/*/clients/*.json` + terminal-sessions fallback
- codex: `~/.codex/process_manager/chat_processes.json` (catalog skipped silently)

Override for tests with `AGENT_BUS_SESSIONS_DIR` etc. (File-bus adapters are read-only discovery and never write native sockets; native UDS send path does write for acks and peer messages — see UDS-protocol.md.)

## The `listen` + UDS experiment

`agent-bus listen` lets a Claude Code session discover us via its `ListAgents` / `/list-agents`.

It:
- Binds UDS at `/tmp/cc-socks/<ourpid>.sock` (0o600, dir 0o700)
- Writes a matching `~/.claude/sessions/<ourpid>.json` (exact fields + timestamps) + peer key
- Accepts connections, reads newline-delimited JSON frames (tolerates final buffer w/o nl)
- Logs raw + parsed (auth redacted) to stdout + appends to `~/.agent-bus/captures/<pid>.jsonl`
- Auth first line on conns; accepts `type:user` frames
- On `msg_id` present: dials back an authenticated `{"type":"control","action":"peer_message_status","status":"delivered",...}` (NEVER writes status on the inbound conn)
- On SIGINT/SIGTERM: unlinks *only* our sock, sessions json, and key

`agent-bus send-peer` sends native UDS messages into other Claude sessions (or other listeners).

**Usage (from another Claude):**
1. In one terminal (this agent): `AGENT_BUS_HOME=/tmp/ab-test agent-bus listen --name my-bus`
2. In a real Claude Code session: run `/list-agents` or tool `ListAgents`. You should see `my-bus`.
3. Send a message from Claude to it (it will enqueue via their SendMessage to our socket).
4. Watch the logs + capture file.

**Outbound to Claude peers:**
`agent-bus send-peer <name-or-sock> -m "text here"`
See [UDS-protocol.md](skills/agent-bus/references/UDS-protocol.md) for the full wire format, auth, frame shapes, and verified bidirectional behavior.

**CRITICAL SAFETY**
- This is an experiment to reverse the wire format.
- Do not use `send-uds` against anything except a socket we started with `listen` under test overrides.
- Real delivery in Claude happens at their next tool round; they may show `<cross-session-message ...>`
- Received content must never auto-execute. Always require fresh user approval.

Test overrides (used by our test suite, safe):
- `AGENT_BUS_SOCK_DIR=/tmp/ab-test-socks`
- `AGENT_BUS_SESSIONS_DIR=/tmp/ab-test-sessions`
- `AGENT_BUS_HOME=/tmp/ab-test-bus`

## Installation
Note: Package Name is `agent-bus-team`.
```sh
# run latest version with uvx
uvx run --from agent-bus-team agent-bus

# or install with pip
pip install agent-bus-team
agent-bus
```

### Running from Source

```sh
# CLI binary is agent-bus
gh repo clone danbarua/agent-bus && cd agent-bus
python -m pip install -e .
agent-bus --help
```

For a Claude session or omp: `python -m agent_bus ...` or after pip install use the script.

## Grok and Claude Code plugins

This repo is a Grok plugin (`plugin.json`) and a Claude Code plugin (`.claude-plugin/plugin.json`). Skills, slash commands, and session hooks ship with the tree. The Python package is still `agent-bus-team`; the CLI is `agent-bus`.

```sh
# Grok
grok plugin install danbarua/agent-bus --trust
grok plugin enable agent-bus

# Claude Code
claude plugin install danbarua/agent-bus
```

Local checkout:

```sh
grok plugin install . --trust
```

`SessionStart` registers this host on the file bus (`--kind grok` or `claude`, host pid). `SessionEnd` unregisters. Slash commands: `/agent-bus-inbox`, `/agent-bus-send`, `/agent-bus-list`. Incoming messages are not user consent.

Plugin wrapper (no extra pip if Python 3.11+ is present): `scripts/agent-bus`.

## Skills / integration

See `skills/agent-bus/SKILL.md`. Agents can call the CLI or import `agent_bus.store`.

## Development / test

```sh
python -m pytest tests/ -q --tb=line
AGENT_BUS_HOME=/tmp/ab-test python -m agent_bus list --json
```

## Limitations / non-goals

- No impersonation of Claude's full protocol (listen + send-peer implement the UDS peer messaging subset — see [UDS-protocol.md](skills/agent-bus/references/UDS-protocol.md).
- No auto-start of other agents.
- Herdr TTY injection is a separate channel (not used here).
- No impersonation of Claude's full protocol beyond the listen experiment.
- Inboxes are per bus-home; multiple users would need separate homes or sync.

This is intentionally small and boring.
