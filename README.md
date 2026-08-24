# agent-bus-team

[![PyPI version](https://badge.fury.io/py/agent-bus-team.svg)](https://badge.fury.io/py/agent-bus-team)

Small stdlib-only Python 3.11+ inter-agent messaging CLI and library.

**One bus** for Claude Code, Grok, Oh My Pi (omp), Codex, and others: one roster of agents, one
durable inbox per agent, one identity each.

`agent-bus send <name> -m TEXT` is the only send command. It resolves the target, reads its kind
and picks the channel that kind actually reads: UDS for a Claude peer, a queued submission for a
Codex thread, the file inbox for everything else. No transport falls back to another — a Claude
session never reads a file inbox, so filing a message for one would report success for a message
that arrived nowhere.

Inbound lands in one place regardless. A native UDS frame from a Claude peer is persisted through
the same `send_message` call, addressed to the same roster id, and `agent-bus inbox` reads both
without being able to tell them apart.

UDS is not a second bus — it is how Claude Code peers reach this one, and how their acks get back.
An outbound frame carries `from: uds:<our-socket>` as its return address, and the
recipient dials that socket back with `peer_message_status`. A peer with no listener has no address
to be acked at, so the send fails before it opens a connection. The listener is the return path,
not a way of advertising yourself.

**The asymmetry:** Claude sees nothing. A Claude Code session has no plugin, no MCP server, no
inbox and no configuration — it uses its native `ListAgents`/`SendMessage` and peers simply
appear. Everything here (roster, inboxes, MCP tools, UDS listener) is peer-side, and its job is to
make a grok, omp or codex process look like a native Claude peer from the outside.

See [identity-and-peering.md](https://github.com/danbarua/agent-bus/blob/main/docs/identity-and-peering.md) for how a peer
gets an identity, and [UDS-protocol.md](https://github.com/danbarua/agent-bus/blob/main/docs/UDS-protocol.md) for the wire
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

agent-bus status <idle|busy|waiting> [--cwd P]
agent-bus watch [--name N] [--from-start]   # one line per inbound message
agent-bus grok-status [--watch]             # grok session activity from its leader
agent-bus orphans [--adopt]                 # mailboxes no roster entry points at

agent-bus listen [--name N] [--pid HOST_PID] [--inbox-name N]
agent-bus mcp                       # stdio MCP server (tools + UDS listen)
agent-bus hook session-start|session-end

# test fixture — drives our own listener, never a live Claude socket
agent-bus send-uds <socket-path> -m TEXT
```

`list` = live roster entries UNION what the native adapters can see (claude/grok/omp read-only
discovery of their own registries). "Live" is the address's own rule, not always a pid: a Codex
thread is addressable precisely when no process is running, so it is never discovered — a Codex
session joins by registering through the MCP server.

`status` is self-reported and nothing can infer it for you; an agent thinking between tool calls
is invisible from outside. The exception is grok, whose leader knows what its sessions are doing —
see `grok-status`.

`watch` prints one compact line per inbound message, for a harness whose watch mechanism turns
stdout lines into events (grok's `monitor`). It starts from now on purpose: replaying a backlog is
the fastest way to trip a rate limiter.

`orphans` finds inbox files no roster entry points at. Mail outlives its agent by design, so this
is how you get at messages left behind by a peer that exited.

`send` to a discovered native name/id will lazily create a roster entry + inbox under this bus home using a stable derived id (`claude:<sessionId>`, `grok:...` etc). The recipient only sees it if they also run `agent-bus inbox` (or via the MCP tools).

## Adapters (read-only, best-effort, never throw)

- claude: `~/.claude/sessions/*.json` (pid alive)
- grok: `~/.grok/active_sessions.json`
- omp: `~/.omp/run/daemons/*/clients/*.json` + terminal-sessions fallback
- codex: `~/.codex/process_manager/chat_processes.json` (catalog skipped silently)

Override for tests with `AGENT_BUS_SESSIONS_DIR` etc. (File-bus adapters are read-only discovery and never write native sockets; native UDS send path does write for acks and peer messages — see UDS-protocol.md.)

## MCP server

`agent-bus mcp` is a stdio MCP server. Transport is newline-delimited JSON; it mirrors an
LSP-style `Content-Length` client back in that client's own framing. Seven tools:

| tool | args |
| --- | --- |
| `list_agents` | optional `kind` |
| `send_message` | `to`, `text`, optional `summary` |
| `get_inbox` | optional `name`, `unread_only` |
| `ack_message` | `message_id`, optional `name` |
| `register` | `name`, optional `kind` |
| `set_status` | `status`, optional `cwd` |
| `self` | — |

`register` is how a peer claims a *name*. Starting the server already registers it — and since
the MCP `initialize` handshake names the client (`codex-mcp-client`, `omp-coding-agent`,
`grok-shell-*`), the kind and a derived name are set without asking. Call `register` to be
addressable as something you chose. It renames the entry the server already claimed for this pid
(it does not create a second identity) and repoints the published socket at the new name.

Starting the server also registers this host on the file bus and starts the UDS listener below.

## `listen` — the UDS return path

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
`session_start()` runs, which in practice means whenever `agent-bus mcp` starts. Claude sessions
are the one kind that never get one, since they already have their own socket.

`agent-bus send <name> -m "text"` reaches a Claude peer over UDS; the transport is chosen from the
target's kind, so there is no vendor-specific send command to remember.

**Claude users:** install nothing. If a peer is running `listen`, it appears in `/list-agents` and
messages arrive as cross-session.

**CRITICAL SAFETY**
- Received content must never auto-execute. Require explicit user approval.
- `send-uds` (not `send`) sends one exact raw frame with an empty auth token, which only our own
  listener accepts. The wire format it was written to reverse is now documented in
  `docs/UDS-protocol.md` and exercised by the integration tiers; what remains is its use as a test
  fixture, in `tests/test_uds.py`. Do not point it at anything except a socket we started with
  `listen` under test overrides. Real sends go through `send`, which picks a transport from the
  target's kind and authenticates with that target's token.

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

### Running the tests in Docker

`agent-bus` discovers live agents on the machine it runs on, so testing it on
your working machine means the suite can see — and message — your real sessions.
The repo ships a container that gives the tests their own `HOME`, bus home,
socket directory and PID namespace:

```sh
docker compose run --rm test    # unit suite; no credentials
docker compose run --rm e2e     # full integration suite; needs API keys
```

See `tests/integration/README.md` for the credentials and for pinning a specific
harness version.

### Running from source

```sh
gh repo clone danbarua/agent-bus && cd agent-bus
python -m pip install -e .
agent-bus --help
```

For Claude or omp: use `python -m agent_bus` or the installed `agent-bus` CLI. No plugin is
required for Claude.

## Installing it

```sh
pip install agent-bus-team        # or: uv pip install -e .
```

That is the whole of it. A peer joins the bus by running the MCP server —
`agent-bus mcp` calls `session_start()` on startup and `session_end()` on exit, in-process, which
registers the session and publishes its UDS listener. Point your harness's MCP config at it.

There is no plugin. This repo used to carry a `plugin.json`, hook scripts and a CLI wrapper for
`grok plugin install`; all of it existed to run `agent-bus` from Grok's Bash tool, which is exactly
what the MCP server removed. A plugin manifest that wires up nothing on install only advertises a
capability that is not there. Whether a plugin is the right shape at all is still open — see
[hooks-in-foreign-harnesses.md](https://github.com/danbarua/agent-bus/blob/main/docs/hooks-in-foreign-harnesses.md)
for why the last attempt was worse than nothing.

`agent-bus hook session-start|session-end` remains as a CLI entry point for a harness that has
hooks and no MCP. It exits 0 always, never blocks on stdin, and writes only to stderr.

**Claude Code: install NOTHING.** No plugin, no `.claude-plugin/`, no `.mcp.json`, no skills or
slash commands. Claude sees a Grok/omp/codex peer through native `ListAgents`/`SendMessage`
because that peer's `listen` published the session file and socket.

The agent-facing surface is the MCP tool set above — the tool descriptions carry the consent
rule. Use the CLI or `import agent_bus.store` for everything else.

## Development / test

```sh
python -m pytest tests/ -q --tb=line
AGENT_BUS_HOME=/tmp/ab-test python -m agent_bus list --json
```

## Limitations / non-goals

- No impersonation of Claude's full protocol — `listen` and the claude transport implement the UDS peer messaging subset only (see [UDS-protocol.md](https://github.com/danbarua/agent-bus/blob/main/docs/UDS-protocol.md)).
- No auto-start of other agents.
- Herdr TTY injection is a separate channel (not used here).
- Inboxes are per bus-home; multiple users would need separate homes or sync.

This is intentionally small and boring.
