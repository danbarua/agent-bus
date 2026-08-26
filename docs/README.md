# docs

Split by **subject**: what this project does, versus what other people's
software does.

- **Here** — our protocol, identity model, design decisions and the reasoning
  behind them.
- **`harnesses/`** — research on Claude Code, Codex, Grok Build, omp and pi,
  plus the review prompts that produced it.

No index. `ls` does that, and unlike a table here it cannot go stale.

`harness-compatibility.md` and `comparison-note.md` sit on this side despite
being about other harnesses: they are our synthesis rather than research, and
the axes in the first are what
`src/agent_bus/adapters/{discovery,lifecycle,transport,addressing}/` is built
from.

## Why not `docs/agent-bus/`

Nesting this project's own docs under its own name inside its own repository
says agent-bus twice in every path. One subdirectory for other people's systems
is enough to make the root mean "ours" by contrast.
