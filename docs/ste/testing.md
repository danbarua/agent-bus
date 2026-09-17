# Testing Practices

## Manual probes before pytest tests

Design-level review does not catch process, signal, or socket bugs. `join`/`leave` (#159, #160) show this. Both defects were invisible until real subprocesses were started, signaled, and torn down against real sockets on disk.

`AGENTS.md`'s "Verify by running it" section states this for behavior claims generally. Process lifecycle, signal, and socket behavior need verification by running real code, not just design review.

Write a manual probe before writing the pytest version. A manual probe is a script that starts a real subprocess, signals it, and checks the filesystem. A manual probe iterates faster than a pytest suite. It found both bugs in this cycle.

## Negative tests for fallback behavior

A positive test can pass without exercising the fix it is meant to prove. `leave()` falls back to `os.getpid()` when no pid is given. A positive test with the correct pid exercises only this fallback path.

A negative test proves the fix: `leave` given a wrong pid must not leave the listener running. Only a wrong-pid input tells two behaviors apart. One behavior resolves the pid from the roster. The other ignores the argument and always uses the caller's own pid.

The same pattern applies to a log warning. A wrong-pid test proves the warning fires. A silence test, using the ordinary path with no `--pid`, proves the warning does not fire elsewhere.

`cmd_leave` pre-filled `os.getpid()` before the roster resolved the pid. This bug was invisible to the wrong-pid test. It would have made every ordinary call warn, backwards from the feature's intent. The silence test caught it.

**The tell:** when a fix corrects a wrong input, write two tests. Test that the wrong input is now safe. Test that the common case, an absent input, stays silent. Either test alone covers only half the fallback chain.

## Asserting on log content

For `leave()`'s log warning, assert on severity, message prefix, and the `host_pid` and `roster_pid` fields. Do not assert only that logging did not crash. A warning could fire on every call, on no calls, or with the wrong fields. A "no crash" check would still pass in each case.

When a change controls what a structured log records, assert on the log's actual content. See the contract in `docs/structured-logging.md`.

## Zombie children and `os.kill(pid, 0)`

`is_pid_alive()` (in `process.py`) is correct for a fresh CLI invocation. A fresh process checking a pid exits either way. Inside `pytest`, the same check behaves differently.

A subprocess that already exited, and was never `wait()`-ed on, is a zombie. `os.kill(pid, 0)` on a zombie succeeds by design, so `waitpid` can find it later. `is_pid_alive()` then returns `True` for that zombie for as long as the test session runs.

A `leave()` regression test flaked at about 60-70% because of this zombie behavior. The manual probe method (see above) showed the process died correctly and quickly. Only the test's own liveness check was wrong.

**The fix and the rule:** check something the code under test is actually responsible for. For a listener, check whether its `.sock` file still exists. `stop_uds_listen()` unlinks that file, and a real sender would find it gone. Do not use `os.kill(pid, 0)` on a subprocess a test spawned. This applies inside a test process that outlives the child without reaping it.

## Instrumenting the code under test

`run_listen` installs its SIGTERM handler seconds after the socket becomes reachable. A signal delivered in that window hits Python's default disposition: instant death, no cleanup. This caused the flake described above.

Instrumenting `run_listen` with a log line found the cause. On a failing run, the listener's own log was empty. The process died before its first `print()`. That is evidence about the code under test.

**The tell:** when a hypothesis is about the test framework, and a second such hypothesis has also failed, stop guessing. Instrument the code under test directly.

## Reverting a fix to check its test

Verify each fix by reverting it. Run `git stash` on the fix, rerun the new tests, and confirm that only one test fails. Do this before splitting the feature commit from the bug-fix commit.

This makes commit independence a checked claim in the PR body. The feature commit's tests all pass on their own. Only one test in the bug-fix commit fails without the fix applied.

If a fix commit's tests still pass with the fix reverted, the fix has no real test.

## Related enforcement

`tests/agent_bus/test_conventions.py` enforces a narrower class of rule: a passing suite that still hides a real defect. Each entry there has its own enforcing test beside it.
