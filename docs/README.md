# docs

**Read [design_philosophy.md](design_philosophy.md) before you touch any code.**
It is fifty lines and it is the one that stops you building something.

Then these, in order. Each assumes the ones above it:

1. [design_philosophy.md](design_philosophy.md) — what this ships, and why
   absence of code is the product rather than an unfinished one.
2. [harness-compatibility.md](harness-compatibility.md) — what each harness can
   do, and **CI-shaped and use-shaped are different questions**. Most of the
   time lost here has been an answer to one handed over as an answer to the
   other.
3. [identity-and-peering.md](identity-and-peering.md) — how a peer gets an
   identity, and how two addresses are known to be one agent.
4. [UDS-protocol.md](UDS-protocol.md) — someone else's wire format, implemented
   faithfully. None of it is ours to change.
5. [structured-logging.md](structured-logging.md) — a field contract shared with
   two other projects, so it cannot be settled from inside this one.

Everything else here is reference. Read it when the thing it covers is in front
of you, and check its date first.

## Why a list, when `ls` is right there

`ls` gives the names. It cannot say which to read first, or that the first one
is not optional — and that is the entire content above. Nothing is summarised:
the files do that, and a summary here would be a second copy to keep true.

Adding a doc does not mean adding a line here. Only a new **mandatory** one
does, and that is a decision taken on purpose rather than a step in filing.

## Split by subject

What this project does, versus what other people's software does.

- **Here** — our protocol, identity model, design decisions and the reasoning
  behind them.
- **`harnesses/`** — research on Claude Code, Codex, Grok Build, omp and pi,
  plus the review prompts that produced it.

`harness-compatibility.md` and `comparison-note.md` sit on this side despite
being about other harnesses: they are our synthesis rather than research, and
the axes in the first are what
`src/agent_bus/adapters/{discovery,lifecycle,transport,addressing}/` is built
from.

## Why not `docs/agent-bus/`

Nesting this project's own docs under its own name inside its own repository
says agent-bus twice in every path. One subdirectory for other people's systems
is enough to make the root mean "ours" by contrast.
