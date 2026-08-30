# Design Philosophy

**What we are shipping:** Air.

Literally. Absence of code, absence of features is not an incomplete product.
This product implements the bare minimum needed to get other products talking
to each other.

**Why this needs writing down:** the problem with coding agents following
instructions isn't attention — it's whether the next step is determined by
evidence or by inference. Inference is the default when evidence runs out, and
this repo runs out of evidence early, **on purpose**.

## In numbers

Measured 2026-08-29:

    tests                10,819 lines — drives five live coding agents
    the product (src)     7,351 lines →  4,252 executable
      of which adapters   1,374 lines →    751 executable

More test than product, and 43% of the product is not code at all — it is the
reasoning, which is the part that stops the next agent reinventing it.

**751 executable lines** is everything that makes Claude, Grok, omp, Codex and
pi talk to each other. The rest is a file store, a CLI, an MCP server, and
someone else's wire protocol implemented faithfully: `uds.py` is 705 lines of
*Claude's* protocol, `store.py` is 962 lines of JSON files in a directory.

The part that looks like it should be hard is the smallest thing here, because
the answer was **publish what each harness already reads** rather than build a
protocol.

## Two jobs, and they want opposite things

What makes something testable in CI and what makes it useful are not the same
question, and the answers can be opposites. CI wants a run nobody watches, that
ends, that leaves something to assert on. A person wants an agent that keeps
working while things arrive.

Ask which one you are building before you copy anything from the other. Most of
the time lost here has been an answer to one, handed over as an answer to the
other — see the note above the matrix in
[harness-compatibility.md](harness-compatibility.md).

## What that means while you are working

The e2e tests are the product demo: wire five real agents together and watch
them talk. So an agent reading 4,252 executable lines under 10,819 lines of
test concludes something is missing. Nothing is — the gap that smells like a
missing feature is the gap the tests already cover.

Two more shapes the same inference takes, both real and both closed:

**Leaking the wrong thing.** An instruction shaped around a CLI's
implementation rather than the surface an agent actually has put a store row
into a user's working git tree (#135) — the agent did not misbehave; the
instruction pointed at the wrong layer.

**Instructing the wrong reader.** `read` existed on the CLI and nowhere on MCP
for four days (#152) — a fix that was correct for a shell-driven agent and
silently wrong for a tool-driven one, because nothing said which surface the
instruction was for.
