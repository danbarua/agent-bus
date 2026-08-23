"""Lifecycle adapters: harnesses that can host an agent-bus session.

Only two, and that is the point. A harness we can merely observe implements
discovery alone; being visible in a listing and being able to register are
different capabilities, and the tree now says so rather than leaving it in a
tuple in another module.
"""

from __future__ import annotations

from typing import Any

from . import claude, grok

# Order matters only in that the first match wins; the detect() predicates are
# meant to be mutually exclusive.
ADAPTERS: tuple[Any, ...] = (grok, claude)


def for_kind(kind: str) -> Any | None:
    for adapter in ADAPTERS:
        if adapter.KIND == kind:
            return adapter
    return None


__all__ = ["ADAPTERS", "claude", "for_kind", "grok"]
