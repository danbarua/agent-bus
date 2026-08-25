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


def proc_start(pid: int | None) -> str | None:
    """Process start time, as ps reports it. None if it cannot be read."""
    if not pid or pid <= 0:
        return None
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        if r.returncode == 0:
            out = r.stdout.strip()
            return out or None
    except (OSError, subprocess.SubprocessError):
        # No ps, or it would not run. "Cannot tell" is what None says.
        pass
    return None


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
    return current == started
