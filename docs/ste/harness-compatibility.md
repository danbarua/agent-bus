# Harness compatibility matrix

This matrix maps what each harness provides. It maps what agent-bus must supply as a result.

Sources: `docs/comparison-note.md` and the source reviews in `docs/harnesses/`.

## Harness shapes

Each harness fits one of five shapes.

- Claude is a special case. Run `agent-bus listen`. It works in both directions. It needs no install on the Claude side.
- Codex is a special case in the other direction. agent-bus writes into Codex natively. Codex discovers agent-bus and writes out through agent-bus's MCP server.
- Grok uses the MCP server and `watch`.
- omp uses the MCP server and `watch`. This is the same shape as Grok.
- pi has no MCP and no hooks. It uses the CLI `listen` and `watch`, run from its shell. Any unknown harness falls back to this shape.

Grok and omp share one shape. agent-bus supplies the transport for both. `watch` is how the agent notices new mail. No axis in this matrix distinguishes Grok from omp.

## The axes

| axis | the question it answers |
|---|---|
| **Discovery** | two questions, not one — see below |
| **Lifecycle** | where can agent-bus attach to session start/end to register an identity? |
| **Transport** | how does a message reach this harness, and is that transport **its own or ours**? |
| **Wake** | once a message has arrived, what makes the agent look at it? |

Discovery runs in two directions. Reading a harness's registry and appearing in it are independent capabilities.

agent-bus reads the Grok registry and the omp registry. Neither Grok nor omp can see agent-bus that way. agent-bus appears to Claude by writing a session file that no other harness reads. Claude discovery is symmetric. This symmetry makes Claude the zero-install case.

Identity and presence depend on discovery. They are fields in whatever the surface is.

## CI use and interactive use

Every row in the matrix below answers one question: what is possible for that harness. Two workloads build on that answer. They want opposite behavior.

| | wants |
|---|---|
| **automated CI** | a run nobody watches, that ends, and that leaves something pytest can read. A blocking call is ideal — it is a deterministic point to assert on. |
| **real use** | an agent that receives information and carries on working, while talking to a person and to its peers. A blocking call is the opposite of that. |

The **Woken headless?** row in the matrix measures the CI case. `park` there means agent-bus can hold the harness at a known point during a hands-off run. It does not describe how the harness behaves when a person uses it. Read the wrong way, `park` looks like an agent that sits blocked and declines work.

A test prompt and a person-facing prompt serve different purposes. Each is correct for its purpose.

Each file in `tests/agent_bus/integration/test_*.py` carries a real captured sequence diagram. Each diagram states which parts show CI use and which parts show real use.

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

### Measuring wake behavior

Testing used the same method for each harness. A tool that turns command output into events was pointed at `agent-bus watch`. Each harness then received a message.

Claude and Grok woke and acted. Codex, omp, and pi all answered `NO_MONITOR`.

omp has no tool named `monitor`. It has `hub`, which supervises project-scoped processes and returns their output. The first test excluded `hub`, because the test brief ruled out simulating a monitor tool.

`hub logs` with `follow` parks with one call. It needs no bookkeeping and returns the output lines. `hub wait` with a `pattern` also parks, but it re-matches an accumulating buffer and spins on the second wake. See [harnesses/omp.md](harnesses/omp.md) for the detail.

Blocking omp with `hub logs` or `hub wait` is a CI technique. It gives a hands-off run a known point to assert on. The same call, given to a person-facing agent, produces an agent that sits blocked and declines work.

### Push and park

Push and park are the two ways a harness wakes. The terms `woken` and `not-woken` do not capture the difference.

- **push**: the turn ends. An event starts a new turn. An idle peer costs no resources until mail arrives.
- **park**: the turn stays open, blocked in a tool call. This works as well for a conversation. The agent stays occupied while it waits.

Codex and pi have neither push nor park, as measured. Both have a shell. Both could in principle block on a read of `watch`, but this is untested and not claimed here.

A harness with no push and no park can still receive a message. It can still read its inbox. It cannot be told when mail arrives, so something must make it check. The Wake cell for omp in the matrix names what consumes `watch`.

`tests/agent_bus/integration/test_two_agents_hold_a_conversation.py` tests two peers, one Claude and one Grok, holding a seven-message conversation. Each peer wakes only from the message before it.

### omp's eval tool

omp has a second way to reach agent-bus. Its `eval` tool is a live Python (IPython) kernel. agent-bus loads as an import there, not a subprocess: `sys.path`, `from agent_bus import store`. The roster is available in process. No other harness in this matrix can do that.

