# How Claude Code does presence, identity and messaging

Reference for aligning agent-bus with the mechanism that already exists, rather
than inventing a parallel one.

Derived from Claude Code **2.1.239** (arm64) by reading the binary's strings and
by inspecting live `~/.claude/sessions/*.json`. Unofficial and version-specific.
Where something is inferred rather than observed it says so.

## 1. What `/list-agents` builds its view from

One JSON file per session, `~/.claude/sessions/<pid>.json`, written by the
session itself. Observed fields across live sessions:

| field | notes |
| --- | --- |
| `pid` | the session process; also the filename |
| `sessionId` | stable id for the conversation |
| `procStart` | process start time — the pid-reuse guard, see below |
| `startedAt`, `updatedAt` | epoch ms |
| `name`, `nameSince` | current name and when it was taken |
| `nameSource` | `derived` or `collision` when not user-set; absent when explicit |
| `formerNames` | `[{name, until}]` — see §2 |
| `status`, `statusUpdatedAt` | `idle`, `busy`, … — what the listing renders |
| `cwd` | working directory |
| `kind` | `interactive`, `bg` |
| `entrypoint` | `cli`, `claude-vscode`, … |
| `version`, `peerProtocol`, `peerFeatures` | `peerFeatures: ["notify_idle"]` |
| `messagingSocketPath` | `/tmp/cc-socks/<pid>.sock` |
| `bridgeSessionId`, `jobId`, `parkedJobId`, `spare` | bridge / background plumbing |

**Liveness.** A session is not trusted just because a file exists:

```js
if (parkedJobId !== undefined) continue;
if (!isRunning(pid)) continue;
let n = procStartFt ?? procStart;
if (n === undefined) { t = true; continue }
let o = await isSameProcess(pid, n);
if (o === true) return "live";
if (o === undefined) t = true;
return t ? "unknown" : "none";
```

The pid must be running **and** its start time must match the recorded
`procStart`. That is what stops a recycled pid from impersonating a dead
session. The result is tri-state: `live`, `unknown`, `none` — not a boolean.

Sessions reached over Remote Control or the cloud also appear, and are listed
`offline` when not currently connected.

### Can agent-bus report state the same way?

Yes, and it needs no new mechanism. `status`, `statusUpdatedAt`, `cwd` and
`updatedAt` are ordinary fields in the session file a peer already publishes, so
a peer that keeps them fresh is rendered exactly like a Claude session.

What agent-bus does today: `_write_our_session()` writes `status: "idle"` once at
startup. After that, `agent-bus set-status` calls `listener.publish_status()`,
which patches `status`, `statusUpdatedAt` and `updatedAt` into that same
published session file -- and every MCP tool call also bumps `updatedAt` via
`touch_published_session()`, so staleness is visible even between explicit
status changes. A peer that never calls `set-status` still reads as idle,
since nothing infers a status on its behalf. A session with no `status` field
at all renders with a blank status column in the listing (`agent-bus-e8` did,
because it never wrote one).

**Gap worth closing:** agent-bus's `is_pid_alive()` checks the pid only. It
writes `procStart` into the session file but never verifies it, so a recycled
pid reads as live where Claude Code would say `none`.

## 2. What happens on `/rename`

From `setRegisteredName(name, source)`:

1. Renaming to the same name (case-insensitively) keeps the original `since`.
2. The outgoing name is put in `heldNames`, keyed by its normalised form.
3. It is prepended to `formerNames` as `{name, until: now}` — but **only** if its
   source was not `derived` and it had been held for at least a minimum age.
   `formerNames` is capped at a fixed length.
4. `name`, `nameSource`, `nameSince`, `formerNames` and `updatedAt` are written
   back to the session file.

So `until` is the moment a name stopped being current, and old names remain
discoverable rather than vanishing. Observed on a live session that had been
renamed twice:

```json
"formerNames": [
  {"name": "send message to agent-bus", "until": 1787435651545},
  {"name": "6718a16f",                  "until": 1787351160385}
]
```

Senders track name→session, and warn when a name now resolves to a different
session than last time:

    Note: messaging a new session for the first time under a previously
    used name (was it restarted?)

(Observed live when messaging a peer that had restarted under the same name.)

**Alignment note:** agent-bus renames peers — the `register` MCP tool exists to
do exactly that — but writes neither `formerNames` nor `nameSince`, so a rename
is abrupt: the old name simply stops resolving, with no grace and no warning to
a sender that knew the peer by it.

## 3. Claude Code's persistent inbox

There isn't one on disk. This is the important structural point.

Delivery is **presence-based**: a message is written to a live peer's socket. If
the peer is not there, the send fails and the sender is told — nothing is stored
for later. There is no queue directory, no undelivered-message spool.

