# Claude Code vs Grok Build vs Codex vs agent-bus

This document compares what Claude Code, Grok Build, Codex, and agent-bus do
now. It states what each finding means for agent-bus.

Sources:

- Claude Code: `docs/harnesses/claude-code-presence.md`. It reviews the
  2.1.239 binary strings and live `~/.claude/sessions/*.json` files.
- Grok Build: `docs/harnesses/grok-build-ipc-reference.md`. It reviews
  `danbarua/grok-build` at commit `07b2f71`, with `file:line` citations.
- Codex: `docs/harnesses/codex-messaging-reference.md`. It reviews
  `openai/codex` at commit `c9b19de`, with `file:line` citations.
- agent-bus: this repo's current behavior, per `docs/identity-and-peering.md`.

## Grok Build local session messaging

Grok Build's local shell has no session-to-session messaging. The source
review found no RPC method, no envelope variant, and no routing function for
it (grok-build-ipc-reference.md §3).

Grok Build's leader socket is a client-to-leader multiplexer. Many client
processes attach to one leader. The leader hosts sessions in-process. It is
not a peer-to-peer channel.

Two mechanisms look adjacent but are not session-to-session messaging:
same-session fan-out to multiple attached clients, and cloud relay to x.ai.

This finding covers the local binary in `crates/codegen/xai-grok-*`. Grok
Bots, the cloud product, does have bot-to-bot messaging and is a separate
system (see below).

agent-bus does not duplicate a facility of the local Grok shell. It supplies
session-to-session messaging that the local shell lacks, using Claude Code's
wire protocol.

## Grok Bots (cloud product)

<https://docs.x.ai/grok-bot/overview>

Grok Bots are "persistent, named teammates" that "message each other, share
context in threads or group chats, and pass ownership so you are not the
router between tools". "Multiple Bots share one user-scoped computer and can
run in parallel", sharing files, browser sessions, and app logins for
handoffs. Grok Bots' coordination is not peer-to-peer IPC. It runs through
this shared computer and a messaging and threading layer.

**Unknown from the product page.** The overview is product-level. It states
no API names, no endpoints, and no message envelope. It states no delivery
semantics: no acknowledgments, no persistence, no queueing. It states no
naming or addressing scheme, and no presence or status reporting. A
wire-level comparison with Claude Code or agent-bus needs an API reference
this project does not have.

**Three axes separate Grok Bots from agent-bus.**

| | Grok Bots | agent-bus |
|---|---|---|
| Where | hosted, user-scoped cloud computer | the user's own machine |
| Who | Grok Bots with each other | Claude Code ↔ grok ↔ omp ↔ codex, cross-vendor |
| Substrate | shared filesystem/browser/logins plus threads | local UDS, Claude Code's peer protocol |

agent-bus's niche is local and cross-vendor. It makes a non-Claude process on
this machine appear in Claude Code's native `ListAgents`. It makes that
process messageable with Claude Code's native `SendMessage`. Grok Bots does
not do this. Grok Bots would need to speak Claude's local protocol to do it.

A published Bots API reference could change this comparison. It would let
agent-bus check whether Grok Bots' addressing, delivery, and presence model
are close enough to mirror in vocabulary. The `RosterActivity` mapping later
in this document is an example of that kind of vocabulary mirroring.

## Feature comparison table

