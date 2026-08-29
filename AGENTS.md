<!-- ┌──────────────────────────────────────────────────────────────────────┐ -->
<!-- │ DO NOT MODIFY UNLESS EXPLICITLY REQUESTED                            │ -->
<!-- │ This section is pinned. Cruft below it may be pruned; this may not.  │ -->
<!-- └──────────────────────────────────────────────────────────────────────┘ -->

# DX Principles

**One command, two surfaces.** `agent-bus inbox` does the same thing whether an
agent calls the MCP tool or the CLI. That is the whole integration: an agent
with `agent-bus` and `inbox` in its context already knows what to do — no
adapter, no `--json`, no script, no instruction manual.

**So build what was asked and stop.** A gap in a request is deliberate: it is
the space an agent crosses on its own. Fill one with machinery and the property
above is gone.

Why there is so little here, in numbers:
[docs/design_philosophy.md](docs/design_philosophy.md).

Read this before you add anything.

`agent-bus` works. It is proven against five real coding harnesses. What stops
it becoming a package someone can install is not missing features — it is
invented complexity: concepts nobody asked for, registries that must be kept in
step by hand, and prose that describes code and then rots beside it.

Every rule below was earned by an agent in this repository doing the opposite.

## Intuitive

Name a thing after what it is, so a stranger can find it without being told.

**The tell:** you are about to explain, in a document, where something lives or
what a name means.

A 600-line `test_smoke.py` held six tests numbered `tier1`–`tier4`. The number
said when it was built, not what it covered, so every new test needed a rung, a
table row and a `-k` incantation before anyone could run it. It became four
files named for the thing under test.

## Simple

Use the mechanism that already exists. Look for it before you build one.

**The tell:** you are adding a concept — a field, a directory, a kind, a
numbering scheme — to solve a problem the codebase has already solved once.

A duplicate-listing bug was fixed by inventing an `agentBusId` field in a file
format we do not own, plus a branch in discovery to read it. `aliases` already
existed for exactly that shape, added by a commit titled *"one agent, one row —
reconcile registered and discovered identity"*. The fix became two lines using
what was there.

## Follows conventions where they exist

Standard library, standard layout, standard flags. A convention you did not
invent is one nobody has to learn.

**The tell:** you are choosing a filename, an environment variable, a directory
or an output format, and reaching for your own.

Call logging arrived as a bespoke `<home>/mcp-calls/<pid>.jsonl`, named after
the one caller it was built for. It became stdlib `logging` to stderr, where
whatever started the process already collects it, with `AGENT_BUS_LOG_LEVEL`
and `AGENT_BUS_LOG_FILE` and nothing else.

## No ceremony

Adding a thing must not require registering it somewhere. If it does, the
registry is the bug.

**The tell:** your change is not finished until you have also updated a list.

`ls` is the table, and git keeps it current for free. A test file dropped into
`tests/agent_bus/integration/` is runnable by name the moment it exists —
`./spendy_tests.sh roster` matches filenames, and when nothing matches it lists
what does with `find` rather than from something a human maintains.

## No prose maintained in parallel with code

Documentation that restates what the code does is a second copy that goes stale
on the next commit, silently. Say how to *run* it; let the code say what it
does. History goes in commit messages, where `git log` finds it.

**The tell:** you are writing a comment about code that is no longer there, a
table that mirrors a directory, or a paragraph that will be wrong when someone
edits the function below it.

A README grew to 304 lines describing behaviour, including a table of tiers
that was missing a row the day it was written. It became 70 lines of
how-to-run; the rest moved to `docs/`. Comments narrating deleted code were
removed wholesale — the reasoning was already in the commit that deleted it.

**Entropy-Safe Prose:** the preceding paragraph self-documents this principle.
"It is 70 lines" -> "It became 70 lines".

Check the tense. A sentence about how the code is now goes stale. A sentence
about what changed does not.

That makes it a grep, not a judgement call, so it catches what review misses.
It found "It is now stdlib `logging` to stderr" in this file: true, readable,
and wrong the day the destination changes.

