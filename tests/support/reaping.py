"""Which processes a test's teardown is allowed to kill.

`tests/conftest.py` reaps the listeners a test started -- they are detached, so
nothing else will. This is the half of that decision worth testing on its own,
and it lives here rather than in the conftest because `tests/support` is on
`sys.path` and a conftest module is not importable by name.

**#214 is why it is a function at all.** `run_listen` writes the *publishing*
pid into `listeners/<watch_pid>.pid`, and `tests/agent_bus/transport/
test_uds.py` calls `run_listen` in a **thread** -- so that file legitimately
holds pytest's own pid. On macOS there is no `/proc`, so the "is this really a
listener" check returned True unconditionally, and the teardown SIGTERMed the
test session partway through that file: `exit 143`, no summary line, on
unmodified `main`.

A regression whose failure mode is a dead session reports no assertion at all,
so the predicate has to be checkable without triggering it.
"""

from __future__ import annotations

import os

#: Pids the teardown must never signal, however a pid file names them. Our own
#: process because `run_listen` in a thread writes it; our parent because that
#: is `uv` or the shell, and killing it takes the run down just as surely.
NEVER_SIGNAL = frozenset({os.getpid(), os.getppid()})


def looks_like_our_listener(pid: int) -> bool:
    """True unless `/proc` positively says this pid is something else.

    **No subprocess, deliberately.** `ps` would be the obvious way to read a
    command line, but `test_presence_reconciliation` monkeypatches
    `subprocess.Popen` and that patch is still live during teardown, so
    `subprocess.run` picks up the fake and raises. Reading `/proc` is a file
    read.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"agent_bus" in f.read()
    except OSError:
        # No /proc (macOS), or the process is already gone. Either way the
        # tmp_path scoping is what stands behind this, and a SIGTERM to a dead
        # pid is a no-op.
        return not os.path.isdir("/proc")


def reapable(pid: int) -> bool:
    """Whether the teardown may signal `pid`.

    A listener worth reaping is one `start_uds_listen` spawned, which is always
    a separate process. It can never be this one, so refusing to signal
    ourselves costs nothing -- and it is the difference between reaping a leak
    and killing the run.
    """
    return pid not in NEVER_SIGNAL and looks_like_our_listener(pid)


def still_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
