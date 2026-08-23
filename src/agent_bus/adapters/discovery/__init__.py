"""Discovery adapters: read each harness's own registry.

Every harness we can see from outside implements this, which is why it is the
only capability all four share. Membership here is not the same as membership
in lifecycle or transport -- that is the whole reason the package is split by
capability.
"""

from __future__ import annotations

from typing import Any

from . import claude, codex, grok, omp

ADAPTERS: tuple[Any, ...] = (claude, grok, omp, codex)


def discover_all() -> list[dict[str, Any]]:
    """Every live agent every harness knows about. Never raises.

    One broken adapter must not empty the listing: a harness whose registry
    has changed shape underneath us is the normal failure, and the rest of the
    bus is still worth showing.
    """
    out: list[dict[str, Any]] = []
    for mod in ADAPTERS:
        try:
            out.extend(mod.discover())
        except Exception:
            continue
    return out


__all__ = ["ADAPTERS", "claude", "codex", "discover_all", "grok", "omp"]
