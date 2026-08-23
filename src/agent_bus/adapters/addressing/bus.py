"""`bus` -- the uuid register() mints for an agent that joined by asking.

Identity is the process that registered, so liveness is that process.

Every bus address has a mailbox, including a Claude one -- unlike the `session`
space, where a discovered `claude:<sid>` has none. The line is consent:
calling register() is asking to be on the bus, mailbox included, whereas being
*noticed* by discovery is not. Writing to a Claude session nobody asked us to
file for is what orphaned four inboxes here.
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