**Module docstrings:** `<=20` routine. `21-29` real complexity in here.
`>=30` it is a document — move it to `docs/` and leave a pointer.

## Logs are one mechanism, or they are none

Four logging mechanisms once existed here: the structured logger, a per-pid
MCP call log, a per-pid frame capture, and `print()`. Every one was correct.
Together they answered nothing, because the surface with traffic and the
surface with instrumentation were never the same one.

**The tell:** you are adding a directory to write into.

- **One logger, one format, one file when a file is wanted.** `AGENT_BUS_LOG_FILE`
  names it; unset means stderr, where whoever started the process already
  collects it. A log file nobody asked for is a file nobody deletes.
- **Never rotate.** Write to stderr and let the supervisor own the file — that
  is launchd's job locally and Cloud Run's job in production. A logging library
  that owns a file has taken on the supervisor's work, which is how the other
  three directories happened.
- **Every level must have call sites.** An advertised level with nothing on it
  is worse than a missing one: you turn it on, see the same records, and
  conclude the thing you were hunting did not happen. DEBUG was advertised that
  way for months.
- **The default level has to carry failures.** It is the level everything runs
  at. A failure that only appears at INFO is a failure nobody sees.
- **Measure bodies, never copy them.** A log that copies message text is a
  second inbox with a different lifetime and no TTL. TRACE is the one exception
  and it is deliberate — it exists to take the wire apart, and nothing selects
  it by accident.
- **Redact before you log, not after.** Content must not reach a record before
  something has decided whether it is a credential. Frame size on the way in;
  content once it has been parsed and the auth frame reduced to `<redacted>`.
- **Fields are a contract, not a preference.** `docs/structured-logging.md`
  holds it, three projects share it, and `severity` is the exact key Cloud
  Logging reads. Two thirty-line formatters agreeing on names is the whole
  mechanism; no library is needed and none is wanted.

**Agree on the correlation id before agreeing on anything else.** Two logs with
a shared id are one view. Two logs without one are two logs, however they are
shipped — and this repo had a correct log on both sides of a message and could
not say where it went, because the id changed at the bridge.

## When these conflict with a task

They do not override an instruction. They override your instinct to add
something while carrying one out. If a rule here genuinely blocks the work,
say so in a sentence and ask — do not quietly build the thing.

<!-- END PINNED SECTION -->

# Working here

## The issues are the plan

GitHub issues are authoritative. A plan file in someone's home directory is not:
it is invisible to the next session, to `@claude`, and to anyone reading the
repository.

- **#57** the original design, frozen, to verify against
- **#58** the current working plan, with sub-issues sliced by **landable
  change** — the test is whether it could merge on its own
- **#59** webhooks: decided, held

Reference them from PR bodies (`Closes #NN`) so the board moves without anyone
moving it by hand.

**The tell:** you are about to record a decision somewhere only your own session
will ever look.

## An open question is an issue, not a paragraph

When something needs a decision the repository owner has to take — a product
choice, a name that fixes a schema, a trade nobody has made yet — open an issue
labelled `open question` and assign it to the owner.

Not a "TBD" in a design doc, and not a bullet at the bottom of a plan. Both get
read past. An assigned issue appears on the board with a name against it.

**The tell:** you are writing "worth deciding", "to be settled", or "for
whoever picks this up". That is an issue you have not opened.

Say what turns on it. A question with the options and their costs laid out can
be answered in a minute; one that only says "we should decide X" costs a
conversation to reconstruct before it can be answered at all.

## What `@claude` can and cannot do here

It runs in GitHub Actions with the repository and nothing else.

| | |
|---|---|
| **can** | read the repo, review a diff, sweep docs, answer from source |
| **cannot** | run the e2e container, drive the five harnesses, reach the bus |

So **review and documentation go to GitHub; verification stays local.**

This matters most for harness claims. `docs/harnesses/README.md` already says
it: *"Source tells you the names; it does not tell you the wire."* A runner can
only read the source, so a finding about how a harness behaves is a hypothesis
until someone runs `./spendy_tests.sh` or the e2e container against the real
binary. Say which one you did.
