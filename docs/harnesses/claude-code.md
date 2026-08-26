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

**`-p` ends the turn when the model stops emitting**, and no prompt fixes that.
A worker told to "count slowly to 300, do not stop early" exited anyway, its
transcript ending "Timer running; will continue on each tick." It believed it
was still running. Hold stdin open with `--input-format stream-json` instead.

**`crossSessionInbound` unset means mode parity**, under which a sender
asserting no permission class — our CLI is one — is held for approval whenever
the receiving session bypasses prompts. Delivery then depends on who is asking,
and a headless peer has nobody to approve it.
