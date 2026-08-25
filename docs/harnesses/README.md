# docs/harnesses

Research on **other people's software**: how each harness works, established by
reading its source and probing the running binary. These describe behaviour that
is true whether or not agent-bus exists.

| file | subject |
|---|---|
| `claude-code-presence.md` | how Claude Code does presence, identity and messaging |
| `codex-messaging-reference.md` | the Codex app-server API |
| `grok-build-ipc-reference.md` | Grok Build's leader socket and IPC |
| `grok-build-monitor-reference.md` | Grok Build's monitor/watch mechanism |
| `prompts/` | the review prompts that produced these, kept with them |

Three carry a `<!-- Provenance: external read-only source review of ... -->`
header naming the checkout they were read from. That header is the test of
whether a document belongs here.

## What does NOT belong here

Our own design notes, decisions and protocol implementation stay in `docs/`.
The line is subject, not topic:

- `docs/harnesses/claude-code-presence.md` — how Claude Code works
- `docs/UDS-protocol.md` — how **we** speak to it

Both are about the same wire; only the second is a description of this project.
`harness-compatibility.md` and `comparison-note.md` also stay in `docs/`: they
are our synthesis across the harnesses, and the axes in the first are what
`src/agent_bus/adapters/{discovery,lifecycle,transport,addressing}/` is built
from.

## Before re-investigating a harness

Read the file here first. These took real effort and cite `file:line` against
checkouts in `~/Code/agents/`. Source tells you the names; it does not tell you
the wire — Grok's source calls the roster method `x.ai/sessions/list` while the
wire wants `_x.ai/sessions/list`, and the documented name answers `-32601`. Read
these to know where to look, then probe the running thing to know what it sends.
