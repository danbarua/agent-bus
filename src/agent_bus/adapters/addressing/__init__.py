"""Addressing adapters: what can be addressed, and how we know it still exists.

The question core asks most often -- every entry, every listing. It used to be
answered by one hardcoded rule, `is_pid_alive`, applied everywhere: the
discovery gate, the prune rule, the visibility rule. That rule is right for a
Claude session and wrong for a Codex thread, and there was nowhere to say so.

Sparse in the same way the other axes are: codex contributes a thread space and
no session space, claude a session space and no thread space.

That used to read "claude a *mailbox-less* session space", which was true until
#26 and is the exclusion `session.py` now says must never come back. Every
session has a mailbox, Claude included. Corrected here rather than left, because
a docstring restating a removed rule as settled fact is how the rule gets
re-added -- and this file and `bus.py` were both still arguing for it three
weeks after it went.
"""

from __future__ import annotations

from typing import Any

from ...address import Address
from ...address import parse as parse_address
from . import bus, pid, session, thread

ADAPTERS: tuple[Any, ...] = (bus, session, pid, thread)

# What an unrecognised space gets. Process-backed with a mailbox is what every
# address meant before spaces existed, so an unknown one behaves as it always
# has rather than vanishing.
DEFAULT = bus


def _address_of(entry: Any) -> Address:
    raw = entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
    if isinstance(raw, Address):
        return raw
    kind = entry.get("kind") if isinstance(entry, dict) else getattr(entry, "kind", None)
    return parse_address(str(raw or ""), kind_hint=kind)


def for_space(space: str) -> Any:
    for adapter in ADAPTERS:
        if space == adapter.SPACE:
            return adapter
    return DEFAULT


def for_entry(entry: Any) -> Any:
    return for_space(_address_of(entry).space)


def is_live(entry: Any) -> bool:
    """Does this agent still exist, by the rule of its own address space?"""
    return for_entry(entry).is_live(entry)


def has_mailbox(entry: Any) -> bool:
    """May a message be written to a file inbox at this address?

    Never consulted when *reading*. Mail already on disk stays readable
    whatever the rule says now.
    """
    return for_entry(entry).has_mailbox(entry)


__all__ = [
    "ADAPTERS",
    "DEFAULT",
    "bus",
    "for_entry",
    "for_space",
    "has_mailbox",
    "is_live",
    "pid",
    "session",
    "thread",
]
