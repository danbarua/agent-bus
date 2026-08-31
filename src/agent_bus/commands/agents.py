"""Who is on the bus, and what they are doing."""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any

from .. import log, store
from ..listener import (
    publish_status,
    rename_uds_listen,
    start_uds_listen,
    stop_uds_listen,
)
from ..log import logged
from ..protocol import normalize_kind, resolve_kind_filter, roster_to_public


@logged
def list_agents(kind: str | None = None, home: str | None = None) -> list[dict[str, Any]]:
    """Live roster, optionally filtered to one harness.

    The filter is resolved in one place now. It used to be resolved twice, and
    the two disagreed: the CLI lowercased before testing for "all", the MCP
    server tested first and lowercased after, so `kind="ALL"` asked for a
    harness literally named "all" and got an empty list -- from the surface
    whose own tool description invites the word.
    """
    entries = store.list_agents(kind=resolve_kind_filter(kind), home=home)
    return [roster_to_public(e) for e in entries]


#: Where a resolved host pid came from. The caller needs this, not just the
#: number: `os.getpid()` is right for a library import into a long-lived
#: process and wrong for a CLI that is about to exit, and nothing downstream
#: can tell those two apart from the pid alone.
PID_EXPLICIT = "explicit"
PID_ADOPTED = "adopted"
PID_SESSION = "session"
PID_OWN = "own"


def resolve_host_pid(
    explicit: int | None, home: str | None
) -> tuple[int | None, str]:
    """Which process this registration is for, and how we know.

    An explicit pid wins. Otherwise adopt the one this process already holds:
    that is what makes a second register() a rename rather than a duplicate
    entry, which is the whole point of letting an agent claim a friendly name
    after its hook has already registered it under a derived one.

    Then ask discovery which session we are running inside. That is what makes
    `agent-bus register` work from a shell with no flag: the harness already
    publishes its own pid, and the ancestor chain from the CLI reaches it. The
    alternative -- `lifecycle.host_pid()` -- cannot do this from a shell. It
    needs a session id, which only a hook payload carries, so it falls through
    to `os.getppid()` and lands on the `uv run` wrapper: a different corpse.

    Our own pid is last. For a library import into a long-lived process -- omp
    loading agent_bus into its IPython kernel -- it is exactly right. For the
    CLI it is never right, which is why this returns the source and lets that
    caller refuse.
    """
    if explicit is not None:
        return explicit, PID_EXPLICIT
    me = store.get_self(home)
    if me is not None and me.pid:
        return me.pid, PID_ADOPTED
    session = store.session_entry_for_current_process(home)
    if session is not None and session.pid:
        return session.pid, PID_SESSION
    return os.getpid(), PID_OWN


def _host_pid(explicit: int | None, home: str | None) -> int | None:
    return resolve_host_pid(explicit, home)[0]


@logged
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
    return {**roster_to_public(entry), "registered": True}


def _wait_until_reachable(listener_pid: int, timeout: float) -> bool:
    """Block until the published listener has bound its socket.

    start_uds_listen spawns a detached process and returns as soon as it has
    been launched, which is before the socket exists.
    """
    from ..uds import _sock_dir

    path = os.path.join(_sock_dir(), f"{listener_pid}.sock")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


@logged
def join(
    name: str,
    kind: str | None = None,
    pid: int | None = None,
    cwd: str | None = None,
    home: str | None = None,
    aliases: list[str] | None = None,
    native: dict[str, Any] | None = None,
    ready_timeout: float = 15.0,
) -> dict[str, Any]:
    """Claim a name and become reachable, returning once both are true.

    register() claims a name and stops. That is not enough to be *addressed*
    by a peer that messages natively: for that an agent needs a published
    listener, which is also what gives it a socket to send **from** -- an
    outbound frame carries the sender's socket as its reply address, so
    without one it cannot answer either.

    The wait is the substance. The listener is a detached process, so there is
    a window in which the agent is registered and cannot yet send. An agent
    that starts working inside that window loses whatever it tried to send:
    a bridge holding queued mail dropped it, and reported the recipient had
    refused.

    `reachable` says whether that completed. False means the name is claimed
    but native peers cannot reach it yet.
    """
    entry = register(name, kind, pid=pid, cwd=cwd, home=home,
                     aliases=aliases, native=native)
    host = entry.get("pid")
    if not host:
        return {**entry, "reachable": False}
    listener_pid = start_uds_listen(entry["name"], int(host), home=home)
    if not listener_pid:
        return {**entry, "reachable": False}
    return {**entry, "reachable": _wait_until_reachable(listener_pid, ready_timeout)}


@logged
def leave(name: str, host_pid: int | None = None, home: str | None = None) -> bool:
    """Give up the name and take the listener down with it. The other half of
    `join`.

    `join` had no counterpart, so anything that used it leaked a listener: the
    listener is a detached process and does not die with its parent. A bridge
    run as a launchd service made that visible twice over -- `launchctl
    kickstart -k` waited about two minutes for a process group whose surviving
    member was the listener, and the orphan went on publishing a
    Claude-shaped session file, so the peer stayed discoverable after the thing
    it stood in for had stopped.

    The roster entry's own pid wins over `host_pid` when both are available.
    `join` registered the entry under the real host pid; a caller passing a
    stale or mistyped `host_pid` (a CLI invocation days after the one that
    joined, say) would otherwise ask `stop_uds_listen` to tear down a listener
    under the wrong pid, unregister the name anyway, and report `True` -- a
    leave that looks clean while the listener it was supposed to take down
    keeps running. `host_pid` remains the fallback for the one case the
    roster cannot answer: the entry is already gone (a second `leave`, or one
    racing a crash) but the listener process it started might not be.

    Best-effort in both halves, and deliberately so: this runs while something
    is already shutting down, and a teardown that raises turns a clean stop
    into a crash.
    """
    entry = store.find_entry(name, home=home)
    roster_pid = entry.pid if entry and entry.pid else None
    if host_pid and roster_pid and host_pid != roster_pid:
        # Not this call's failure -- it corrected for it -- but a caller
        # passing a pid that disagrees with what it actually registered
        # under is a symptom worth a record wherever it came from.
        log.warn(
            "leave: host_pid disagrees with roster, using roster's",
            name=name, host_pid=host_pid, roster_pid=roster_pid,
        )
    target_pid = roster_pid or host_pid or os.getpid()
    stopped = False
    with contextlib.suppress(OSError):
        stopped = stop_uds_listen(target_pid, home=home)
    try:
        return store.unregister(name, home=home) or stopped
    except OSError:
        return stopped


def self_info(home: str | None = None) -> dict[str, Any]:
    """This process's registration, and failing that, whether it is reachable.

    Unregistered is two different situations and they used to answer alike.
    Being *reached* needs nothing installed -- a harness publishes its own
    session and peers address that -- while initiating needs a registration.
    So an unregistered session that discovery can see is on the bus and merely
    unnamed, and one it cannot see is not on the bus at all. Reporting both as
    a bare "not registered" is what told eleven agents they were absent while
    eleven peers could already write to them.
    """
    entry = store.get_self(home)
    if entry is not None:
        return {**roster_to_public(entry), "registered": True}
    session = store.session_entry_for_current_process(home)
    if session is None:
        return {"registered": False, "reachable": False}
    return {**roster_to_public(session), "registered": False, "reachable": True}


@logged
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
