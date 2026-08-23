"""`bus` -- the uuid register() mints for an agent that joined by asking.

Identity is the process that registered, so liveness is that process.
"""

from __future__ import annotations

from typing import Any

from ...address import BUS
from ._process_backed import is_live as _is_live

SPACE = BUS


def is_live(entry: Any) -> bool:
    return _is_live(entry)


def has_mailbox(entry: Any) -> bool:
    return True
