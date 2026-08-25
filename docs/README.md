# docs

Split by **subject**, not by topic: what this project does, versus what other
people's software does.

## Ours — this directory

| file | |
|---|---|
| `UDS-protocol.md` | how we speak Claude Code's peer protocol, in both directions |
| `identity-and-peering.md` | what identity means here — a peer is not necessarily a live process |
| `transport-seam.md` | what the second transport taught, and what was deliberately not extracted |
| `waking-peers.md` | getting a peer to notice a message it has already received |
| `hooks-in-foreign-harnesses.md` | why the shipped hooks were deleted rather than fixed |
| `harness-compatibility.md` | the axes (discovery, lifecycle, transport, addressing) and which harness has what |
| `comparison-note.md` | four-way comparison, agent-bus included |

The last two sit here rather than in `harnesses/` because they are our synthesis
rather than research: the axes in `harness-compatibility.md` are what
`src/agent_bus/adapters/{discovery,lifecycle,transport,addressing}/` is built
from.

## Theirs — `harnesses/`

Source-level research on Claude Code, Codex and Grok Build, plus the review
prompts that produced it. See `harnesses/README.md`; read it before
re-investigating any harness.

## Why not `docs/agent-bus/`

Nesting this project's own docs under its own name inside its own repository
says agent-bus twice in every path. One subdirectory for other people's systems
is enough to make the root mean "ours" by contrast.