| | Claude Code | Grok Build | Codex | agent-bus today |
|---|---|---|---|---|
| Socket role | peer ↔ peer between sessions | client ↔ leader (one leader hosts many sessions) | client ↔ app-server (one server multiplexes many connections) | publishes a Claude-shaped socket per peer |
| Transport | AF_UNIX, `/tmp/cc-socks/<pid>.sock` | AF_UNIX `~/.grok/leader.sock`; Named Pipe on Windows | stdio, AF_UNIX, or TCP — the UDS and TCP paths are both WebSocket | AF_UNIX, Claude's path convention |
| Framing | newline-delimited JSON | 4-byte big-endian length prefix + JSON, 64 MB cap | JSON-RPC-*shaped* (no `jsonrpc` field); NDJSON on stdio, one message per WebSocket frame otherwise | newline-delimited JSON |
| Auth | `peerToken` from a `0600` key file, auth frame first | **none** — filesystem permissions only | local: filesystem permissions only (`0700` dir, `0600` socket), no peer-cred check. remote TCP: optional bearer/JWT, mandatory only for non-loopback | implements Claude's token scheme |
| Session discovery | one `sessions/<pid>.json` per session | no local session list | SQLite `state_5.sqlite` + rollout JSONL; no pid or socket field | writes Claude-shaped session files |
| Liveness | pid running **and** `procStart` matches | residency + turn-state, in-process | per-thread advisory lock files; daemon probed by socket connect | pid only |
| Session→session messaging | yes | **none** | yes — `thread/queue/add` | file bus + UDS shim |
| Message persistence | none; at-most-once to a live peer | n/a | **SQLite, survives restart of both server and target** | durable file inboxes |
| Identity | mutable name; `formerNames` grace | immutable UUIDv7 id; mutable title | immutable UUIDv7 `ThreadId`; mutable `name` | roster name + published socket name |
| Rename | old name kept in `formerNames` with `until` | overwritten, no trace | overwritten, no alias or history | `formerNames` with `until`, resolves for a fixed grace window (#148) |
| Presence vocabulary | `status` string (`idle`, `busy`, …) | `RosterActivity`: `Working`, `Idle`, `NeedsInput`, `Dormant`, `Completed`, `Dead` | `ThreadStatus`: `NotLoaded`, `Idle`, `SystemError`, `Active{WaitingOnApproval\|WaitingOnUserInput}` | writes `idle` at startup; `set-status` updates it thereafter |
| Status persistence | in the session file on disk | in-memory, per leader | in-memory, per app-server; not in SQLite | in the session file, refreshed on `set-status` and touched on every MCP call |

## Codex message queue

Codex is the only one of the three harnesses with store-and-forward
messaging.

The `codex queue --thread <THREAD> --message <TEXT>` command submits
`thread/queue/add`. `thread/queue/add` writes the message to a SQLite table,
`queued_items`, in `queue_1.sqlite`. It writes the message before it
attempts to wake the target.

- The write succeeds even if the target is busy. The row is written
  unconditionally and sits until the active turn ends.
- The write also succeeds if the target is not loaded in any process. Only
  an archived thread rejects the write.
- It survives restarts of both sides. The queue is keyed on `thread_id`, not
  on any live handle. The Codex source review confirms a queued item
  dispatches after both the app-server and the target session restart.
- The cap is on capacity, not time. Codex allows 100 items per queue, with
  no TTL, no dead-letter, and no expiry.

The sender learns only that Codex persisted the message. The
`thread/queue/add` response carries a `QueuedSubmission` field and no
delivery field. Codex reports actual dispatch only as an async notification
to a subscribed client.

Codex arrives independently at durable inboxes. Messaging a session that is
busy, cold, or restarting requires a store. Codex reaching this design on
its own supports agent-bus's file inboxes as the right shape for this
problem.

agent-bus refuses a new message addressed to a peer with no running process.
It does not queue that message for a future process to read.

An inbox holds unread mail after its peer's process exits
(`store.prune_dead_roster`). Mail sent while the peer was alive stays
readable after the peer exits. See
`../tests/agent_bus/presence/test_presence_vs_mailbox.py` for a specific
example:

```
receiver unavailable: recipient is registered as a other peer but its process
is not running, so nothing would read this. Not sent. (Mail already in its
inbox stays readable.)
```

Codex writes the row unconditionally. The row then waits for the thread to
load. agent-bus tells the sender immediately instead. A peer in agent-bus is
a live process. A message the peer will never read is worse than a returned
error.

What agent-bus keeps does not keep long. A message expires after one hour.
Codex's queue caps on capacity at 100 items, and never on time.

## Codex weaknesses versus Claude

- Codex's local auth is filesystem permissions only. It uses a `0700`
  directory and a `0600` socket, with no peer-credential check anywhere in
  the app-server family. This matches Grok's posture, and is weaker than
  Claude's `peerToken`. A localhost TCP listener can run with no bearer
  token. Codex requires auth only for non-loopback binds.
- Codex rename discards the old name, with no alias table or history. This
  matches Grok. Claude's `formerNames` is the only implementation of a grace
  period among the three. It is a model agent-bus can copy.
- Codex injects a queued message as a plain user turn, with no wrapper or
  system-reminder framing. The message is indistinguishable from something
  the user typed. Claude marks a peer message as
  `<cross-session-message from=...>`. This marking lets a recipient treat
  the message as untrusted input. agent-bus should keep this marking.

## Codex duplicate-name claim

A Codex PR description claimed Codex "rejects ambiguous or duplicate names."
The Codex source review found this claim false. Codex has no `UNIQUE`
constraint on the name column, and no ambiguity error anywhere in the
codebase. The resolver silently picks the most-recently-updated match. The
type for this behavior is named `SessionNameMatch::First`. agent-bus should
avoid this pattern: it should not resolve duplicate names silently to the
most recent match.

## Grok and Claude presence mapping

Grok publishes `RosterActivity` per session in two ways
(grok-build-ipc-reference.md §5). One way is request and response over
`x.ai/sessions/list`. The other way is a broadcast, `x.ai/sessions/changed`,
on every state transition: spawn, turn start, turn end, teardown.
`grok_leader.py` subscribes to this broadcast. `grok_leader.py` maps
`RosterActivity` onto the session file's `status` field:

| `RosterActivity` | `status` |
|---|---|
| `Working` | `busy` |
| `Idle` | `idle` |
| `NeedsInput` | `busy` (Claude has no distinct "blocked"; the listing shows `busy`) |
| `Dormant`, `Completed`, `Dead` | stop publishing — the peer is not addressable |

The mapping exists because the two liveness models disagree. Grok's own
documentation says liveness is "residency + turn-state, not a pid"
(grok-build-ipc-reference.md §5). A Grok session is an in-process actor with
no pid of its own. Claude's model uses pid plus start time.

agent-bus bridges the two models. It gives each peer a listener process with
a real pid. This is why the shim exists, and why one peer means one socket.

## Grok leader socket authentication

Grok's leader socket has no peer credentials, no token, and no explicit
socket permissions (grok-build-ipc-reference.md §1). The socket relies only
on the process umask on `~/.grok/leader.sock`. Any process that can reach
that path can register as a client. agent-bus implements Claude Code's
`peerToken` scheme for its own socket. agent-bus should keep this stronger
scheme.

## Grok ambient credential exposure

The Grok source review flags an ambient-environment leak
(grok-build-ipc-reference.md §6). Grok sets `GROK_LEADER_SOCKET`
process-wide, using `std::env::set_var`, when a process passes
`--leader-socket`. Every child process inherits `GROK_LEADER_SOCKET`,
including hook scripts and MCP stdio servers. `XAI_API_KEY` and the other
`FIRST_PARTY_CREDENTIAL_ENV_VARS` are also ambient in this way.

Grok's codebase has a `scrub_first_party_credentials` helper. Grok applies
this helper to one child type only, the auth-provider helper. Neither the
hook spawn path nor the MCP spawn path calls this helper.

Combined with the unauthenticated leader socket, this leak has a
consequence. A third-party MCP server or hook script can read
`GROK_LEADER_SOCKET`. It can then dial the leader's control surface. This is
Grok's issue. agent-bus ships an MCP server into that same position, inside
a harness's child process. agent-bus's own child environment content is
relevant to this issue.

## GROK_SESSION_ID and kind detection

`detect_kind()` keys on `GROK_HOOK_EVENT` and `GROK_PLUGIN_ROOT` to identify
a Grok peer. Grok never sets `GROK_SESSION_ID` process-wide. Every site uses
`Command::env`, not `std::env::set_var` (grok-build-ipc-reference.md §6).
`GROK_SESSION_ID` is set on the Bash and PTY tool's environment
(`terminal/pty_session.rs:256-262`).

A shell spawned by Grok carries `GROK_SESSION_ID`. Any process launched from
that shell, including a Claude Code session, inherits `GROK_SESSION_ID`.
`detect_kind()` avoids `GROK_SESSION_ID` for this reason: a Claude Code
session could otherwise be misidentified as a Grok peer.
