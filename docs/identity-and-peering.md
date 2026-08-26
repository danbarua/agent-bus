# Identity and peering — what the code does today

Current behaviour as of 2026-08-25, written from observed runs. This is a
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

## One bus, two ways in

There is one bus: `AGENT_BUS_HOME` (default `~/.agent-bus`) holds a roster of
live agents and one JSONL inbox per agent. That is the whole of it.

A message can reach an inbox two ways, and once there they are the same thing:

- **Directly** — the `agent-bus` CLI or the MCP tools call `send_message()`.
- **Over UDS** — `~/.claude/sessions/<pid>.json` plus
  `/tmp/cc-socks/<pid>.sock`, the protocol Claude Code's own `ListAgents` /
  `SendMessage` speak, documented in `UDS-protocol.md`. An inbound frame is
  persisted by calling the same `send_message()`, addressed to the same roster
  id, so a reader cannot tell which path a message took.

Do not model these as two buses. The socket is not a parallel channel with its
own inbox; it is how a Claude peer reaches this bus and how acks return, since
an outbound frame names `uds:<our_sock>` as its return address. A peer without a
listener cannot be acked at all.

Claude itself reads neither the roster nor the inbox — it only ever sees the
socket, through its own harness.

## How a peer gets an identity

`lifecycle.session_start()` runs when the MCP server starts, or from a
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
so `detect_kind()` returns the fallback.

### `pending` and `other` are different facts

They shared one word until they were split, and the word hid a bug.

| kind | means | changes later? |
|---|---|---|
| `pending` | nobody has connected and identified themselves **yet** | yes — it exists to be replaced |
| `other` | there **is** an agent, it is addressable, and no discovery adapter can name its type | no — this is a settled answer |

`other` is a positive claim, not a gap. An agent never has to identify its kind
to work: pi is `other` and always will be, and it messages Claude sessions
perfectly well. Nothing may treat `other` as something to fill in later.

`pending` is what the MCP server registers as, because at that moment it
genuinely knows nothing — the harness passes its MCP child no identifying
environment at all. The name is `pending-<pid>`.

Why the split matters: the `initialize` handshake upgrades a peer *only* from
the unclaimed state. While that state was spelled `other`, the guard could take
a correct kind off a peer that had one — a pi peer running the MCP server would
have been overwritten.

### Claiming a name

The MCP surface has a `register` tool (name, kind). It re-registers under the pid
`session_start()` already claimed, so it renames that entry rather than adding a
second one, and it rewrites the published session file so the socket advertises
the same name. An agent that never calls it keeps whatever the handshake
settled on — its harness's kind if the client identified itself, `other` if it
connected and could not be placed.

The CLI equivalent is `agent-bus register --name X --kind K --pid P`. `--pid`
matters: `register()` defaults to the calling process, and a short-lived
`uv run agent-bus` exits immediately, so the entry is pruned as dead before the
next command runs.

## An id is an address

An entry's id says *how to reach this agent and how to know it is still there*,
not merely which row it is. Canonical spelling is `<kind>:<space>:<value>`,
where the space is a namespace of identifiers sharing a liveness rule:

| space | example | still there when |
|---|---|---|
| `bus` | `8054898a-70b8-…` | the process that registered is alive |
| `session` | `claude:a4775baa-…` | the harness's process is alive |
| `pid` | `codex:pid:4242`, `omp:tty:900` | that process is alive |
| `thread` | `codex:thread:01a01cb8-…` | **always** — a thread is a document, not a process |

Legacy two-part ids (`claude:<sessionId>`) parse as `session` addresses and are
never re-rendered: an inbox filename is derived from the id, so canonicalising
one would move its mailbox out from under it.

### Aliases: the same agent, two addresses

An agent registers under a `bus` uuid *and* is separately discovered under its
harness's `session` address. With nothing linking the two, `agent-bus list`
showed one Claude session twice, under two different names — a registered
`claude-a4775baa` and a discovered `exo-ledger`, both pid 58291.

`session_start` now records the harness address as an alias, so the two
reconcile into one row. Entries written before aliases existed are reconciled
retroactively by matching `(kind, pid)`. That comparison deliberately ignores
`procStart`: session files publish `Fri Aug 21 20:16:00 2026` while
`ps -o lstart=` gives `Sun 23 Aug 21:21:13 2026` — two formats under one field
name, so comparing them yields silent false negatives.

