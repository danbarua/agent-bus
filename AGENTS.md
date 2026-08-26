<!-- ┌──────────────────────────────────────────────────────────────────────┐ -->
<!-- │ DO NOT MODIFY UNLESS EXPLICITLY REQUESTED                            │ -->
<!-- │ This section is pinned. Cruft below it may be pruned; this may not.  │ -->
<!-- └──────────────────────────────────────────────────────────────────────┘ -->

# DX Principles

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

**Module-level DocStrings:** 
\<=20 - fairly routine. 
\<=30 - complexity in here. 
\>=30 - probably belongs in a doc.

## When these conflict with a task

They do not override an instruction. They override your instinct to add
something while carrying one out. If a rule here genuinely blocks the work,
say so in a sentence and ask — do not quietly build the thing.

<!-- END PINNED SECTION -->
