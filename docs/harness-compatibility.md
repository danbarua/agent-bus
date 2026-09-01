# Harness compatibility matrix

A map of what each harness provides and what agent-bus must therefore supply.
Written before the adapter refactor, on the principle that the abstraction
should fall out of the map rather than the other way round; kept current since,
because `src/agent_bus/adapters/` is now shaped exactly like it.

Sources: `docs/comparison-note.md` and the source reviews in `docs/harnesses/`.

## The shape, in one line each

The matrix is detail. The taxonomy is:

- **Claude** — special case. `agent-bus listen` and it just works, both
  directions, with nothing installed on its side.
- **Codex** — special case the other way round. We write *into* it natively; it
  discovers and writes *out* through our MCP server.
- **Grok** — MCP server + `watch`.
- **omp** — MCP server + `watch`. The same shape as Grok.
- **pi** — no MCP, no hooks. CLI `listen` + `watch`, driven from its shell. This
  is also the shape any unknown harness falls back to.

That Grok and omp are one shape is the point. This matrix used to call Grok's
inbound transport "none exists" and omp's "file inbox only", which are two
descriptions of the same fact: **agent-bus supplies the transport**, and `watch`
is how the agent comes to notice it. Nothing distinguishes them.

## The axes

| axis | the question it answers |
|---|---|
| **Discovery** | two questions, not one — see below |
| **Lifecycle** | where can agent-bus attach to session start/end to register an identity? |
| **Transport** | how does a message reach this harness, and is that transport **its own or ours**? |
| **Wake** | once a message has arrived, what makes the agent look at it? |

**Discovery runs in two directions, and conflating them hid a fact.** Reading a
harness's registry and appearing in it are independent capabilities. We read
grok's and omp's registries without either being able to see us that way; we
appear to Claude by writing a file no other harness reads. Only Claude is
symmetric, which is exactly why Claude is the zero-install case.

Identity and presence ride on discovery — they are fields in whatever the
surface is.

## CI-shaped and use-shaped are different questions

Every row below answers *what is possible*. Two things get built on that, and
they want opposite behaviour:

| | wants |
|---|---|
| **automated CI** | a run nobody watches, that ends, and that leaves something pytest can read. A blocking call is ideal — it is a deterministic point to assert on. |
| **real use** | an agent that receives information and carries on working, while talking to a person and to its peers. A blocking call is the opposite of that. |

The **Woken headless?** row is the first kind. `park` there means *this harness
can be held at a known point in a hands-off run* — not *this is how it behaves
when someone is using it*. Read the second way, it produces an agent that sits
blocked and declines work.

A prompt written for a test is not a prompt to hand a person, and neither is a
defect in the other.

`tests/agent_bus/integration/` carries this distinction applied to every
`tests/agent_bus/integration/test_*.py` file, one real captured sequence
diagram per test, each stating outright which parts are CI's shape and which
are the real one.

## The matrix

| | Claude Code | Codex | Grok Build | omp | pi |
|---|---|---|---|---|---|
| **Can we discover it?** | yes — `~/.claude/sessions/<pid>.json` | **no, by choice** — no pid in its thread metadata | **no** — `active_sessions.json` is pruned to `[]` at startup and is empty while sessions run; nothing in `~/.grok` records a live session's pid (#184) | yes — `~/.omp/run/daemons/*/clients/*.json` | no adapter |
| **Can it discover us?** | **yes** — `listen` writes the session file it already reads | MCP `list_agents` | MCP `list_agents` | MCP `list_agents` | `agent-bus list` from its shell |
| **Lifecycle attach** | none needed | MCP server start | MCP server start (hooks exist, unused) | MCP server start | none — the prompt runs `listen --pid $PPID` |
| **Inbound transport** | **its own** — UDS peer protocol; it dials us | **its own** — `thread/queue/add` on the app-server socket | **ours** — file inbox | **ours** — file inbox | **ours** — file inbox |
| **Wake** | native — the harness delivers into the conversation | native — a queued item auto-wakes an idle thread | `watch`, feeding its `monitor` | `watch`, feeding its `hub` — see the row below | `watch`, or `inbox` from the shell |
| **Woken headless?** | **push** — its `Monitor` event starts a turn after the last one ended | **no push** — `exec_command`/`write_stdin` only ask; nothing arrives unbidden | **push** — native `monitor`, persistent, and it keeps `grok -p` alive | **park** — `hub` on `watch`; the call is in [omp.md](harnesses/omp.md) | **no push** — shell only |
| **Outbound** | native `SendMessage` | MCP `send_message` | MCP `send_message` | MCP `send_message` | `agent-bus send` |
| **agent-bus supplies** | **nothing** | the roster | transport + wake | transport + wake | everything, through the CLI |

**"Woken headless" was measured, and the first measurement was wrong.** Each
harness got the same brief — start whatever tool turns a command's output into
events, point it at `agent-bus watch`, then stop — and was then sent a message.
Claude and Grok woke and acted. Codex, omp and pi all answered `NO_MONITOR`.

