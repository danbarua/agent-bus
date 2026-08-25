"""Transport adapters: how a message actually reaches an agent of a given kind.

The capability that had no home at all before -- one transport lived in
uds.py, one in codex_client.py, nothing enumerated them, and the CLI exposed
each as its own vendor-named command (`send-peer`, `send-codex`). Callers had
to know a target's harness and pick the matching command, which is precisely
the thing the bus exists to hide.

`for_kind()` is the routing table. A kind with no entry uses the file bus,
which is the honest default: it is what an agent gets when its harness has no
native way in.

No transport falls back to another. Each kind reads exactly one channel -- a
Claude peer has no inbox and never sees a file-bus message, grok and omp never
see a UDS frame -- so a fallback would deliver into a channel the recipient
does not read, and report success for a message that arrived nowhere.
"""

from __future__ import annotations

from typing import Any

from . import claude, codex, filebus

# Kinds with a native channel. Everything else -- grok, omp, and any harness we
# have not heard of -- reads the file bus.
ADAPTERS: tuple[Any, ...] = (claude, codex)


def for_kind(kind: str) -> Any | None:
    """The native transport for this kind, or None to mean the file bus."""
    for adapter in ADAPTERS:
        if kind == adapter.KIND:
            return adapter
    return None


def resolve_unknown(target: str) -> tuple[Any, dict[str, Any]] | None:
    """Ask each native transport whether it can address a target the bus cannot.

    Only codex answers today: its threads are addressable but never appear as
    roster entries, because codex's registry records no pid and no socket to
    build one from. This runs only after roster and discovery have both missed.
    """
    for adapter in ADAPTERS:
        try:
            entry = adapter.resolve(target)
        except Exception:  # noqa: BLE001,S112  # a transport adapter may raise anything
            continue
        if entry is not None:
            return adapter, entry
    return None


__all__ = ["ADAPTERS", "claude", "codex", "filebus", "for_kind", "resolve_unknown"]
