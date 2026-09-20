# What one feature cycle found

Written after building `join`/`leave` (#159, #160): three commits, two of
which were bugs the first commit's tests found live rather than bugs anyone
went looking for. The pattern is worth keeping because it repeated three
times in one afternoon.

`tests/agent_bus/test_conventions.py` already does this for a narrower
class -- a passing suite that still hides a real defect -- with its own
enforcing test beside each entry. This is the wider version: patterns and
anti-patterns from actually running things, not from reading source.

## Run the thing before testing around it

The `join`/`leave` CLI verbs were designed on paper first: read `join()`,
read `leave()`, write the argparse wiring, write tests. Both bugs below were
invisible at that stage -- the design read correctly, the unit-level plumbing
was correct, and nothing about either bug shows up from reading the code in
isolation. Both were found only once real subprocesses were started, signaled,
and torn down against real sockets on disk.

`AGENTS.md`'s "Verify by running it" already says this for behavior claims in
general. The addition here is narrower: *design-level correctness is not
evidence a feature works*, specifically for anything touching process
lifecycle, signals, or sockets. Write the manual probe -- a script that starts
a real subprocess, signals it, and checks the filesystem -- before writing the
pytest version. It is faster to iterate on and it is what actually found both
bugs here.

## A negative test is not optional when the positive one could pass by accident

The `leave()` roster-pid fix shipped with a positive test (`leave` given the
*correct* pid works) and it would have been worthless alone: `leave()`
already fell back to `os.getpid()` when no pid was given, so a naive positive
test exercises the fallback path, not the fix. The test that actually proves
anything is the negative one -- `leave` given a **wrong** pid must not leave
the listener running -- because that is the only input that distinguishes
"resolves from the roster" from "ignores the argument entirely and always
uses the caller's own pid."

The same shape applies to the `leave_host_pid_disagrees` warning. A wrong-pid
test proves the warning fires. A second test proves it does not fire on the
ordinary path, where no `--pid` is given
(`tests/agent_bus/cli/test_cli.py::test_cli_leave_with_a_wrong_pid_logs_a_warning`
and `::test_cli_leave_with_no_pid_flag_logs_nothing`). Without the second test,
a caller that fills in its own pid before the roster answers makes every
ordinary call warn.

**The tell:** if a fix corrects a wrong input rather than rejecting it, write
two tests -- wrong input still safe, and the wrong-but-common shape of "input
absent" still silent. One without the other only tests half the fallback
chain.

## Read the log content, not just the return code

A check on `rc == 0` or on socket existence does not show that the
`leave_host_pid_disagrees` warning is right. The test has to read the actual
JSONL record: `severity`, `message`, and the `host_pid` and `roster_pid`
fields. A test that only checks that logging did not crash passes when the
warning fires on every call, on no calls, or with the wrong fields.

Where a change's whole point is what gets written to a structured log
(`docs/structured-logging.md`'s contract), the assertion has to read that
log's actual content, not a proxy for it.

## `os.kill(pid, 0)` inside a long-lived test process is not liveness

`is_pid_alive()` (`process.py`) is correct in production -- a fresh CLI
invocation, checking a pid, exits either way. Inside `pytest`, calling the
same primitive against a subprocess that already exited and was never
`wait()`-ed on returns `True` for as long as the test session runs: the dead
child is a zombie, and `os.kill(pid, 0)` succeeds against a zombie by
design -- that's what lets `waitpid` find it later.

Chasing this cost real time here: a `leave()` regression test flaked at
~60-70%, and the first three fix attempts were aimed at pytest's fd capture
mode and signal-delivery timing, both wrong. Neither was the bug. Enough
manual reproduction outside pytest (see "manual probe" above) eventually
showed the *actual* process was dying correctly and fast; only the test's own
liveness check was lying.

**The fix, and the rule:** check something the code under test is actually
responsible for -- here, whether the listener's `.sock` file still exists,
since that's what `stop_uds_listen()` unlinks and what a real sender would
find. Never `os.kill(pid, 0)` a subprocess a test spawned, inside a test
process that outlives the child without reaping it.

## The real bug was two capture layers away from the first hypothesis

The flake above was real -- 60-70% failure, reproducible -- but its actual
cause (`run_listen` installing its SIGTERM handler seconds after the socket
became reachable, so a signal in that window hit Python's *default*
disposition: instant death, no cleanup) had nothing to do with any of the
three things tried first. Each wrong hypothesis was plausible on its own
(pytest's fd-level capture *does* redirect file descriptors a subprocess can
inherit; signal delivery to a `start_new_session=True` child *is* a
real thing to doubt) and each cost a full isolated repro to rule out.

What separated the wrong hypotheses from the right one: the wrong ones were
about the *test harness*. The right one was found by adding a log line to
`run_listen` itself and reading what it actually printed on a failing run --
which showed the listener's own log was **empty**, meaning the process died
before it reached even its first `print()`. That is evidence about the code
under test, not about pytest, and it's the only kind of evidence that would
have pointed at the real fix.

**The tell:** if a hypothesis is about the test framework rather than the
code being tested, and the second such hypothesis has also failed to explain
the failure, stop guessing and instrument the subject itself.

## The bug-fix commit needs a test that fails without it

Every fix in this cycle was validated by literally reverting it (`git stash`
the fix, rerun the new tests, confirm the relevant one fails and only that
one) before the two commits were split. This is what makes "these two
commits are independent" a checked claim in the PR body rather than an
assertion: the feature commit's tests all pass on their own, and exactly one
test in the bug-fix commit fails without it. A fix commit whose own tests
still pass with the fix reverted is not tested; it is decorated.
