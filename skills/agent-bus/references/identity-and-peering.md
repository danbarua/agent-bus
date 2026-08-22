# Identity and peering — what the code does today

Current behaviour as of 2026-08-23, written from observed runs. This is a
description, not a design. Where behaviour is awkward it is recorded as
behaviour, not as a plan.

## The asymmetry

Claude sees nothing. A Claude Code session has no agent-bus plugin, no MCP
server, no inbox and no configuration. It uses its native `ListAgents` and
`SendMessage` and peers simply appear, like any other Claude session. Nothing on
the Claude side is aware this project exists.

Peers see agent-bus. Everything below — the roster, the inboxes, the MCP tools,
the UDS listener — is peer-side. Its job is to make a grok, omp or codex process
look like a native Claude peer from the outside.

So the two halves of this document are not symmetric, and should not be read as
though they are.

## Two transports

- **File bus** — `AGENT_BUS_HOME` (default `~/.agent-bus`): a roster of live
  agents, one JSONL inbox per agent. Reached by the `agent-bus` CLI and by the
  MCP tools.
- **Native UDS** — `~/.claude/sessions/<pid>.json` plus `/tmp/cc-socks/<pid>.sock`:
  the protocol Claude Code's own `ListAgents` / `SendMessage` speak. Documented
  separately in `UDS-protocol.md`.

A peer is present on both, and they carry different things: the roster holds
identity, the socket carries live delivery. Both are maintained by the peer.
Claude reads neither — it only ever sees the socket, through its own harness.

## How a peer gets an identity

`plugin_host.session_start()` runs when the MCP server starts, or from a
session-start hook. It:

1. `detect_kind()` — `grok` if `GROK_HOOK_EVENT` or `GROK_PLUGIN_ROOT` is set,
   `claude` if `CLAUDE_PLUGIN_ROOT` or `CLAUDE_PROJECT_DIR` is set, otherwise
   `other`. These are the only signals used.
2. `host_pid()` — for grok, the pid from `~/.grok/active_sessions.json`; for
   claude, the pid from the matching `~/.claude/sessions/*.json` **if that pid is
   alive**; otherwise `os.getppid()`.
3. `derive_name()` — `<kind>-<first 8 chars of session id>`, or `<kind>-<pid>`
   when there is no session id. Grok sessions additionally take their session
   title as the name when one exists.
4. `register()` on the file bus under the host pid.

**omp is not detected.** An MCP server launched by omp inherits exactly one
identifying variable, `PI_NO_TITLE=1`. There is no session id and no agent dir,
so `detect_kind()` returns `other` and the name is `other-<pid>`.

### Claiming a name

The MCP surface has a `register` tool (name, kind). It re-registers under the pid
`session_start()` already claimed, so it renames that entry rather than adding a
second one, and it rewrites the published session file so the socket advertises
the same name. An agent that never calls it stays `other-<pid>`.

The CLI equivalent is `agent-bus register --name X --kind K --pid P`. `--pid`
matters: `register()` defaults to the calling process, and a short-lived
`uv run agent-bus` exits immediately, so the entry is pruned as dead before the
next command runs.

## Who a message is from

`store.send_message()` resolves the sender with `get_self()`, which walks the
caller's ancestor pids and matches them against the live roster. An explicit
`from_name` overrides it and is used by the CLI. The MCP `send_message` tool does
not expose `from_name`, so a peer cannot assert another peer's identity.

If nothing matches, the sender is `anonymous` with a random id — which is
delivered but unaddressable, since there is no name to reply to.

## The UDS listener

`session_start()` starts a detached listener for **every kind except claude**
(Claude sessions already have their own socket). The listener:

- binds `/tmp/cc-socks/<listener_pid>.sock`
- publishes `~/.claude/sessions/<listener_pid>.json` with `agentBus: true`, plus
  a `0600` `.key` holding a `peerToken`
- adopts the host's existing roster entry when started with `--pid`, rather than
  registering itself, so one peer has one identity
- writes `listeners/<host_pid>.pid` under `AGENT_BUS_HOME`, containing the
  **listener's** pid

That publication is what makes a non-Claude peer appear in Claude's native
`ListAgents`.

Ordering: the socket is bound before the session file is written, so identity
comes from `register()` and a bind failure cannot leave a stale registration.
There is therefore a brief window where the socket exists and the session file
does not.

`session_end()` stops the listener for every kind that gets one, and unregisters
by pid.

## Delivery, in each direction

**Claude → peer.** Native `SendMessage` to the peer's name. The frame reaches the
listener, which persists it into the peer's file inbox and acks on a separate
dial-back connection. The peer reads it with `get_inbox`.

**Peer → Claude.** `agent-bus send-peer <name> -m ...`, which dials the target's
socket over UDS; the message arrives in the Claude session's conversation.
This requires the sending peer to have a listener of its own, because the
outbound frame carries its socket as the reply address. The listener only exists
while the peer's MCP server is running — a run that never touches an MCP tool has
no listener, and `send-peer` fails with
`[send-peer] err: cannot determine our listen socket`.

The file-bus `send_message` tool does **not** reach a Claude conversation. It
writes to the target's inbox file, which a Claude session does not poll.

## Lifetime

The roster is pruned of dead pids on read. When a peer's process exits, its
roster entry and its inbox are both removed. A message sent to it afterwards
fails with `no such agent`, and anything already queued is gone.

For a single-turn peer such as `omp -p`, this means it is unaddressable the
moment it exits. A reply must arrive while the peer is still running.

## Known rough edges

Recorded as observed, not as a to-do list.

- A peer's identity depends on its MCP server being alive; nothing registers it
  otherwise.
- `detect_kind()` recognises only grok and claude, so every other harness is
  `other` unless it calls `register`.
- Mailboxes do not outlive their process, so the bus cannot hold a message for an
  agent that is currently down.
