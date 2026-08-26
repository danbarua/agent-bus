# pi

What to know when pi is the harness that is misbehaving.

**It has no MCP and no hooks.** A shell, and nothing else. It joins the bus by
running the CLI, which makes it the floor case: a harness with no integration
points at all can still be a peer. It can be given MCP if configured; we do not,
because it does not need it.

**Its default provider is google, so an unpinned pi dies on a key nobody
supplied.** With no `--model` it reaches for Gemini and fails on an unset key —
a failure that has nothing to do with the bus and reads like one that does.
`--model` takes `provider/id` with an optional `:<thinking>` suffix
(`anthropic/claude-haiku-4-5`, `sonnet:high`); `pi --list-models` prints what
the current auth can actually reach, and `pi auth check --model <id>` says
whether one resolves before a run depends on it.

**`--pid $PPID` is not optional.** Inside pi's own shell tool that is *pi's*
pid. Without it the entry belongs to the CLI process, which exits immediately
and is pruned before anything can address it. The same flag is what makes a
listener outlive the command that started it.

**It is `other`, permanently.** Not a defect and not a gap: `other` means the
peer works and no discovery adapter can name its type. Nothing should try to
"fix" it into a kind of its own.

**It drives the Claude-messaging tests deliberately.** Measured: a pi-driven
run completes in **15s** against omp's minutes, and three of four omp
round-trip runs failed on omp's own side — MCP tools missing from its list, the
send step silently skipped. Every one of those failure modes is MCP-shaped, and
pi has none.

The harness with the least machinery finds the gaps, because nothing else is
papering over them. `run_listen` publishing a working socket without
registering under its host pid was found this way: `send` could not locate it,
and every other harness got its listener from the other code path.

**It will not relay shell output verbatim.** A run that completed an entire
round trip failed its assertion because pi wrote "The inbox contains a message."
where the test grepped for `SEND_EXIT=0`. Have the shell write a marker file
and read that; the model's only job is to run the command.

**It cannot be woken by mail.** A shell is all it has, so nothing turns
`agent-bus watch` output into anything pi notices; probed, and it reports as
much itself. A pi peer polls `inbox` when something asks it to look, which is
the floor case here too — the least machinery, and the least that can be done
for it.
