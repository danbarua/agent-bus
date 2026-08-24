"""Discovery adapters: read each harness's own registry.

Membership here is not the same as membership in lifecycle or transport --
that is the whole reason the package is split by capability.

Codex is deliberately absent. It was here, reading
`~/.codex/process_manager/chat_processes.json`, which has been `[]` since July
and cannot be otherwise: Codex records no pid anywhere in its thread metadata,
so there is nothing for a process-shaped adapter to find. A Codex session joins
this bus the way it was always going to -- by registering through the MCP
server, which since the clientInfo handshake it does without being asked.
"""

from __future__ import annotations

from typing import Any

from . import claude, grok, omp

ADAPTERS: tuple[Any, ...] = (claude, grok, omp)


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
