"""`pid` -- an agent named by the OS process behind it.

`codex:pid:<n>` and `omp:tty:<n>` both land here; the tty is how the pid was
found, not a different kind of thing.
"""

from __future__ import annotations

from typing import Any

from ...address import PID
from ._process_backed import is_live as _is_live

SPACE = PID


def is_live(entry: Any) -> bool:
    return _is_live(entry)


def has_mailbox(entry: Any) -> bool:
    return True
