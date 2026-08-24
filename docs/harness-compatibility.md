# Harness compatibility matrix

Written to reason about the compatibility layer *before* refactoring. It is a
map of what each harness already provides and what agent-bus must therefore
supply — not a design for the abstraction. The abstraction should fall out of
this, rather than the other way round.

Sources: `docs/comparison-note.md` and the three source reviews it cites.

## The axes

The refactor survey identified three adapter axes where the code has one
(discovery). This note uses four, splitting **wake** out of transport, because
the two come apart in practice: Grok has a wake mechanism (`monitor`) and no
transport, Codex has both but they are separate subsystems, and Claude fuses
them.

| axis | the question it answers |
|---|---|
| **Discovery** | how does this harness learn that other agents exist, and can we write into that? |
| **Lifecycle** | where can agent-bus attach to session start/end to register an identity? |
| **Transport** | how does a message physically reach this harness, and can we speak it? |
| **Wake** | once a message arrives, how does the agent come to notice it? |

Identity and presence ride on discovery — they are fields in whatever the
discovery surface is.

## The matrix

| | Claude Code | Grok Build | Codex | omp | unknown |
|---|---|---|---|---|---|
| **Discovery surface** | `~/.claude/sessions/<pid>.json`, one file per session | none locally | `state_5.sqlite` (`threads` table) + rollout JSONL | none found | none |
| Can we write into it? | **yes** — write a file | n/a | **no** — a shared, migration-versioned DB owned by another product | n/a | n/a |
| **Lifecycle attach** | none needed | MCP server start (hooks exist, unused) | MCP server start | MCP server start | MCP server start |
| **Transport in** | its own UDS peer protocol; it dials us | **none exists** | `thread/queue/add` RPC on the app-server socket | file inbox only | — |
| Can we speak it? | **yes** — implemented | n/a, we supply it | **yes** — local auth is filesystem permissions only | yes | — |
| **Transport out** | native `SendMessage` | `agent-bus send` (ours) | `codex queue` / RPC | ours | — |
| **Wake** | harness delivers into the conversation | `monitor` tool, needs something to watch | native — a queued item auto-wakes an idle thread | none; polls its own inbox | none |
| **Message durability** | none | n/a | SQLite, survives restarts | our file inbox | — |
| **agent-bus must supply** | **nothing** | discovery, transport, wake source | discovery only | discovery, transport, wake | fallback identity |

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
  server is on the bus: `serve()` calls `session_start()`, and the tier-2 smoke
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
to its own Codex thread.** Someone sending to its bus name reaches its file
inbox; `thread/queue/add` still addresses threads directly by id or name. We do
not guess the link from cwd and recency — two sessions in one repo would
collide, and misrouting a message is worse than not routing it. If Codex ever
exposes the thread id to its MCP children, one explicit alias completes the link.

What Codex *does* say is who it is, in the `initialize` handshake:

| harness | `clientInfo.name` | version seen |
|---|---|---|
| Codex | `codex-mcp-client` | 0.149.0 |
| omp | `omp-coding-agent` | 1.0.0 |
| Grok | `grok-shell-<our server name>` | 1.0.5 |

That is now how an MCP-only peer gets its kind — before this it registered as
`other-<pid>` and stayed there unless the agent thought to call `register`
itself. Grok additionally passes `GROK_SESSION_ID` to MCP children, which we
read **only** after its clientInfo matched; see the note in
`adapters/lifecycle/grok.py::detect`.

**Unrelated but adjacent:** `adapters/discovery/codex.py` reads
`~/.codex/process_manager/chat_processes.json`, which has been `[]` since
31 July on this machine. Codex records no pid anywhere in its thread metadata,
so that adapter structurally cannot work. It is a candidate for deletion in a
change of its own.

## Grok: what the affordances actually are

Grok needs the most from us because it has the least: no session-to-session
messaging at all.

- **Discovery** — we publish a Claude-shaped session file and socket, so the
  peer appears to *Claude*. Nothing makes it appear to other Grok sessions,
  because Grok has no such view.
- **Transport** — entirely ours (the UDS listener).
- **Lifecycle** — hooks, which is why `session-start` matters and why a Grok
  session that never touches an MCP tool has no listener.
- **Wake** — Grok supplies the mechanism (`monitor`), we supply the thing to
  watch. That is the one axis where Grok meets us halfway.

## What this says about the refactor

The survey's three axes hold up, with one amendment: **wake is a fourth axis**,
and it is the one where the harnesses differ most. Claude needs nothing, Grok
needs a command to watch, Codex needs nothing, omp needs polling. An adapter
interface with only discovery/lifecycle/transport has nowhere to put that.

Two observations on sequencing, agreeing with the survey's own caveat:

1. **Transport extraction (step 3) should wait.** The survey says it risks
   designing around a sample of one. This matrix sharpens that: the second
   transport, when it arrives, is most likely *Codex outbound* — an RPC client
   over someone else's socket, not a listener we own. That is a very different
   shape from Claude's, and an interface extracted from Claude's alone would
   almost certainly not fit it. Build the Codex client first, then extract.
2. **Opening the `Kind` enum (step 1) is still first**, and this matrix is the
   argument for it: five harnesses are already in play, and the closed
   `Literal["claude","grok","omp","codex","other"]` in `protocol.py:12` — restated
   in four argparse choices and two MCP schemas — cannot express a sixth.

## Where the API surface actually lives now

A related question, since `plugin_host.py` was named for an architecture we have
partly moved away from.

## Waking a headless Claude peer: measured, resolved