### Message durability

Every message that reaches a peer through agent-bus sits in the inbox. It survives a restart, regardless of harness. Whether a harness also stores the message itself is a separate question: Claude does not store it. This does not change what agent-bus provides.

## Zero-install conditions

Claude Code is zero-install because two conditions hold together.

1. Claude discovers peers from a session file agent-bus writes.
2. Claude delivers to peers over a socket agent-bus serves: it dials the socket.

Both conditions must hold for a harness to need no install. The matrix above shows which harness meets which condition.

## Codex integration shape

Codex integration is transparent in one direction and not in the other. The transparent direction, sending into Codex, is the more useful one.

### Sending into Codex

agent-bus sends into Codex transparently, with no install on the Codex side. `thread/queue/add` is an RPC on the app-server's Unix socket. Local auth is filesystem permissions only: a `0700` directory and a `0600` socket, with no peer-credential check. Any process running as the same user can connect, call `initialize`, and queue a message to a thread by id.

The wake is native. A queued item auto-wakes an idle thread. Codex injects the message as a plain user turn, so delivery and wake happen together.

Codex is the easiest of the three harnesses to message this way. It is the only one where durability comes free.

### Busy Codex threads

A busy thread's queued message waits for the current turn to finish (#292).

`turn/steer` can interject into an in-progress turn directly. Only the process already holding that turn can call it. This was verified live against a real app-server, codex-cli 0.149.0.

`send_to_codex` spawns its own app-server for each call. It closes that server the moment the call returns. Sending the spawned server a `turn/start` or `turn/steer` starts a real turn, then kills it. No error appears. The message leaves no trace anywhere.

A thread another process holds open picks up a queued write as soon as its current turn ends. It starts the next turn automatically. This needs no immediate-wake path.

See `docs/transport-seam.md` for the probes behind these claims.

### Faking a Codex thread

agent-bus cannot make a non-Codex agent appear as a Codex thread. Codex discovers threads from `state_5.sqlite`, a shared SQLite database with a migration chain (`0001_threads.sql` through `0041_threads_name.sql`). Another product owns and schema-versions this database. Writing rows into it to fake a peer means writing into that product's private, migrating store. This differs from writing a self-contained session file. It breaks on the next migration of that database.

Codex integration is asymmetric.

- Outbound to Codex is direct: no install, no plugin.
- Inbound from Codex works through the MCP server. A Codex session that runs the agent-bus MCP server is on the bus: `serve()` calls `session_start()`. The harness-join test shows Codex registering and delivering a message. agent-bus has no Codex hook.

This inverts the Grok situation. Grok has no transport of its own, so agent-bus supplies the whole transport for it. Codex has a transport agent-bus can use, but a discovery surface agent-bus cannot join.

### Codex MCP peer knowledge (2026-08-24)

Codex tells its MCP child no session details. This was verified with a recording MCP server. The child's entire environment was:

    HOME LANG LOGNAME PATH SHELL TERM TMPDIR USER __CF_USER_TEXT_ENCODING

This matches the allowlist in `rmcp-client/src/utils.rs:162-175`. The environment carries no thread id, no session id, and no socket path.

A Codex bus peer cannot link to its own Codex thread as a result. A registered Codex-kind peer with no `native.threadId` still has an inbox. A roster entry is a `bus` address, and `has_mailbox` is unconditionally true there. That peer has no route to its own thread.

Routing always selects the Codex transport for a Codex-kind peer. The Codex transport refuses to send without a thread id. It reports "not a thread" and does not fall back to the file bus.

`thread/queue/add` still addresses threads directly by id or name. This is a separate path that the bus does not touch.

agent-bus does not guess the thread link from cwd and recency. Two sessions in one repo would collide, and a misrouted message is worse than an unrouted one.

If Codex ever exposes the thread id to its MCP children, one explicit alias completes the link.

Codex identifies itself in the `initialize` handshake:

| harness | `clientInfo.name` | version seen |
|---|---|---|
| Codex | `codex-mcp-client` | 0.149.0 |
| omp | `omp-coding-agent` | 1.0.0 |
| Grok | `grok-shell-<our server name>` | 1.0.5 |

This handshake is how an MCP-only peer gets its `kind`. A peer starts as `pending-<pid>` before any client connects and identifies itself. The handshake then sets the kind, or sets it to `other` if the client cannot be placed.