omp's answer was true and the question was bad: it was asked whether it has a
tool *named* `monitor`. What it has is `hub`, which supervises project-scoped
processes and returns their output, and the brief's "do not simulate one" ruled
that out before it could be tried.

`hub logs` with `follow` is the call — one call, no bookkeeping, and it returns
the lines. `hub wait` with a `pattern` also parks, and was what the first probe
used, but it re-matches an accumulating buffer and spins on the second wake;
[omp.md](harnesses/omp.md) has the detail.

Blocking omp is a **CI technique** either way. It is how a hands-off run gets a
known point to assert on, and handing the same shape to a person produces an
agent that sits blocked and declines work — see above.

**Push and park are the distinction worth drawing**, not woken and not-woken:

- **push** — the turn *ends*, and an event starts a new one. Cheap to leave
  running: an idle peer costs nothing until mail arrives.
- **park** — the turn stays open, blocked in a tool call. Works just as well
  for a conversation, and the agent is occupied while it waits.

Codex and pi have neither, measured. Both have a shell, so both could in
principle block on a read of `watch` — untested, and not claimed here.

A harness with no push and no park can still be sent to and can still read its
inbox — it just cannot be *told*, so something has to make it look. `watch` in
omp's Wake cell used to say nothing about what consumed it; now it does.

**omp has a second trick, and it is the stranger one.** Its `eval` tool is a
live Python (IPython) kernel, so agent-bus is an *import* rather than a
subprocess — `sys.path`, `from agent_bus import store`, and the roster is in
hand, in process. No other harness here can do that.

Two peers holding a seven-message conversation, one Claude and one Grok, each
woken only by the message before it, is
`tests/agent_bus/integration/test_two_agents_hold_a_conversation.py`.

**Durability was a row here and should not have been.** It read "none" for
Claude against "SQLite, survives restarts" for Codex, comparing what each
harness's own store happens to do. Everything that reaches a peer through us is
in the file inbox and survives a restart either way. Whether the *harness* also
persists it is its own business — Claude deliberately does not — and the answer
changes nothing we build.

## What "transparent" actually requires

Claude Code is zero-install because two conditions hold at once:

1. It **discovers peers from something we can write** — a per-session JSON file.
2. It **delivers to peers over something we can serve** — it dials our socket.

Both, and only both, produce "it just works". Neither alone is enough, and the
matrix above is really a table of which harnesses satisfy which condition.

## Codex: can we hook in transparently?

Asked directly, and the answer is **half — and the useful half is the one we
would want first.**

**Sending *into* Codex: yes, transparently, today.** `thread/queue/add` is an
RPC on the app-server's Unix socket, and local auth is filesystem permissions
only — `0700` directory, `0600` socket, no peer-credential check. Any process
running as the same user can connect, `initialize`, and queue a message to a
thread by id. Nothing needs installing on the Codex side. Better still, the
wake is native: a queued item auto-wakes an idle thread and is injected as a
plain user turn, so delivery and wake come together.

That makes Codex the *easiest* of the three to message, and the only one where
durability comes free.

**Making a non-Codex agent *appear* as a Codex thread: no, and we should not
try.** Codex discovers threads from `state_5.sqlite` — a shared SQLite database
with a migration chain (`0001_threads.sql` … `0041_threads_name.sql`), owned and
schema-versioned by another product. Writing rows into it to fake a peer would
be inserting into someone else's private, migrating store. That is a different
proposition from writing a self-contained per-session JSON file, and it breaks
on their next migration.

So the shape for Codex is asymmetric:

- **outbound to Codex** — direct, no install, no plugin
- **inbound from Codex** — ~~needs a Codex-side affordance, its hooks are the
  attach point~~ **done, and not via hooks.** A Codex session that runs our MCP
  server is on the bus: `serve()` calls `session_start()`, and the harness-join
  test has Codex registering and delivering a message. Hooks were never needed
  and no longer exist here.

Worth noting this inverts the Grok situation. Grok needs us to supply the entire
transport because it has none; Codex has a good one we can use but a discovery
surface we cannot join.

### What a Codex MCP peer can and cannot know (probed 2026-08-24)

Codex tells its MCP child **nothing about the session**. Verified with a
recording MCP server rather than inferred: the child's entire environment was

    HOME LANG LOGNAME PATH SHELL TERM TMPDIR USER __CF_USER_TEXT_ENCODING

which matches the allowlist in `rmcp-client/src/utils.rs:162-175`. No thread id,
no session id, no socket path.

The consequence is a deliberate non-feature: **a Codex bus peer cannot be linked
to its own Codex thread.** A registered Codex-kind peer with no `native.threadId`
still has a mailbox -- a roster entry is a `bus` address, and `has_mailbox` is
unconditionally true there -- but no *route* to it: routing selects the Codex
transport regardless of whether a thread id exists, and that transport refuses
anything without one -- "not a thread" -- rather than falling back to the file
bus. `thread/queue/add` still addresses threads
directly by id or name; that is a separate path this bus does not touch. We do
not guess the thread link from cwd and recency — two sessions in one repo would
collide, and misrouting a message is worse than not routing it. If Codex ever
exposes the thread id to its MCP children, one explicit alias completes the link.