What exists instead lives inside a running session:

- **Outstanding sends and receipts.** The sender records each send and matches
  `peer_message_status` frames back to it (`orig_msg_id`), which is how a
  delivery notice is rendered.
- **A hold state.** A message can be held for the recipient's user to approve
  before Claude sees it. The status vocabulary is `held`, `denied`, `expired`,
  `delivered`, `refused`, `dropped`, and a sender that saw `held` reconciles the
  later `delivered` via a `wasHeld` flag.
- **Idle subscriptions.** `notify_when_idle` registers interest in a peer going
  idle, with a TTL and a cap on outstanding subscriptions.

So the model is at-most-once delivery to a live peer, with receipts — not
store-and-forward.

**Where agent-bus differs, deliberately.** Its file inboxes are durable: a
message written to a peer's inbox persists whether or not the peer is reading.
That is a different guarantee, not a better or worse one, and it is why the two
halves feel different. Worth keeping only where durability is actually wanted,
since it is the piece with no counterpart on the Claude side.

## 4. When a peer stops, crashes, or is unreachable

**Presence.** The liveness rule in §1 removes it: the pid is gone, or the pid is
alive but `procStart` no longer matches. Stale socket and pid files are swept.

**Sending to it.** The failure is classified and surfaced, never retried into a
queue:

| condition | class | what the user sees |
| --- | --- | --- |
| endpoint gone | `stale_socket` | "that session may have just exited" |
| endpoint busy / registry unreadable | `socket_busy` | "alive but momentarily busy. Retry the same name shortly." |
| wrong pid behind the socket | `stale_socket` | refuses to write |
| symlink / non-local / unvettable path | `invalid_target` | refused |
| no response in time | `timeout` | — |

There is also a pre-write identity check: the client reads the connected
endpoint's pid and refuses to write if it is not the expected process
(`[uds-client] connected endpoint is pid H, expected N — refusing to write`).

**Nothing is redelivered.** A crashed peer's messages are not held; the sender
learns immediately and reports it.

**Alignment note:** `prune_dead_roster()` only removes a dead entry once its
mail is gone -- an entry with unread mail waiting is kept, addressable but off
the live roster, precisely so a peer that exits does not take its queued mail
down with it (`store.py:235-265`). A dead entry with no mail left is removed.
Claude Code loses only presence — it has no mail to lose. agent-bus's durable
inboxes already outlive the process that owned them, for as long as anything
in them is unread.

## 5. Which Claude sessions can be peers

Not every Claude session binds a socket, which decides whether it can be
messaged at all.

**Verified here (2026-08-24), by watching `~/.claude/sessions/` and
`/tmp/cc-socks/` while a run was alive:**

- A headless `claude -p` worker **does** publish a session file and bind a
  socket, exactly like an interactive session. Session files went 4 → 5 and
  sockets 10 → 11 for the life of the process, and back down when it exited.
  The published entry carries a real name (auto-derived, e.g. `hcpeer-78`) and
  a `messagingSocketPath`, and `agent-bus list` sees it.
- The window is the process lifetime, and nothing more. An early probe of mine
  reported the opposite simply because the prompt finished before I looked --
  worth stating, because "no session file" and "the job already ended" are
  indistinguishable from the outside.

That makes a headless worker usable as the Claude end of an integration test,
which is what tiers 3 and 4 start for themselves -- see
`tests/support/claude_peer.py` for the two things that took measuring: a
worker only stays alive while its stdin is held open, and it must be idle to
receive but needs a turn to act.

**Reported, not verified here** (from a third-party write-up the maintainer
found; recorded because it matches the verified behaviour above and would be
expensive to rediscover, but treat as unconfirmed):

- Bare-mode sessions bind nothing.
- Hooks receive the socket path as `CLAUDE_CODE_MESSAGING_SOCKET`, so a hook
  can post back into its own session.
- Across machines a session can *reply* but never *initiate*; starting an
  exchange needs a same-machine peer, or the user steering via Remote Control.
- `isolatePeerMachines: true` forces the user's approval before any message
  leaves the machine, even in bypass mode. A checked-in project file can turn
  that requirement **on** but not **off**.

## Summary of concrete alignment work

Recorded as findings, not a plan:

1. Refresh `status` / `statusUpdatedAt` / `updatedAt` while a peer runs, instead
   of writing `idle` once at startup.
2. Verify `procStart` alongside pid liveness, matching Claude's tri-state
   `live` / `unknown` / `none` rather than a boolean.
3. Populate `formerNames` and `nameSince` when the `register` tool renames a
   peer, so a rename has the same grace period.
4. Decide whether durable inboxes are wanted. If yes, they should survive the
   peer process; if no, the socket already does the job.