When a merge happens the roster entry wins on identity — id, name and kind, the
identity the agent claimed on the bus. The discovered record supplies `status`,
which is the thing that changes moment to moment, and **fills gaps** in
`native`: the merge is `{**discovered, **roster}`, so the roster wins any key
the two both hold.

There is a third address, and it used to be a duplicate: the listener's own
published session. `session_start` records the *harness's* address as an alias
but nothing recorded this one, so a peer registered under its host pid — every
MCP harness — was listed once as itself and once as its own socket.

`run_listen` now records it the same way, with the same `address.mint` call:
it publishes `sessionId` as the entry's own id and registers
`agentbus:session:<entry-id>` as an alias. No new field in the session file and
no new branch in discovery — the address is minted from the entry id, which
`register()` keeps across a rename, so a claim moves the name and the published
address still resolves.

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
- publishes `~/.claude/sessions/<listener_pid>.json` with `agentBus: true` and
  a `sessionId` that is the roster entry's own id, plus a `0600` `.key` holding
  a `peerToken`
- registers `agentbus:session:<entry-id>` as an alias, so the address it just
  published resolves to the entry that published it
- adopts the host's existing roster entry when started with `--pid` and that
  host has already registered, so one peer has one identity. With no such
  entry it registers itself — a listener starting anonymous is normal, not a
  fault
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

**Peer → Claude.** `agent-bus send <name> -m ...`, routed to the claude
transport by the target's kind, which dials the target's
socket over UDS; the message arrives in the Claude session's conversation.
This requires the sending peer to have a listener of its own, because the
outbound frame carries its socket as the reply address. The listener only exists
while the peer's MCP server is running — a run that never touches an MCP tool has
no listener, and the send fails with
`[send-peer] err: cannot determine our listen socket`.

The file-bus `send_message` tool reaches a Claude conversation too. It is the
same router: `commands.messages.send` picks the transport from the target's
kind, so a Claude recipient gets the UDS delivery above and a file-inbox peer
gets a file inbox. One code path, which is the point of the bus. (This document
previously said the opposite; it was true before every peer got a mailbox.)

## Lifetime

Presence and mail have different lifetimes, and conflating them cost real
messages. The roster is pruned of dead pids on read, so a peer stops being
*live* the moment its process exits — but **its mail is not thrown away with
it**. An entry with unread messages is kept, because the entry is the only
pointer to the mailbox, and deleting it on exit meant a reply to an agent that
had just exited failed with `no such agent` while the queued mail became
unreachable.

Delivery to a peer that is not live is refused at the sender with
`Receiver Unavailable`, rather than filing into an inbox nobody will drain.
Reading is deliberately not gated the same way: mail already on disk stays
readable.

For a single-turn peer such as `omp -p`, a reply still has to arrive while the
peer is running for the peer to *act* on it. What changed is that the message
survives to be read on its next run instead of vanishing.

## Starting a listener by hand

An implementation detail, kept here for debugging. It is not how a peer joins
the bus and should not be read as a procedure to follow: `session_start()` does
that, from `agent-bus mcp` or a session-start hook, and it is also what detects
the kind.

```sh
agent-bus listen --name my-bus --pid <host-pid>
```

`listen` registers its own entry, so there is no separate `register` step. A
standalone `agent-bus register` from a shell is a wrong turn worth naming: it
claims the pid of the command, the command exits, and the dead entry is pruned
on the next `list`. A peer started this way is kind `other`, because nothing in
a bare shell identifies a harness.

To watch the path end to end, run it under the test overrides
(`AGENT_BUS_HOME`, `AGENT_BUS_SOCK_DIR`, `AGENT_BUS_SESSIONS_DIR`). A Claude
Code session's `/list-agents` then shows the peer, and anything sent from there
lands in both the capture file and the inbox.

## Known rough edges

Recorded as observed, not as a to-do list.

- A peer's identity depends on its MCP server being alive; nothing registers it
  otherwise.
- `detect_kind()` recognises only grok and claude, so every other harness is
  `pending` until the `initialize` handshake places it, and `other` if that
  handshake cannot.
- Presence still depends on a process. A peer that is down is refused at the
  sender rather than queued, so the bus holds mail for an agent that *was*
  there but cannot accept mail for one that has never been.
