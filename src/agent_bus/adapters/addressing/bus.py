"""`bus` -- the uuid register() mints for an agent that joined by asking.

Identity is the process that registered, so liveness is that process.

Every bus address has a mailbox. So does every other space now, `session`
included -- this used to say otherwise, and drew a line at consent: registering
asks to be on the bus, being *noticed* by discovery does not.

That line is gone and the reason it was drawn is worth keeping, because the
harm was real. Writing to a Claude session nobody asked us to file for
orphaned four inboxes here: unread mail in a file its owner would never read
and could never clear. What dissolved the objection was not deciding the harm
acceptable but removing it -- `commands/messages.send` writes the durable copy
**already acked** once a native transport delivered, so the unread never
exists. `adapters/addressing/session.py` carries the full argument and the
instruction not to re-add the exclusion citing those orphans.
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