Grok also passes `GROK_SESSION_ID` to its MCP children. agent-bus reads this value only after Grok's clientInfo has matched. See the note in `adapters/lifecycle/grok.py::detect`.

### No Codex discovery adapter

`adapters/discovery/codex.py` used to read `~/.codex/process_manager/chat_processes.json`. That file has held `[]` since 31 July on this machine.

Codex records no pid anywhere in its thread metadata. A process-shaped discovery adapter cannot work for Codex as a result, so agent-bus has none. See the docstring in `adapters/discovery/__init__.py` for the detail.

This is why the matrix answers "no, by choice" for discovering Codex. A Codex session joins the bus by registering through the MCP server. The `clientInfo` handshake does this automatically.

## Grok integration shape

Grok has no session-to-session messaging of its own. As a result, agent-bus supplies more for Grok than for any other harness in this matrix.

- Discovery: agent-bus publishes a Claude-shaped session file and socket. The Grok peer appears to Claude sessions. Grok has no view of other Grok sessions, so the peer does not appear there.
- Transport: agent-bus supplies the whole transport, the UDS listener.
- Lifecycle: the MCP server's own startup handles lifecycle. agent-bus installs no Grok hook. The `hook` subcommand serves a harness with hooks and no MCP. Grok has MCP, so it does not use this subcommand. A Grok session that never calls an MCP tool has no listener.
- Wake: Grok supplies the mechanism, `monitor`. agent-bus supplies the thing to watch. This is the one axis where Grok meets agent-bus halfway.

## Waking a headless Claude peer

Measured findings about waking a headless Claude peer live in [harnesses/claude-code.md](harnesses/claude-code.md). These include the idle/act tension, the tick cadence, `crossSessionInbound`, and why `-p` ends a turn regardless of the prompt text. That file has the rest of what to know when Claude Code is the peer.

## Adapter tree structure

`src/agent_bus/adapters/` is split by capability, not by vendor. The matrix above is sparse.

Three of the five harnesses can be discovered. Two can host a session. Two have an inbound transport of their own. `ls adapters/transport/` answers which harnesses agent-bus can reach natively.

```
adapters/contracts.py       Discovery | HarnessLifecycle | Transport | AddressSpace
adapters/discovery/         claude  omp
adapters/lifecycle/         claude  grok
adapters/transport/         claude  codex   (+ filebus, the default)
adapters/addressing/        bus  session  pid  thread
```

Each directory carries its own registry, so the tree itself records membership per capability. `commands/messages.send` routes sending by kind. A kind with no native transport reads the file bus.

### Addressing spaces

Addressing does not split by vendor the way discovery, lifecycle, and transport do. A space is a namespace of identifiers that share one liveness rule. Each harness contributes different spaces to this system. Codex has a `thread` space and no `session` space. Claude has a `session` space, using the same liveness rule as any other process-backed address.

Different spaces exist because one liveness rule does not fit every space. `is_pid_alive` is right for a Claude session. It is wrong for a Codex thread, which needs its own liveness rule.

| space | liveness | mailbox |
|---|---|---|
| `bus` — the uuid `register()` mints | the registering process | yes |
| `session` — `claude:<sid>`, `grok:<sid>`, `omp:<id>` | the harness's process | yes |
| `pid` — `codex:pid:<n>`, `omp:tty:<n>` | that process | yes |
| `thread` — `codex:thread:<uuid>` | **existence only** | **no** |

### Tool surface

The tool surface is MCP: `register`, `list_agents`, `send_message`, `get_inbox`, `read_message`, `ack_message`, `set_status`, `self`. A peer agent calls these tools. These operations live in `commands/`. Both `cli.py` and `mcp_server.py` shape arguments over them.

The CLI exposes the same set, plus the operational commands `listen` and `watch`, which have no MCP equivalent. There are no vendor-named send commands. `send` routes by kind.

### Lifecycle entry points

Lifecycle has two entry points.

- `serve()` calls `session_start()` on startup and `session_end()` on exit. Most agent-bus code takes this path.
- The `hook` subcommand serves a harness that has hooks and no MCP.

Both entry points are needed. The hook path is the only way to get lifecycle in a harness whose MCP server is not running. Without it, a Grok session that never calls an MCP tool has no listener. An outbound send then cannot find its own socket.

`lifecycle.py` is vendor-neutral. It asks each adapter: am I present, what is my host pid, what is this session called. It takes an explicit `SessionDescriptor`. It does not read the environment on its own.
