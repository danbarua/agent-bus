"""Process liveness and identity. A leaf: imports nothing from this package.

Split out of store.py so the adapters can answer "is this pid alive" without
importing the roster. Every adapter needs it and none of them need a bus, and
the old arrangement had store reaching into adapters while adapters reached
back into store -- survivable only because store's half was a function-local
import. Splitting adapters by capability multiplies those edges, so the cycle
gets cut here rather than papered over again.
"""

from __future__ import annotations

import os
import subprocess


def is_pid_alive(pid: int | None) -> bool:
    """Does a process with this pid exist? Not "can we signal it"."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        # ESRCH -- no such process. The only answer that means dead.
        return False
    except PermissionError:
        # EPERM -- the process EXISTS, we simply may not signal it. Reporting
        # that as dead prunes live agents belonging to another uid. It never
        # fired on a single-user laptop, which is why it survived; a container
        # running agents under a different uid to the bus hits it immediately.
        return True
    except OSError:
        return False


# Prefix for the Linux form. The value is meaningless without it -- and worse,
# indistinguishable from a `ps` timestamp to anything comparing strings, which
# is the whole of is_process_alive.
_BOOT_TICKS = "boot:"


def _proc_start_linux(pid: int) -> str | None:
    """Start time in clock ticks since boot, from /proc/<pid>/stat.

    **`ps -o lstart=` is a wall-clock time and moves when the wall clock does.**
    Measured: a 3-minute `date -s` step moved the reported lstart of a process
    that had not restarted by exactly 3 minutes, while this field was
    byte-identical. On a machine whose clock corrects -- a freshly booted VM
    reaching NTP, which is every CI worker -- every recorded start time stops
    matching at once, and the guard below declares every live agent dead.

    Ticks since boot cannot do that. They also still distinguish a recycled pid,
    which is the only reason the field exists.
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            data = f.read()
    except OSError:
        return None
    # Field 2 is the executable name in parentheses and may contain both spaces
    # and parentheses -- `(a b) c)` is a legal comm. Splitting from the left is
    # the classic way to get this wrong; the last `)` is the only safe anchor.
    try:
        rest = data[data.rindex(")") + 2:].split()
        return f"{_BOOT_TICKS}{int(rest[19])}"  # field 22
    except (ValueError, IndexError):
        return None


def proc_start(pid: int | None) -> str | None:
    """Process start time. None if it cannot be read.

    Two formats, deliberately distinguishable -- see `_same_format`.
    """
    if not pid or pid <= 0:
        return None
    if os.path.isdir("/proc"):
        got = _proc_start_linux(pid)
        if got:
            return got
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
            # **`ps -o lstart=` formats by locale.** Measured on macOS 15:
            #
            #   LANG=en_GB.UTF-8   Fri 28 Aug 13:22:06 2026
            #   LC_ALL=C           Fri Aug 28 13:22:06 2026
            #
            # The roster stores the string, so two processes on one machine
            # disagreed about whether a pid was the same process -- and a
            # launchd service, which inherits no locale at all, pruned every
            # entry a terminal had registered while the terminal pruned its.
            # Pinning the locale makes the string canonical wherever it is
            # produced; _comparable below handles what is already on disk.
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
        if r.returncode == 0:
            out = r.stdout.strip()
            return out or None
    except (OSError, subprocess.SubprocessError):
        # No ps, or it would not run. "Cannot tell" is what None says.
        pass
    return None


def _same_format(a: str, b: str) -> bool:
    """Are these two start times even comparable?

    They are not, across the change that introduced ticks-since-boot: every
    entry already on disk holds a `ps` timestamp. Comparing the two would make
    every agent on every machine look dead at upgrade -- inflicting, once and
    deliberately, exactly the failure this change exists to remove.
    """
    return a.startswith(_BOOT_TICKS) == b.startswith(_BOOT_TICKS)


def _comparable(value: str) -> tuple[str, ...]:
    """The fields of a start time, in an order neither locale chose.

    `ps -o lstart=` orders day and month by locale, so the same instant is
    written two ways on one machine. The fields themselves are identical, so
    comparing them as a sorted set answers "is this the same process" without
    caring which arrangement produced it.

    This is what lets the fix land without a flag day. Every entry already on
    disk carries whatever locale wrote it, and re-pruning the whole roster to
    adopt a canonical format would inflict, once, exactly the failure being
    removed.
    """
    return tuple(sorted(value.split()))


def is_process_alive(pid: int | None, started: str | None = None) -> bool:
    """Liveness that survives pid reuse.

    A pid alone is not identity: pids are recycled, and a recycled one makes a
    dead agent look live. Claude Code checks the recorded process start time
    against the running process for exactly this reason. When we have no
    recorded start time (an entry written before the field existed, or a
    platform where ps gave us nothing) we fall back to the pid alone rather
    than declaring a live agent dead.
    """
    if not is_pid_alive(pid):
        return False
    if not started:
        return True
    current = proc_start(pid)
    if current is None:
        return True
    if not _same_format(current, started):
        # Recorded in the other format, so it says nothing either way. "Cannot
        # tell" is alive here, as it is for a missing value: the next
        # register() rewrites it in the current format and the ambiguity is
        # gone after one cycle.
        return True
    return _comparable(current) == _comparable(started)
