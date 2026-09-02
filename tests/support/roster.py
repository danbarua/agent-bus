"""Roster lookups that say what went wrong when they find nothing.

`store.find_entry` returns `RosterEntry | None`, and a test that reaches
straight into the result fails as `AttributeError: 'NoneType' object has no
attribute 'id'` -- which names neither the target that was not found nor the
test's actual assumption. Every caller here assumes the lookup succeeds; this
says so once, and says it usefully.
"""

from __future__ import annotations

from typing import Any

from agent_bus import store
from agent_bus.protocol import AgentTarget


def found(target: AgentTarget, home: str | None = None) -> Any:
    """The entry, or an assertion naming what was not there."""
    entry = store.find_entry(target, home=home)
    assert entry is not None, f"no roster entry for {target!r} in {home!r}"
    return entry
