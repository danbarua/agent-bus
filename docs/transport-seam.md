# What the second transport taught

The Codex outbound client was built to find the transport seam against a real
second implementation rather than guessing it from Claude's alone. This records
what it changed. It is a finding, not a design doc — no interface has been
extracted yet.

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
should wait for a second implementation. Having built one, the recommendation
stands and is now concrete: the interface above could not have been derived from
Claude's transport, because three of its four capability flags are constant
there and only vary once Codex exists.

Whether to extract now is still a judgement call — two implementations is enough
to see the shape, but a third (an omp transport, or a Grok one if Grok ever
gains messaging) would test whether the flags are the right four.

## Verification status

The client was checked against codex-cli 0.149.0 on a live app-server:

- `initialize` → real `InitializeResponse`
- `thread/list` → 25 real threads
- `thread/queue/add` → reached the server and was refused with a genuine
  server-side error for a nonexistent thread id

The last one was run against a deliberately nonexistent thread so nothing was
injected into a real session. **A successful queue into a live Codex thread has
not been exercised** — that writes a user turn into someone's session and needs
a deliberate decision, not a test run.
