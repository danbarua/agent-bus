# docs

**Read [design_philosophy.md](design_philosophy.md) before you touch any
code.** It is fifty lines. It is the file that stops you building something.

Then read these, in order. Each file assumes the ones above it.

1. [design_philosophy.md](design_philosophy.md): what this project ships,
   and why an absence of code is the finished product.
2. [harness-compatibility.md](harness-compatibility.md): what each harness
   can do. CI-shaped and use-shaped are different questions. Most time lost
   here came from answering one and handing it to the other.
3. [identity-and-peering.md](identity-and-peering.md): how a peer gets an
   identity, and how two addresses are known to be one agent.
4. [UDS-protocol.md](UDS-protocol.md): someone else's wire format,
   implemented faithfully. This project does not change any part of it.
5. [structured-logging.md](structured-logging.md): a field contract shared
   with two other projects. This project alone cannot settle that contract.

The other files here are reference. Read one when the thing it covers is in
front of you, and check its date first.

Adding a new doc does not require a new line in this list. Adding a new
mandatory doc does.

## Split by subject

This directory covers two subjects: what this project does, and what other
people's software does.

- **Here**: this project's protocol, identity model, and design decisions,
  with the reasoning behind them.
- **`harnesses/`**: research on Claude Code, Codex, Grok Build, omp, and pi,
  plus the review prompts that produced it.

`harness-compatibility.md` and `comparison-note.md` sit on this side. Both
describe other harnesses, but each is this project's own synthesis. The axes
in `harness-compatibility.md` are what
`src/agent_bus/adapters/{discovery,lifecycle,transport,addressing}/` is
built from.

## Directory naming

This project's own docs live directly under `docs/`, not under
`docs/agent-bus/`. A nested path would repeat the name agent-bus in every
path. `harnesses/` is the one subdirectory for other systems' documentation.
That one subdirectory marks the root as this project's own docs.