What Codex *does* say is who it is, in the `initialize` handshake:

| harness | `clientInfo.name` | version seen |
|---|---|---|
| Codex | `codex-mcp-client` | 0.149.0 |
| omp | `omp-coding-agent` | 1.0.0 |
| Grok | `grok-shell-<our server name>` | 1.0.5 |

That is now how an MCP-only peer gets its kind. It starts as
`pending-<pid>` — nobody has connected and identified themselves yet — and
the handshake settles it, to `other` if the client cannot be placed. Grok additionally passes `GROK_SESSION_ID` to MCP children, which we
read **only** after its clientInfo matched; see the note in
`adapters/lifecycle/grok.py::detect`.

**Since deleted.** `adapters/discovery/codex.py` read
`~/.codex/process_manager/chat_processes.json`, which has been `[]` since
31 July on this machine. Codex records no pid anywhere in its thread metadata,
so a process-shaped adapter structurally cannot work, and it is gone -- see the
docstring in `adapters/discovery/__init__.py`. This is why the matrix answers
"no, by choice" to discovering Codex: a Codex session joins by registering
through the MCP server, which the clientInfo handshake now does unasked.

## Grok: what the affordances actually are

Grok needs the most from us because it has the least: no session-to-session
messaging at all.

- **Discovery** — we publish a Claude-shaped session file and socket, so the
  peer appears to *Claude*. Nothing makes it appear to other Grok sessions,
  because Grok has no such view.
- **Transport** — entirely ours (the UDS listener).
- **Lifecycle** — the MCP server's own startup, not hooks. agent-bus installs
  no grok hook; the `hook` subcommand still exists for a harness that has
  hooks and no MCP, which grok no longer is. This row used to read "hooks,
  which is why `session-start` matters", contradicting the matrix above it
  ("hooks exist, unused"). A grok session that never touches an MCP tool has
  no listener, and that is now the whole of it.
- **Wake** — Grok supplies the mechanism (`monitor`), we supply the thing to
  watch. That is the one axis where Grok meets us halfway.

## Waking a headless Claude peer

Measured findings -- the idle/act tension, the tick cadence,
`crossSessionInbound`, and why `-p` ends a turn no matter what the prompt says
-- are in [harnesses/claude-code.md](harnesses/claude-code.md), with the rest
of what to know when Claude Code is the peer.

## The tree mirrors this matrix

`src/agent_bus/adapters/` is split by capability, not by vendor, because the
matrix above is sparse: three of the five can be discovered, two can host a
session, two have an inbound transport of their own. `ls adapters/transport/` answers "who can
I reach natively" — a question this document exists to answer.

```
adapters/contracts.py       Discovery | HarnessLifecycle | Transport | AddressSpace
adapters/discovery/         claude  omp
adapters/lifecycle/         claude  grok
adapters/transport/         claude  codex   (+ filebus, the default)
adapters/addressing/        bus  session  pid  thread
```

Each directory carries its own registry, so membership per capability is a fact
of the tree rather than a hand-kept tuple. Sending is routed by kind in
`commands/messages.send`; a kind with no native transport reads the file bus.

**Addressing** is the axis that is not per-vendor. A space is a namespace of
identifiers sharing a liveness rule, and the sparseness is in what each harness
contributes: Codex brings a `thread` space and no `session` space, Claude a
`session` space with the same liveness rule as any other process-backed
address. It exists because "is this
agent still there" used to be answered everywhere by one hardcoded rule —
`is_pid_alive` — which is right for a Claude session and wrong for a Codex
thread, and there was nowhere in the tree to say so.

| space | liveness | mailbox |
|---|---|---|
| `bus` — the uuid `register()` mints | the registering process | yes |
| `session` — `claude:<sid>`, `grok:<sid>`, `omp:<id>` | the harness's process | yes |
| `pid` — `codex:pid:<n>`, `omp:tty:<n>` | that process | yes |
| `thread` — `codex:thread:<uuid>` | **existence only** | **no** |

The *tool* surface is now MCP: `register`, `list_agents`, `send_message`,
`get_inbox`, `read_message`, `ack_message`, `set_status`, `self`. That is what
a peer agent calls. Those operations live in `commands/`, and both `cli.py`
and `mcp_server.py` are argument-shaping over them — the CLI exposes the same
set plus the operational commands (`listen`, `watch`) that have no MCP
equivalent. There are no vendor-named send commands: `send` routes by kind.

But lifecycle is not a command, and is reached by two entry points:

- **`serve()`** calls `session_start()` on startup and `session_end()` on exit.
  This is the path almost everything takes.
- **the `hook` subcommand**, for a harness that has hooks and no MCP.

Both are still needed. The hook path is the only way to get lifecycle in a
harness whose MCP server is not running — the failure seen when a Grok session
that never called an MCP tool had no listener, and an outbound send could not
find its own socket.

`lifecycle.py` is vendor-neutral: it asks each adapter *am I present, what is
my host pid, what is this session called*, and takes an explicit
`SessionDescriptor` rather than sniffing the environment itself.
