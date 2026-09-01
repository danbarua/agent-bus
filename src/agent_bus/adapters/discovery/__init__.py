"""Discovery adapters: read each harness's own registry.

Membership here is not the same as membership in lifecycle or transport --
that is the whole reason the package is split by capability.

Codex is deliberately absent. It was here, reading
`~/.codex/process_manager/chat_processes.json`, which has been `[]` since July
and cannot be otherwise: Codex records no pid anywhere in its thread metadata,
so there is nothing for a process-shaped adapter to find. A Codex session joins
this bus the way it was always going to -- by registering through the MCP
server, which since the clientInfo handshake it does without being asked.

Grok is deliberately absent for the same reason, and was removed later (#184)
because its failure was quieter. It read `~/.grok/active_sessions.json`, which
grok prunes to `[]` at startup and never repopulates: measured empty 0.9s
before a live session was created and never written again, with
`worktrees.db.meta.last_auto_gc_at` 0.4s after that write showing the write was
the prune. Nothing anywhere in `~/.grok` records a live session's pid --
`worktrees.creator_pid` is worktree-only and GC'd -- which is the same
structural fact that retired codex, and this repo's own source audit had
already recorded it as a firm negative
(`docs/harnesses/grok-build-ipc-reference.md:287-291`).

The leader socket is grok's only live view and is not a substitute: it is a
routing table for an in-flight IPC session tree, carries `sessionId` and
`activity` but no pid, and has never been observed running here. A grok session
reaches this bus by registering through the MCP server, exactly as codex does.
"""

from __future__ import annotations

from typing import Any

from . import claude, omp

ADAPTERS: tuple[Any, ...] = (claude, omp)


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
        except Exception:  # noqa: BLE001,S112  # a harness adapter may raise anything
            continue
    return out


__all__ = ["ADAPTERS", "claude", "discover_all", "omp"]
