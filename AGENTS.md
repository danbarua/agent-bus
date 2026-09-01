<!-- ┌──────────────────────────────────────────────────────────────────────┐ -->
<!-- │ DO NOT MODIFY UNLESS EXPLICITLY REQUESTED                            │ -->
<!-- │ Instructions, not notes. Nothing here restates what the code does.   │ -->
<!-- └──────────────────────────────────────────────────────────────────────┘ -->

# Read this first

**[docs/README.md](docs/README.md) says which page answers which question, and
in what order.** The first one on that list is not optional.

Then the issue you are working, and **#58**, which is the current plan.

# The property everything here protects

**One command, two surfaces.** `agent-bus inbox` does the same thing whether an
agent calls the MCP tool or the CLI. That is the whole integration: an agent
with `agent-bus` and `inbox` in its context already knows what to do — no
adapter, no `--json`, no script, no instruction manual.

The two spell it differently on purpose: **the CLI is the short form, the MCP
tool is `verb_noun`** — `inbox` / `get_inbox`, `read` / `read_message`, `list`
/ `list_agents`. Each is idiomatic for its surface, and the association
survives the prefix: an agent carrying `read_message` finds it when told
`read`. `self` and `register` are "me"-shaped, so no `verb_noun` pair makes
sense and they are the same word on both.

Some verbs are **CLI-only, deliberately**. `watch` and `listen` are processes
rather than calls — they block and stay up. `join` and `leave` are CLI
semantics: they wrap register-and-listen for a shell that owns a pid. `help`
is a terminal affordance an agent does not need, `mcp` starts the server
itself, and `reap`, `orphans`, `unregister`, `grok-status` and `hook` are
administration over the bus rather than one agent's operations.

`tests/agent_bus/test_surface_naming.py` is the guard, and it checks
completeness rather than spelling: a new tool with no verb, or a new verb
classified as neither, fails until someone writes down which it is.

**So build what was asked and stop.** A gap in a request is deliberate: it is
the space an agent crosses on its own. Fill one with machinery and the property
above is gone.

# Before you add anything

Six rules. Each has a tell — the thing you will be doing just before you break
it. The reasoning behind them is in `docs/design_philosophy.md` and in the
commits that made each decision.

**Intuitive.** Name a thing after what it is, so a stranger finds it without
being told.
*Tell:* you are about to explain, in a document, where something lives or what
a name means.

**Simple.** Use the mechanism that already exists. Look for it before you build
one.
*Tell:* you are adding a concept — a field, a directory, a kind, a numbering
scheme — to solve a problem this codebase has already solved once.

**Follow conventions where they exist.** Standard library, standard layout,
standard flags, standard directories. A convention you did not invent is one
nobody has to learn.
*Tell:* you are choosing a filename, an environment variable, a directory or an
output format, and reaching for your own.

**No ceremony.** Adding a thing must not require registering it somewhere. If
it does, the registry is the bug.
*Tell:* your change is not finished until you have also updated a list.

**No prose maintained in parallel with code.** Say how to *run* it; let the code
say what it does. History goes in commit messages, where `git log` finds it.
*Tell:* you are writing a table that mirrors a directory, a paragraph that will
be wrong when someone edits the function below it, or a sentence in the present
tense about how the code is now.

**One mechanism, or none.** Two ways to do a thing answer nothing, because the
surface with traffic and the surface with instrumentation are never the same
one.
*Tell:* you are adding a second place to write to, read from, or configure.

Module docstrings: `<=20` lines routine, `21-29` real complexity, `>=30` it is a
document — move it to `docs/` and leave a pointer.

# When these conflict with a task

Say so in a sentence and ask. Do not quietly build the thing.

# How work is recorded

**GitHub issues are the plan.** A plan file in a home directory is invisible to
the next session, to `@claude`, and to anyone reading the repository. Reference
issues from PR bodies (`Closes #NN`) so the board moves without anyone moving it.

*Tell:* you are about to record a decision somewhere only your own session will
look.

**An open question is an issue, not a paragraph.** When something needs a
decision the owner has to take, open one labelled `open question` and assign it.
Say what turns on it: a question with the options and their costs can be
answered in a minute; one that says only "we should decide X" costs a
conversation to reconstruct first.

*Tell:* you are writing "worth deciding", "to be settled", or "for whoever picks
this up".

# Verify by running it

On this machine you can run all of it: the suite, the e2e container, the
harnesses, the bus itself. So a claim about behaviour is something you ran, and
saying which command you ran is part of making it.

Reading source tells you the names. It does not tell you the wire.

The exception is `@claude` in GitHub Actions, which has the repository and
nothing else — no container, no harnesses, no bus. Its findings are hypotheses
until someone runs them here, so review and documentation go there and
verification stays local.
