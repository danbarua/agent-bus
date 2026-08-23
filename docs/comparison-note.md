# Claude Code vs Grok Build vs agent-bus

What each system already does, and what that means for agent-bus. Sources:

- Claude Code — `skills/agent-bus/references/claude-code-presence.md`
  (2.1.239 binary strings plus live `~/.claude/sessions/*.json`).
- Grok Build — `docs/grok-build-ipc-reference.md` (source review of
  `danbarua/grok-build` @ `07b2f71`, with `file:line` citations).
- agent-bus — this repo, current behaviour, per
  `skills/agent-bus/references/identity-and-peering.md`.

## The headline

**Grok Build — the local shell — has no session-to-session messaging at all.**
The review looked for it specifically and returned a firm negative: no RPC
method, no envelope variant, no routing function (§3). Its leader socket is a
*client↔leader* multiplexer — many client processes attached to one leader that
hosts sessions in-process — not a peer-to-peer channel. The two things that
look adjacent are same-session fan-out to multiple attached clients, and cloud
relay to x.ai.

Scope that claim carefully. It is about the local binary in
`crates/codegen/xai-grok-*`. **Grok Bots — the cloud product — does have
bot-to-bot messaging**, and is a separate system; see below.

So agent-bus is not duplicating a facility of the local Grok shell. It supplies
one that shell genuinely lacks, borrowing Claude Code's wire protocol to do it.
Worth stating plainly, because the opposite assumption would have argued for
deleting the project.

## Grok Bots (cloud) — a different product, a different shape

<https://docs.x.ai/grok-bot/overview>

Grok Bots are "persistent, named teammates" that "message each other, share
context in threads or group chats, and pass ownership so you are not the router
between tools". Coordination is not peer-to-peer IPC: "Multiple Bots share one
user-scoped computer and can run in parallel", sharing files, browser sessions
and app logins for handoffs.

So the shared substrate is a hosted machine plus a messaging/threading layer,
not sockets on the user's laptop.

**Unknown, and not inferable from that page.** The overview is product-level.
It gives no API names, no endpoints, no message envelope, no delivery semantics
(acks, persistence, queueing), no naming or addressing scheme, and no presence
or status reporting. A wire-level comparison with Claude Code or agent-bus is
not possible from it; that needs an API reference we do not have.

**Why it does not change agent-bus's position.** Three axes separate them:

| | Grok Bots | agent-bus |
|---|---|---|
| Where | hosted, user-scoped cloud computer | the user's own machine |
| Who | Grok Bots with each other | Claude Code ↔ grok ↔ omp ↔ codex, cross-vendor |
| Substrate | shared filesystem/browser/logins plus threads | local UDS, Claude Code's peer protocol |

agent-bus's niche is *local and cross-vendor*: making a non-Claude process on
this machine appear in Claude Code's native `ListAgents` and be messageable
with its native `SendMessage`. Grok Bots does not address that, and could not
without speaking Claude's local protocol.

Worth revisiting if x.ai publishes a Bots API reference — particularly whether
addressing, delivery semantics and presence are close enough to be worth
mirroring in vocabulary, the way the `RosterActivity` mapping below does.

## Side by side

| | Claude Code | Grok Build | agent-bus today |
|---|---|---|---|
| Socket role | peer ↔ peer between sessions | client ↔ leader (one leader hosts many sessions) | publishes a Claude-shaped socket per peer |
| Transport | AF_UNIX, `/tmp/cc-socks/<pid>.sock` | AF_UNIX `~/.grok/leader.sock`; Named Pipe on Windows | AF_UNIX, Claude's path convention |
| Framing | newline-delimited JSON | 4-byte big-endian length prefix + JSON, 64 MB cap | newline-delimited JSON |
| Auth | `peerToken` from a `0600` key file, auth frame must be the first line | **none** — filesystem permissions only | implements Claude's token scheme |
| Session discovery | one `sessions/<pid>.json` per session | no local session list; in-memory roster in the leader, plus a remote REST replica registry | writes Claude-shaped session files |
| Liveness | pid running **and** `procStart` matches → `live`/`unknown`/`none` | residency + turn-state, in-process (`.is_finished()`); flock only for the leader itself | pid only |
| Session→session messaging | yes | **none** | file bus + UDS shim |
| Message persistence | none; at-most-once to a live peer | n/a | durable file inboxes |
| Identity | mutable name; `formerNames` grace | immutable UUIDv7 id; mutable title | roster name + published socket name |
| Rename | old name kept in `formerNames` with `until` | title overwritten, **no trace, no grace** | no `formerNames` |
| Presence vocabulary | `status` string (`idle`, `busy`, …) | `RosterActivity`: `Working`, `Idle`, `NeedsInput`, `Dormant`, `Completed`, `Dead` | writes `idle` once, never updates |

