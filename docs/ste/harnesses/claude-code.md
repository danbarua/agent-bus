# claude-code

The `claude-code-presence.md` file documents Claude Code's presence,
identity, and messaging internals.

## No client-side installation

Claude Code requires no plugin, no MCP server, no hook, and no polling. It
discovers peers because `agent-bus listen` writes the session file that
Claude Code already reads. Claude Code replies using its own native
`SendMessage`. Do not build a mechanism that requires Claude Code to poll,
read an inbox, or look up a socket.

## Symmetric session file access

Claude Code is the only symmetric harness. agent-bus can read Claude
Code's session file (`~/.claude/sessions/<pid>.json`). agent-bus can also
appear inside that same file. Every other harness supports only one of
these two directions. This is why Claude Code is the zero-install case.
Other harnesses need agent-bus to supply a transport.

## Discovery without a join step

A Claude Code session publishes a session file for its own reasons. It has
no separate joining step. Tests do not exercise Claude Code as the subject
of a join test. Tests use Claude Code only as the peer being messaged.

## Idle-to-receive, turn-to-act balance

A headless peer must be idle to receive a message. A headless peer needs a
turn to act on a message. These two requirements pull against each other.
An idle peer with no tick takes delivery but does not answer. A peer
ticking every 12 seconds refuses the frame outright, mid-turn. The tick
interval must be slow enough that the peer spends most of its time idle.

## `--model` selection

`--model` accepts an alias or a full model id. An unpinned peer runs a
different model on each machine. `haiku`, `sonnet`, `opus`, and `fable`
resolve to the latest model in that family. `claude-haiku-4-5-20251001`
pins an exact model.

Without `--model`, a headless peer inherits whoever started it. That means
the developer's own configured default locally, or the account default
under an API key. The same test then costs a different amount, depending
on where it ran. It also runs different model weights.

## `-p` turn termination

`-p` ends the turn when the model stops emitting output. A prompt cannot
prevent this behavior. A worker told to "count slowly to 300, do not stop
early" exited anyway. Its transcript ended "Timer running; will continue
on each tick." The worker believed it was still running. Hold stdin open
with `--input-format stream-json` to avoid this.

## `crossSessionInbound` and mode parity

An unset `crossSessionInbound` means mode parity. Under mode parity, a
sender that asserts no permission class is held for approval whenever the
receiving session bypasses prompts. The agent-bus CLI asserts no
permission class. Delivery then depends on who is asking. A headless peer
has no user available to approve it.

## Self-wake versus ticking

A headless peer can wake itself instead of being ticked externally.
Claude Code's own `Monitor` tool runs a command and delivers each output
line as an event. Each event starts a turn in a session whose previous
turn had already ended.

Arm a monitor on `agent-bus watch --target <me>`. After a turn ends, wait
sixty seconds without writing to stdin. A message sent from another
process still starts the next turn.

Self-wake removes the need for an external ticker. The peer is idle by
default and gets a turn when mail arrives.

## Requirements for self-wake

Self-wake needs two things. The `Monitor` tool may be deferred. The brief
given to the peer must say to load it, using `ToolSearch` with query
`select:Monitor`. A peer that cannot find the tool fails the same way as a
broken self-wake mechanism.

`Monitor started` confirms only that the tool accepted the command. A
watch that died on its next line leaves the same `Monitor started` string
behind. Check for a running `watch` process to confirm the watch is still
active.
