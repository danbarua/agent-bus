# Transport Seam

The transport interface was shaped by two real implementations: Claude's and Codex's.

`adapters/transport/` holds the two implementations behind `adapters/contracts.py::Transport`. `commands/messages.send` routes by kind.

The four capability flags are not yet part of this interface. See "On sequencing" below.

## Claude and Codex transport shapes

| | Claude | Codex |
|---|---|---|
| We run | a **listener** others dial | a **client** that dials them |
| Direction of connection | inbound | outbound |
| Peer is | a live socket | a durable thread id |
| Must the peer be running? | **yes** | **no** |
| Delivery means | handed to a live session | **persisted**, may be delivered later |
| Result carries | an ack correlated to `msg_id` | a `QueuedSubmission` id |
| Failure taxonomy | `stale_socket`, `socket_busy`, timeout | archived thread, queue at capacity, not initialized |
| Can we publish presence into it? | yes: write a session file | **no**: its registry is a migrating SQLite DB |

## Operations common to both transports

Three operations are common to both transports. This common ground is narrower than one shared socket type.

```
resolve(target)          -> peer reference      # name or id -> something addressable
deliver(peer, text)      -> receipt             # accepted, not necessarily delivered
enumerate()              -> [peer]              # who can I address
```

The transports differ beyond these three operations. Each caller must reason about those differences. Each transport also declares what it can promise:

```
durable          # does a message survive the peer being absent?
requires_live    # must the peer be running to accept?
publishes_presence  # can we make a non-native agent appear in this harness?
wakes_on_deliver # does delivery itself get the agent's attention?
```

Claude is `durable=False, requires_live=True, publishes_presence=True, wakes_on_deliver=True`. Codex is `durable=True, requires_live=False, publishes_presence=False, wakes_on_deliver=True`.

Those four flags carry most of the compatibility matrix. A router would need them to pick a transport. An agent needs them to interpret a receipt.

## Accepted versus delivered

Claude's ack means a live session received the frame. Codex's `QueuedSubmission` means a row is written to SQLite. The target may be busy, cold, or not running. Actual dispatch is observable only as an async notification to a subscriber.

If both return "ok", a caller cannot tell whether the message has landed. The receipt must say which it is. The Claude protocol already distinguishes `held` from `delivered`. The same vocabulary can describe accepted versus delivered for a transport receipt.

## Peer identity per transport

`store.py` prunes an entry when its pid dies, and removes the inbox with it. This is correct for a Claude peer, where identity is a live socket. It is incorrect for a Codex thread, which stays addressable when no process is running. A transport that supports absent peers must handle this identity mismatch directly. This defect is recorded in `comparison-note.md`.

## Duplicate thread names

Codex resolves a duplicate thread name by taking the most recently updated match. It reports no ambiguity, using a type named `SessionNameMatch::First`. Our `resolve_thread()` refuses a duplicate name instead. It lists the candidate ids. Silent delivery to whichever session was touched last risks a misroute. Such a misroute is hard to notice afterward.

## On sequencing

`docs/harness-compatibility.md` states that extracting transport should wait for a second implementation. The Codex client provided that second implementation. The extracted interface covers only the part two implementations could justify.

- **Extracted:** `send(entry, text, ...)` and `resolve(target)`. Both differ between Claude and Codex in ways a caller must handle directly. Both had real callers from the start.
- **Not extracted:** the four capability flags (`durable`, `requires_live`, `publishes_presence`, `wakes_on_deliver`) and `enumerate()`. No caller routes on the flags yet. Routing currently happens by kind. `enumerate()` has one workable implementation. Codex threads cannot be listed cheaply, and Claude peers are already covered by discovery.

Encoding the flags now would mean designing a router around two data points. No decision currently depends on them. A third transport would test whether these four flags are right. Candidates include omp or a future Grok messaging path.

## Verification status

The client was checked against codex-cli 0.149.0 on a live app-server. Three checks passed. `initialize` returned a real `InitializeResponse`. `thread/list` returned 25 real threads. `thread/queue/add` reached the server and was refused with a genuine server-side error for a nonexistent thread id.

`send_to_codex` queues a message. It does not open a new `CodexAppServer` to wake the target thread. `CodexAppServer.wake` and `turn/steer` do not exist. Codex thread state is per-app-server and held in memory. Opening a new server to wake a thread, then closing it right after, can drop that turn silently. `resume_thread` and `start_turn` remain on `CodexAppServer`, used to deliver a peer's own opening turn.

A short-lived process can queue a message with `thread/queue/add` for a thread a separate, long-lived process is holding. That message dispatches without an explicit wake call. Auto-wake happens both when the holding thread is idle and when it is busy. A queued message becomes the next turn's input as soon as the current turn ends, with no delay.

An e2e Codex peer holds one `CodexAppServer` open across one exchange. It delivers its own opening turn with `resume_thread` and `start_turn`. It relies on the queue for every message after that. The counterpart's `agent-bus send` wakes the thread whether it is idle or mid-turn.

Not yet re-probed on a codex-cli newer than 0.149.0. See `docs/harnesses/codex-messaging-reference.md` and issue #292.
