# Design Philosophy

## Minimal product surface

This product ships with very little code and few features. This small size is
complete work. The product implements the minimum needed for other products
to talk to each other.

## Evidence and inference

When a coding agent follows an instruction, evidence or inference decides its
next step. Inference is the default when evidence runs out. This repository
provides little evidence early. That scarcity is intentional.

## Lines of code

Measured 2026-08-29:

    tests                10,819 lines — drives five live coding agents
    the product (src)     7,351 lines →  4,252 executable
      of which adapters   1,374 lines →    751 executable

Tests outnumber product code. 43% of the product consists of reasoning
captured in docstrings and comments. This reasoning stops the next agent
from reinventing it.

751 executable lines make Claude, Grok, omp, Codex, and pi talk to each other.
The rest of the product is a file store, a CLI, an MCP server, and someone
else's wire protocol implemented faithfully. `uds.py` is 705 lines that
implement Claude's protocol. `store.py` is 962 lines that manage JSON files
in a directory.

The hardest-looking part of this system is the smallest piece of code. The
design publishes what each harness already reads. This system builds no
separate protocol for that talk.

## CI use versus interactive use

Testability in CI and usefulness to a person are different questions. Their
answers can be opposite. CI wants a run that finishes unattended and leaves a
result to check. A person wants an agent that keeps working while things
arrive.

Decide which job you are building for before you copy a pattern from the
other. Most time lost on this project came from answering one job's question
and handing it to the other. See the note above the matrix in
[harness-compatibility.md](harness-compatibility.md).

## Reading a gap in the code

The end-to-end tests are the product demo. They wire five real agents
together so they can talk to each other. An agent that reads 4,252 executable
lines under 10,819 lines of test may think code is missing. Each such gap is
a feature the end-to-end tests already cover.

Two further examples show the same mistaken inference.

**Leaking the wrong thing.** An instruction addressed the CLI's internals. It
wrote a store row into a user's working git tree. The instruction should
have addressed the surface an agent actually uses. See #135.

**Instructing the wrong reader.** `read` existed on the CLI but not on MCP,
for four days. The fix worked for a shell-driven agent. It failed silently
for a tool-driven agent. No line said which surface the instruction was for.
See #152.