A `claude -p` worker is a real peer -- it binds the same inbox socket as an
interactive session and appears in `list`, for exactly the life of its process
(bare mode binds nothing). Driving one unattended took two findings, and the
second overturned the first conclusion.

**Finding one: idle to receive, a turn to act.** Measured one variable at a
time:

| peer | result |
|---|---|
| idle, no tick | `SEND_EXIT=0` -- delivered, never answered |
| ticking every 12s | `SEND_EXIT=1` -- "refused the message" |

These conflict: a frame arriving mid-turn is refused, and a delivered message
sits in a session with nothing running to surface it. An interactive session
gets turns for free because its user keeps typing; a headless one has no such
user, and ticking hard enough to guarantee a turn destroys the idleness that let
the message land. The resolution is a *slow* tick -- 30s, so the peer is idle
almost always and still gets a turn shortly after anything arrives -- plus
`crossSessionInbound: accept`, without which delivery depends on the sender's
permission class rather than on the test.

**Finding two: the remaining failures were not wake failures.** With the slow
tick in place, tier 4 still failed intermittently, and that was recorded here as
the wake problem being unresolved. It was not. Capturing the driver's stdout
showed the failing run had *completed the whole round trip* -- its output
contained `REPLY=ack from headless claude`, which is unreachable without
listener-up, registration, delivery, wake, a native SendMessage and a successful
poll. The test failed because the driver wrote "The inbox contains a message."
where the assertion grepped for `SEND_EXIT=0`.

The bug was in the grader. Asking a language model to relay shell output
verbatim is asking it to do the one thing it will not do reliably, and a test
built that way measures the driver's prose discipline rather than the product.
Tiers 3 and 4 now have each shell step write its own marker to a file and assert
against those files; the model only has to *run* the command. Whether it runs
the step at all is still model-dependent, but a missing marker file now names
the skipped step instead of looking like a product failure.

With both fixed, three consecutive rounds of tiers 3 and 4 passed unattended
(~64s per round). A mutation confirms the assertions bite: briefing the peer to
reply with different words fails the test on the reply text.

One caveat on the record above. Two of the four original runs had no driver
stdout captured, so they are *consistent with* the transcription explanation
rather than shown to be it; both were omp-driven, and omp was replaced for
unrelated reasons. The 12s refusal is a genuine product observation and is why
the tick stays slow.

Two traps found while measuring, both worth keeping:

- **Never hand the peer an unread pipe.** With `--verbose --output-format
  stream-json` a 40s run emits ~39KB against a ~64KB buffer. A peer blocked
  mid-write looks exactly like a peer that never woke.
- **Grok is the opposite axis.** Grok receives nothing and needs a channel
  (`docs/waking-peers.md`); headless Claude receives fine and needs a reason to
  look.

**Also measured, and separate:** the omp driver is itself unreliable at this
duration -- three of the four runs above failed on omp's side (tools missing,
send step skipped) rather than the peer's. A test that spans two agents and a
model call in each is measuring both.

## The tree mirrors this matrix

`src/agent_bus/adapters/` is split by capability, not by vendor, because the
matrix above is sparse: all four harnesses can be discovered, two can host a
session, two have a native transport. `ls adapters/transport/` answers "who can
I reach natively" — a question this document exists to answer.

```
adapters/contracts.py       Discovery | HarnessLifecycle | Transport | AddressSpace
adapters/discovery/         claude  grok  omp  codex
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
`session` space that is deliberately mailbox-less. It exists because "is this
agent still there" used to be answered everywhere by one hardcoded rule —
`is_pid_alive` — which is right for a Claude session and wrong for a Codex
thread, and there was nowhere in the tree to say so.

| space | liveness | mailbox |
|---|---|---|
| `bus` — the uuid `register()` mints | the registering process | yes |
| `session` — `claude:<sid>`, `grok:<sid>`, `omp:<id>` | the harness's process | yes, **except `claude`** |
| `pid` — `codex:pid:<n>`, `omp:tty:<n>` | that process | yes |
| `thread` — `codex:thread:<uuid>` | **existence only** | **no** |

The *tool* surface is now MCP: `register`, `list_agents`, `send_message`,
`get_inbox`, `ack_message`, `set_status`, `self`. That is what a peer agent
calls. Those seven operations live in `commands/`, and both `cli.py` and
`mcp_server.py` are argument-shaping over them — the CLI exposes the same seven
plus the operational commands (`listen`, `watch`) that have no MCP
equivalent. There are no vendor-named send commands: `send` routes by kind.

But lifecycle is not a command, and is reached by two entry points:

- `mcp_server.py:273,289` — `serve()` calls `session_start()` on startup and
  `session_end()` on exit. **This is the path.**
- `cli.py` — the `hook` subcommand, for a harness that has hooks and no MCP.
  The bash shims that used to invoke it are gone; they only existed to run the
  CLI from Grok's Bash tool, which is what the MCP server removed.

So `plugin_host.py` was misnamed rather than vestigial: it was *session
lifecycle*, shared by the hook path and the MCP path. Both are still needed. The
hook path is the only way to get lifecycle in a harness whose MCP server is not
running — which is exactly the failure seen when a Grok session that never
called an MCP tool had no listener, and an outbound send could not find its own
socket.

**Since done.** That module no longer exists. It is now `lifecycle.py` --
vendor-neutral, asking each adapter *am I present, what is my host pid, what is
this session called*, and taking an explicit `SessionDescriptor` -- plus
`listener.py`, holding the Claude-shaped listener and the session file it
publishes, which are transport concerns that had been sitting in the same
module.
