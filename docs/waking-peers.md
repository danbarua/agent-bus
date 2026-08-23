# Waking a peer

How each side learns that a message has arrived. Design note, not a record of
what is built — the Grok half does not exist yet.

## Claude gets this for free

A Claude Code session needs nothing. Its harness delivers an inbound peer
message into the conversation as a `<cross-session-message>` block and wakes the
session. No plugin, no polling, no subscription. The absence of Claude-side code
is the point, and it is why the integration test's Claude half is literally
"do nothing, then reply".

## Grok has no equivalent, but it has `monitor`

A Grok session is not woken by anything external. What it has is the `monitor`
tool (`docs/grok-build-monitor-reference.md`): it runs a shell command, treats
**each stdout line as one event**, and feeds those events back into the
conversation as a synthetic turn. `persistent: true` makes it session-length.

That is the wake mechanism. A peer starts one monitor at session start, and
inbound bus traffic arrives as events.

## What it should watch

**Not `tail -f` on the inbox file.** That works, and it is the tempting
shortcut, but it welds the on-disk layout into every peer's prompt: the JSONL
path, the id-based filename, the record shape. Change any of it and every
running monitor breaks. The sink is an implementation detail.

Instead a purpose-built follow command, so the peer's watch line is stable:

```
agent-bus watch --name <me>
```

One compact line per inbound message, nothing else on stdout.

### Constraints the monitor tool imposes

These are not preferences; they come from the reference and they shape the
command's output:

| Constraint | Value | Consequence |
|---|---|---|
| Rate limit | token bucket, capacity 10, refill 1 per 2 s | sustained output above ~0.5 lines/s is suppressed |
| Auto-kill | 30 s of continuous suppression | a chatty watch is killed outright, not throttled |
| Line truncation | 500 chars | a line must be a summary, never a message body |
| Batch truncation | 3 000 chars | bursts are clipped as a whole |
| Poll/debounce | 200 ms | sub-200 ms latency is not achievable, and not needed |
| Exit ends the watch | — | the command must not exit on an idle bus |

So the design follows:

1. **One line per message, not per byte.** A summary line, not the text.
2. **Start from now.** Replaying an existing backlog on startup is the easiest
   way to trip the rate limiter within the first second and get auto-killed.
   Historic mail is what `agent-bus inbox` is for.
3. **Line-buffered and flushed per line**, or events sit in a pipe buffer and
   arrive in clumps — the same failure the monitor tool's own guidance warns
   about with `grep --line-buffered`.
4. **Never exit while healthy.** Exit is the watch's terminal condition, so an
   empty bus must not end it.
5. **Bounded line width** well under 500 chars: truncate the summary, keep the
   sender and id intact so the peer can act on them.

### Sketch

```
[agent-bus] from=claude-bus id=5c6c39e9 summary=reply to omp-peer
```

Under 100 chars, one per message, and it carries exactly what a peer needs to
decide whether to read the full message with `get_inbox` and who to answer.

### Where it leaves the peer

The monitor event lands as a synthetic turn, so the peer sees the line, then
calls `get_inbox` (MCP) for the body and `send_message` / `send-peer` to reply.
The watch is a doorbell, not a mail slot: it says something arrived, and the
existing tools fetch it.

## Symmetry worth noticing

| | how it learns | who built it |
|---|---|---|
| Claude Code | harness delivers into the conversation | nobody — it is native |
| Grok | `monitor` on an agent-bus follow command | agent-bus supplies the command; Grok supplies the wake |
| omp | nothing yet | open question |

The Grok row is the interesting one: Grok already has the wake mechanism, and
agent-bus only has to give it something worth watching. That is the same
pattern as the UDS work — use what the harness already does rather than build a
parallel one.

For omp there is no `monitor` equivalent identified yet. Until there is, an omp
peer has to poll its own inbox, which is what the round-trip test does today.

## Status

Nothing here is implemented. `agent-bus watch` does not exist; the constraints
above are derived from the monitor reference, and the line format is a sketch,
not a settled contract.