## What agent-bus should take from each

### From Grok: a live status feed it is not yet consuming

Grok already computes exactly the thing agent-bus fakes. `RosterActivity` is
maintained per session and published two ways (§5): request/response over
`x.ai/sessions/list`, and a broadcast `x.ai/sessions/changed` emitted on every
state transition — spawn, turn start, turn end, teardown.

agent-bus writes `status: "idle"` once at startup and never touches it again,
so a grok peer always reads idle in Claude's listing no matter what it is
doing. Subscribing to `x.ai/sessions/changed` and mapping it onto the session
file's `status` field would make a grok peer's state genuinely live, using a
feed that already exists.

A mapping is needed because the vocabularies differ:

| `RosterActivity` | suggested `status` |
|---|---|
| `Working` | `busy` |
| `Idle` | `idle` |
| `NeedsInput` | `busy` (Claude has no distinct "blocked"; the listing shows `busy`) |
| `Dormant`, `Completed`, `Dead` | stop publishing — the peer is not addressable |

Note the shapes disagree on a deeper point. Grok's own doc comment says
liveness is "residency + turn-state, not a pid" (§5), because a Grok session is
an in-process actor with no pid of its own. Claude's model is pid-plus-start-time.
agent-bus bridges these by giving each peer a listener process with a real pid —
which is why the shim exists at all, and why one peer must mean one socket.

### From Claude: the parts agent-bus half-implements

1. **`procStart` verification.** agent-bus writes it into the session file but
   checks only `is_pid_alive()`, so a recycled pid reads as live where Claude
   would say `none`. Claude's check is tri-state; agent-bus's is a boolean.
2. **`formerNames` on rename.** The `register` tool renames peers, and Grok
   offers no precedent here — its rename is a bare overwrite with no history
   (§4), and the review notes senders have nothing to fall back on. Claude's
   `{name, until}` grace is the better model and costs a field.
3. **Status refresh**, per above.

### What not to copy from Grok

**Its leader socket has no authentication** (§1): no peer credentials, no
token, no explicit socket permissions — only the process umask on
`~/.grok/leader.sock`. Anyone who can reach the path can register as a client.
Claude Code's `peerToken` scheme is strictly better and agent-bus already
implements it; there is no case for relaxing to match Grok.

## Security note worth surfacing

The review flags an ambient-environment leak (§6) that matters to anything
plugging into Grok:

- `GROK_LEADER_SOCKET` is set process-wide via `std::env::set_var` when
  `--leader-socket` is passed, and is inherited by every child — including
  hook scripts and MCP stdio servers.
- `XAI_API_KEY` and the other `FIRST_PARTY_CREDENTIAL_ENV_VARS` are likewise
  ambient. The codebase has a `scrub_first_party_credentials` helper and
  applies it to exactly one child type — the auth-provider helper. Neither the
  hook spawn path nor the MCP spawn path calls it.

Combined with the unauthenticated leader socket, a third-party MCP server or
hook script can read `GROK_LEADER_SOCKET` and dial the leader's control
surface. That is Grok's issue rather than agent-bus's, but agent-bus ships an
MCP server into exactly that position, so it is worth knowing what its own
child environment contains.

### One correction to our earlier reasoning

Earlier in this project we tightened `detect_kind()` to key on
`GROK_HOOK_EVENT`/`GROK_PLUGIN_ROOT` instead of `GROK_SESSION_ID`, on the
grounds that `GROK_SESSION_ID` was "ambient and inheritable" and could let a
Claude session adopt a Grok identity.

The review shows the first half of that is wrong in a specific way:
`GROK_SESSION_ID` is **never** set process-wide — every site uses
`Command::env`, never `std::env::set_var` (§6), so it is not ambient in the
Grok process.

The conclusion still holds, for a different reason. It *is* set on the
Bash/PTY tool's environment (`terminal/pty_session.rs:256-262`), so a shell
spawned by Grok carries it, and anything launched from that shell — a Claude
Code session included — inherits it transitively. Scoped-at-spawn is not the
same as un-inheritable by grandchildren. The fix was right; the stated reason
needed sharpening.
