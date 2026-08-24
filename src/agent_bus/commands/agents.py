"""Who is on the bus, and what they are doing."""

from __future__ import annotations

import os
from typing import Any

from .. import store
from ..listener import publish_status, rename_uds_listen
from ..protocol import normalize_kind, resolve_kind_filter, roster_to_dict


def list_agents(kind: str | None = None, home: str | None = None) -> list[dict[str, Any]]:
    """Live roster, optionally filtered to one harness.

    The filter is resolved in one place now. It used to be resolved twice, and
    the two disagreed: the CLI lowercased before testing for "all", the MCP
    server tested first and lowercased after, so `kind="ALL"` asked for a
    harness literally named "all" and got an empty list -- from the surface
    whose own tool description invites the word.
    """
    entries = store.list_agents(kind=resolve_kind_filter(kind), home=home)
    return [roster_to_dict(e) for e in entries]


def _host_pid(explicit: int | None, home: str | None) -> int | None:
    """Which process this registration is for.

    An explicit pid wins. Otherwise adopt the one this process already holds:
    that is what makes a second register() a rename rather than a duplicate
    entry, which is the whole point of letting an agent claim a friendly name
    after its hook has already registered it under a derived one. Falling back
    to our own pid is last, and for the CLI it is nearly always wrong -- the
    command exits immediately and the entry is pruned -- but it is what the
    caller asked for when nothing else is known.
    """
    if explicit is not None:
        return explicit
    me = store.get_self(home)
    if me is not None and me.pid:
        return me.pid
    return os.getpid()


def register(
    name: str,
    kind: str | None = None,
    pid: int | None = None,
    cwd: str | None = None,
    home: str | None = None,
    aliases: list[str] | None = None,
    native: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Claim a name on the bus for a process.

    `aliases` are other addresses denoting this same agent -- a harness's own
    session address, so the registered entry and the discovered one reconcile
    into a single row instead of the agent appearing twice.
    """
    entry = store.register(
        name=name,
        kind=normalize_kind(kind),
        cwd=cwd,
        pid=_host_pid(pid, home),
        home=home,
        aliases=aliases,
        native=native,
    )
    # Keep the socket's advertised name in step with the roster, or a sender
    # reads one name from the listing and cannot reach it. No-ops for a
    # registrant with no published listener, which is the common CLI case.
    if entry.pid:
        rename_uds_listen(entry.pid, entry.name, home=home)
    return {**roster_to_dict(entry), "registered": True}


def self_info(home: str | None = None) -> dict[str, Any]:
    """This process's registration, walking ancestor pids."""
    entry = store.get_self(home)
    if entry is None:
        return {"registered": False}
    return {**roster_to_dict(entry), "registered": True}


def set_status(
    status: str,
    cwd: str | None = None,
    home: str | None = None,
) -> dict[str, Any]:
    """Report what this agent is doing.

    Two places have to hear it and only one of them always exists. The roster
    is what any listing reads and is the only home a Claude peer's status has,
    since it publishes no listener; the session file is what a Claude peer
    reads about us, and only peers that publish a listener have one. Reporting
    to the roster alone is a success, not a partial failure.
    """
    me = store.get_self(home)
    if me is None:
        return {"recorded": False, "published": False, "status": status,
                "reason": "not registered"}
    recorded = store.set_status(status, home=home)
    published = publish_status(me.pid, status, cwd, home=home) if me.pid else False
    return {"recorded": bool(recorded), "published": bool(published), "status": status}
