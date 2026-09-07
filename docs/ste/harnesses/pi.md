# pi

## No MCP or hooks

Pi has no MCP. Pi has no hooks. Pi is a shell only. Pi joins the bus by
running the CLI. This makes pi the floor case. A harness with no
integration points can still be a peer.

Pi can run with MCP if configured. Agent-bus does not configure MCP for pi.
Pi does not need MCP to join the bus.

## Default provider and unpinned pi

Pi's default provider is google. An unpinned pi with no `--model` flag
selects Gemini. It fails when no key is set for Gemini. This failure is
unrelated to the bus. It looks like a bus failure.

`--model` takes `provider/id`, with an optional `:<thinking>` suffix.
Examples: `anthropic/claude-haiku-4-5` and `sonnet:high`. `pi
--list-models` prints the models the current authentication can reach. `pi
auth check --model <id>` checks whether a model resolves before a run
depends on it.

## The --pid $PPID flag

`--pid $PPID` is required. Inside pi's shell tool, `$PPID` is pi's own
process id. Without it, the roster entry belongs to the CLI process. The
CLI process exits immediately. The bus prunes the entry before any peer can
address it. The same flag lets a listener outlive the command that started
it.

## Pi's kind

Pi peers have kind `other`. `other` means the peer works and no discovery
adapter identifies its harness. Do not change `other` to a new kind for pi.

## Role in the Claude-messaging tests

Pi drives the Claude-messaging tests. A pi-driven run completes in 15
seconds. An omp-driven run takes minutes. Three of four omp round-trip runs
failed on omp's own side. MCP tools were missing from omp's list. The send
step was silently skipped.

Every one of those failure modes is MCP-shaped. Pi has no MCP component. Pi
has the least machinery of any harness. This lets it reveal gaps that other
harnesses paper over.

`run_listen` can start a listener without registering it under its host pid
on the roster. `send` then cannot locate that listener. Every other harness
starts its listener through a different code path.

## Shell output relay

Pi does not relay shell output verbatim. In one round trip, the run
completed, but the test's assertion failed. Pi wrote "The inbox contains a
message." The test grepped for `SEND_EXIT=0`.

Have the shell write a marker file. Read that marker file to check the
result. The model's only job is to run the command.

## Mail delivery to pi

No mechanism pushes mail into pi. Pi has only a shell. Pi has no monitor
tool. Pi has no supervised-process tool.

`agent-bus watch` produces a notice for each arriving message. This notice
does not reach pi automatically. Pi reports the same limitation when asked
directly.

## Blocking on watch output

Parking a shell call until `watch` delivers a notice is untested for pi.
omp does this with `hub wait`. Pi has no equivalent of `hub`'s timeout,
pattern, and interruptibility for this. A pi peer must check its inbox
itself. It does not receive a push notification of new mail.
