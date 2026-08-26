# claude-code

What to know when Claude Code is the peer. Its presence, identity and messaging
internals are in `claude-code-presence.md`.

**Nothing is installed on its side. Ever.** No plugin, no MCP server, no hook,
no polling. It discovers peers because `agent-bus listen` writes the session
file it already reads, and it replies with its own native `SendMessage`. That
absence is the feature, so anything that needs Claude to poll, read an inbox or
look up a socket is solving the wrong problem.

**It is the only symmetric harness.** We can read its registry
(`~/.claude/sessions/<pid>.json`) *and* appear in it. Every other harness is
one or the other, which is why Claude is the zero-install case and the others
need us to supply a transport.

**It is discovered by existing.** A Claude session has no joining step to test —
it publishes a session file for its own reasons. That is why it never appears
as the *subject* of a join test, only as the thing being messaged.

**A headless peer must be idle to receive and needs a turn to act.** These pull
against each other. Measured, one variable at a time: an idle peer with no tick
takes delivery and never answers; a peer ticking every 12s refuses the frame
outright, mid-turn. The tick has to be slow enough that the peer spends most of
its time idle.

**`--model` takes an alias or a full id**, and an unpinned peer is a different
model on every machine. `haiku`, `sonnet`, `opus`, `fable` resolve to the
latest of that family; `claude-haiku-4-5-20251001` pins an exact one. Left off,
a headless peer inherits whoever started it — the developer's own configured
default locally, the account default under an API key — so the same test costs
a different amount and runs different weights depending on where it ran.

**`-p` ends the turn when the model stops emitting**, and no prompt fixes that.
A worker told to "count slowly to 300, do not stop early" exited anyway, its
transcript ending "Timer running; will continue on each tick." It believed it
was still running. Hold stdin open with `--input-format stream-json` instead.

**`crossSessionInbound` unset means mode parity**, under which a sender
asserting no permission class — our CLI is one — is held for approval whenever
the receiving session bypasses prompts. Delivery then depends on who is asking,
and a headless peer has nobody to approve it.

**A headless peer can wake itself, which beats being ticked.** Its own
`Monitor` tool runs a command and delivers each output line as an event, and
the event starts a turn in a session whose previous turn had already ended.
Measured: arm a monitor on `agent-bus watch --name <me>`, watch the turn end,
wait sixty seconds with nothing written to stdin, and a message sent from
another process starts the next turn. That removes the external ticker and the
idle-versus-turn tension above along with it — the peer is idle by default and
gets a turn exactly when mail arrives.

Two things it needs. `Monitor` may be deferred, so the brief has to say to load
it (`ToolSearch`, query `select:Monitor`); a peer that cannot find the tool
looks identical to a mechanism that does not work. And `Monitor started` means
the tool *accepted* the command — a watch that died on the next line leaves the
same string behind, so check for a running `watch` process instead.
