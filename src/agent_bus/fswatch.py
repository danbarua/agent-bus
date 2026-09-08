"""Block on stdin readiness and a directory changing, natively, per platform.

No polling interval: `Waiter.wait()` blocks in the OS until either the MCP
peer sends a request or the watched directory's contents change on disk,
using each platform's native event mechanism -- kqueue on macOS/BSD,
inotify on Linux -- rather than waking on a timer to check nothing happened.

Both mechanisms watch an inode, not a path: `store.py`'s `compact_inbox` and
`ack_message` rewrite the inbox file via `os.replace()`, which swaps in a new
inode at the same path and would silently orphan a watch held on the file
itself. Watching the *containing directory* sidesteps this -- a directory's
own inode is untouched by a rename inside it, and any event there (this
peer's inbox file appended to, or rewritten, or a neighbor's) is cheap enough
to just trigger a recheck rather than try to filter it precisely.

An unknown platform falls back to watching stdin alone: tool calls keep
working, resource-update notifications simply never fire -- degrade, don't
fail the host, same rule every other harness-facing surface in this package
follows.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import select
import sys
from typing import BinaryIO, Protocol


class Waiter(Protocol):
    def wait(self, timeout: float | None) -> tuple[bool, bool]:
        """Block until stdin is readable or the watched directory changes.

        Returns `(input_ready, dir_changed)`. Either or both may be true; a
        timeout elapsing with neither is also possible and means only
        "nothing happened, loop again."
        """
        ...

    def close(self) -> None: ...


def watcher(inp: BinaryIO, watch_dir: str) -> Waiter:
    if sys.platform == "darwin" or sys.platform.startswith(("freebsd", "openbsd", "dragonfly")):
        return _KqueueWaiter(inp, watch_dir)
    if sys.platform.startswith("linux"):
        return _InotifyWaiter(inp, watch_dir)
    return _StdinOnlyWaiter(inp)


class _StdinOnlyWaiter:
    def __init__(self, inp: BinaryIO) -> None:
        self._inp_fd = inp.fileno()

    def wait(self, timeout: float | None) -> tuple[bool, bool]:
        ready, _, _ = select.select([self._inp_fd], [], [], timeout)
        return bool(ready), False

    def close(self) -> None:
        pass


class _KqueueWaiter:
    def __init__(self, inp: BinaryIO, watch_dir: str) -> None:
        self._kq = select.kqueue()
        self._inp_fd = inp.fileno()
        self._dir_fd = os.open(watch_dir, os.O_RDONLY)
        self._kq.control(
            [
                select.kevent(self._inp_fd, filter=select.KQ_FILTER_READ,
                              flags=select.KQ_EV_ADD),
                select.kevent(
                    self._dir_fd, filter=select.KQ_FILTER_VNODE,
                    flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                    fflags=(select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND
                            | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE),
                ),
            ],
            0,
        )

    def wait(self, timeout: float | None) -> tuple[bool, bool]:
        events = self._kq.control(None, 2, timeout)
        idents = {e.ident for e in events}
        return self._inp_fd in idents, self._dir_fd in idents

    def close(self) -> None:
        os.close(self._dir_fd)
        self._kq.close()


class _InotifyWaiter:
    """ctypes over libc: no stdlib inotify wrapper exists.

    inotify_init1() returns an ordinary, select()-able fd -- readable once an
    event is pending -- so stdin and the watch both go into one plain
    `select.select()` call rather than needing epoll.
    """

    _IN_MODIFY = 0x00000002
    _IN_MOVED_TO = 0x00000080
    _IN_CREATE = 0x00000100
    _IN_CLOSE_WRITE = 0x00000008
    _WATCH_MASK = _IN_MODIFY | _IN_MOVED_TO | _IN_CREATE | _IN_CLOSE_WRITE

    def __init__(self, inp: BinaryIO, watch_dir: str) -> None:
        self._inp_fd = inp.fileno()
        libc = ctypes.CDLL(None, use_errno=True)
        self._inotify_fd = libc.inotify_init1(0)
        if self._inotify_fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        wd = libc.inotify_add_watch(
            self._inotify_fd, watch_dir.encode("utf-8", "surrogateescape"),
            self._WATCH_MASK,
        )
        if wd < 0:
            errno = ctypes.get_errno()
            os.close(self._inotify_fd)
            raise OSError(errno, "inotify_add_watch failed", watch_dir)

    def wait(self, timeout: float | None) -> tuple[bool, bool]:
        ready, _, _ = select.select([self._inp_fd, self._inotify_fd], [], [], timeout)
        dir_changed = self._inotify_fd in ready
        if dir_changed:
            # Drain so the fd goes back to not-readable; otherwise it wakes
            # every subsequent wait() with the same already-handled event.
            with contextlib.suppress(OSError):
                os.read(self._inotify_fd, 4096)
        return self._inp_fd in ready, dir_changed

    def close(self) -> None:
        os.close(self._inotify_fd)
