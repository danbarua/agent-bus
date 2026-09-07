# What the second transport taught

The Codex outbound client was built to find the transport seam against a real
second implementation rather than guessing it from Claude's alone. This records
what it changed.

**Status: the seam has since been cut.** `adapters/transport/` holds the two
implementations behind `adapters/contracts.py::Transport`, and
`commands/messages.send` routes by kind. The findings below are what that
interface was shaped by, and the four capability flags are *not* yet part of
it — see "On sequencing".

## The two transports are opposite shapes

| | Claude | Codex |
|---|---|---|
| We run | a **listener** others dial | a **client** that dials them |
| Direction of connection | inbound | outbound |
| Peer is | a live socket | a durable thread id |
| Must the peer be running? | **yes** | **no** |
| Delivery means | handed to a live session | **persisted**, may be delivered later |
| Result carries | an ack correlated to `msg_id` | a `QueuedSubmission` id |
| Failure taxonomy | `stale_socket`, `socket_busy`, timeout | archived thread, queue at capacity, not initialized |
| Can we publish presence into it? | yes — write a session file | **no** — its registry is a migrating SQLite DB |

An abstraction extracted from Claude's transport alone would have assumed a
listener, a live peer, and delivery-means-delivered. Codex breaks all three.

## What is actually common

Three operations, and they are narrower than "a socket":

```
resolve(target)          -> peer reference      # name or id -> something addressable
deliver(peer, text)      -> receipt             # accepted, not necessarily delivered
enumerate()              -> [peer]              # who can I address
```

Everything else differs, and the differences are not incidental — they are what
a caller has to reason about. So each transport also has to declare what it can
promise:

```
durable          # does a message survive the peer being absent?
requires_live    # must the peer be running to accept?
publishes_presence  # can we make a non-native agent appear in this harness?
wakes_on_deliver # does delivery itself get the agent's attention?
```

For the two we have: Claude is `durable=False, requires_live=True,
publishes_presence=True, wakes_on_deliver=True`. Codex is `durable=True,
requires_live=False, publishes_presence=False, wakes_on_deliver=True`.

Those four flags carry most of the compatibility matrix. They are also what a
router would need to pick a transport, and what an agent needs to know before it
can interpret a receipt.

## `deliver` must not conflate accepted with delivered

The sharpest single lesson. Claude's ack means a live session received the
frame. Codex's `QueuedSubmission` means a row was written to SQLite — the target
may be busy, cold, or not running at all, and actual dispatch is observable only
as an async notification to a subscriber.

If both return "ok", a caller cannot tell whether the message has landed. The
receipt needs to say which it is. This is the same distinction the Claude
protocol already draws between `held` and `delivered`, so the vocabulary exists.

## Peer identity is not uniformly "a live process"

`store.py` currently prunes an entry when its pid dies, taking the inbox with
it. That is correct for a Claude peer, where identity *is* a live socket. It is
wrong for a Codex thread, which is addressable precisely when nothing is
running.

This is the same defect already recorded in `comparison-note.md` — mailboxes
dying with the process — reached from a second direction. A transport that
supports absent peers makes it structural rather than a rough edge.

## Addressing: we are stricter than Codex, deliberately

Codex resolves a duplicate thread name by taking the most recently updated match
and reports no ambiguity — the type is literally `SessionNameMatch::First`. Our
`resolve_thread()` refuses instead, listing the candidate ids. Silently
delivering to whichever session was touched last is misrouting that is very hard
to notice afterwards, and this bus already has one identity-collision bug in its
history.

## On sequencing

`docs/harness-compatibility.md` argued that extracting transport (survey step 3)
should wait for a second implementation. Having built one, the interface was
extracted — but deliberately only the part two implementations could justify:

- **Extracted:** `send(entry, text, ...)` and `resolve(target)`. Both vary
  between Claude and Codex in ways a caller must not paper over, and both had
  concrete callers on day one.
- **Not extracted:** the four capability flags (`durable`, `requires_live`,
  `publishes_presence`, `wakes_on_deliver`) and `enumerate()`. Nothing routes on
  the flags yet — routing is by kind — and `enumerate()` has exactly one
  plausible implementation, since Codex threads cannot be listed cheaply and
  Claude peers are already covered by discovery.

Encoding the flags now would mean designing a router around two data points for
a decision nothing currently makes. A third transport (an omp one, or a Grok one
if Grok ever gains messaging) is what tests whether they are the right four.

## Verification status

The client was checked against codex-cli 0.149.0 on a live app-server:

- `initialize` → real `InitializeResponse`
- `thread/list` → 25 real threads
- `thread/queue/add` → reached the server and was refused with a genuine
  server-side error for a nonexistent thread id

The last one was run against a deliberately nonexistent thread so nothing was
injected into a real session then. **That deliberate decision was since made
(#292), twice, and the second run reversed the first.**

The first live run pushed straight through: `thread/start` → `turn/start` →
`thread/resume` (confirms `{"type": "active", "activeFlags": []}`) →
`turn/steer` (with `expectedTurnId`, required, not optional) → `thread/resume`
(confirms `{"type": "idle"}`) → `turn/start` again, all inside one
`CodexAppServer` held open for the whole sequence. It worked, and it was
tempting to read that as "`send_to_codex` can wake a busy thread immediately
now" -- which is what got implemented and comment-posted to #292 as working.

**It wasn't testing what shipping would do.** `send_to_codex` opens its own
`CodexAppServer`, calls `wake`, and closes that server the instant the call
returns -- a different shape from the first run's one-server-held-open
sequence. A second probe built that exact shape: `wake()` returned a real
turn id (`mode: "started"`), the thread went `idle` within seconds, and 25s
later `thread/resume` showed only the *original* turn's reply -- the PONG
turn never produced an item anywhere. Thread state is per-app-server and
in-memory; closing the server that started a turn kills it, no error, and
`send_to_codex`'s own `except CodexError: pass` fallback never fires because
`wake()` didn't raise -- the message is silently dropped, worse than doing
nothing. `send_to_codex` was reverted to queue-only before merge, and `wake`
and `turn/steer` were deleted from `CodexAppServer` with it: with no caller
left, keeping them would be surface area nothing exercises. `resume_thread`
and `start_turn` stayed -- see the fourth probe below for their real use.

A third probe filled in the piece #292's comment had assumed rather than
run: does `thread/queue/add` from a short-lived process actually auto-wake a
thread a *separate*, long-lived process is holding idle? Yes, live: a
holder process ran one turn, sat idle; a second, short-lived process queued a
message and closed; the holder's `thread/resume` came back with a second,
completed turn and the reply in it, no `wake()` involved on either side. That
is the real second half of `wakes_on_deliver` for the queue path.

A fourth probe checked the shape an alternating conversation actually needs:
does the same auto-wake happen when the holder's thread is *busy*, not idle,
at the moment the external write lands? Yes -- a holder ran a 20s turn; a
second process queued a message five seconds in; the queued text landed as
the very next turn's input the instant the first turn completed, no gap, no
manual nudge. That is what lets an e2e Codex peer hold one `CodexAppServer`
open across a whole exchange, deliver its own opening turn with
`resume_thread`/`start_turn`, and then rely on the queue alone for every
message after -- the counterpart's ordinary `agent-bus send` wakes it whether
the thread happens to be idle or mid-turn when the message arrives.

Not yet re-probed on a codex-cli newer than 0.149.0 -- see
`docs/harnesses/codex-messaging-reference.md` and issue #292.
