"""Who is on the bus, and what they are doing."""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any

from .. import log, store
from ..adapters import addressing
from ..listener import (
    host_pid_for_listener,
    publish_status,
    rename_uds_listen,
    start_uds_listen,
    stop_uds_listen,
)
from ..log import logged
from ..protocol import (
    AgentTarget,
    BridgeAddress,
    normalize_kind,
    resolve_kind_filter,
    roster_to_public,
)


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


def dead_holder(
    target: AgentTarget | BridgeAddress, home: str | None = None
) -> dict[str, Any] | None:
    """The roster entry `target` last belonged to, only if it is not live.

    `find_entry` prefers a live match over a stale one, so this collapses
    "nothing has ever held that name" and "something live holds it now" into
    the same `None` -- a caller asking whether there is a dead mailbox worth
    reading only ever needs the third case answered, and both of the others
    mean no.

    Not `@logged`: it is a probe, not a verb, and the exception-shaped way to
    ask this question -- calling `messages.inbox` and catching the "no such
    agent" it raises for the first two cases -- logged a WARNING on every
    ordinary first start of any address, because `@logged` had no way to know
    the caller was about to treat that as routine.
    """
    entry = store.find_entry(target, home=home)
    if entry is None or addressing.is_live(entry):
        return None
    return roster_to_public(entry)


@logged
def leave(name: AgentTarget, host_pid: int | None = None, home: str | None = None) -> bool:
    """Give up the name and take the listener down with it. The other half of
    `join`.

    `join` had no counterpart, so anything that used it leaked a listener: the
    listener is a detached process and does not die with its parent. A bridge
    run as a launchd service made that visible twice over -- `launchctl
    kickstart -k` waited about two minutes for a process group whose surviving
    member was the listener, and the orphan went on publishing a
    Claude-shaped session file, so the peer stayed discoverable after the thing
    it stood in for had stopped.

    The roster entry's own pid is tried first, but it is not always the
    right key. `join` always registers under the real host pid, so for a
    `join`ed peer `roster_pid` is exactly what `stop_uds_listen` (keyed on
    the *host* pid: `listeners/<host_pid>.pid`) needs. A hand-started
    `agent-bus listen --pid HOST` with nothing registered yet is a different
    shape: `run_listen`'s adopt loop finds no existing entry (`--adopt` is
    internal-only, never passed from a bare `listen` invocation, so that
    loop's deadline is `now + 0`) and registers fresh under its own pid --
    so the roster entry's pid is the *listener's*, not the host's, while the
    pid file on disk is still keyed by the host pid the caller actually
    knows. Trusting `roster_pid` alone here silently reintroduces the exact
    bug this function exists to fix: `stop_uds_listen(roster_pid)` finds no
    matching pid file, does nothing, and the caller's correct `host_pid` --
    the one that would have worked -- is never tried. So `host_pid` is the
    fallback whenever stopping by `roster_pid` reports it found nothing to
    stop, not only when the roster has no pid at all.

    A caller passing a `host_pid` that disagrees with the roster gets a
    warning only once the outcome says which one was actually wrong: a
    stale or mistyped `host_pid` (a CLI invocation days after the one that
    joined, say) is a real symptom worth a record, but a *correct* `host_pid`
    against a listener the roster names by its own pid is not a caller
    mistake -- it is the shape above, and it must not warn.

    Best-effort in both halves, and deliberately so: this runs while something
    is already shutting down, and a teardown that raises turns a clean stop
    into a crash.
    """
    entry = store.find_entry(name, home=home)
    roster_pid = entry.pid if entry and entry.pid else None
    # roster_pid first -- right for a join()ed peer, whose entry always
    # carries the real host pid -- then host_pid, only when roster_pid
    # either does not exist or did not actually stop anything: that second
    # case is the hand-started-listener shape above, where the roster names
    # the listener's own pid but the pid file on disk is keyed by the host
    # pid the caller knows. Our own pid, unchanged, is the last resort when
    # neither candidate exists at all -- the same single value the old code
    # fell to.
    stopped = False
    stopped_pid = None
    # Third candidate, and the one that makes `leave --name X` work with no
    # pid at all: recover the host pid from the listener pid the roster does
    # hold. Without it the hand-started shape is only fixable by a caller who
    # already knows the host pid -- and the caller who knows it is exactly the
    # caller who did not need help. Last, because it reads a directory, and the
    # two candidates above answer without touching the disk twice.
    recovered = host_pid_for_listener(roster_pid, home=home) if roster_pid else None
    candidates = [p for p in dict.fromkeys((roster_pid, host_pid, recovered)) if p]
    for candidate in candidates:
        with contextlib.suppress(OSError):
            stopped = stop_uds_listen(candidate, home=home)
        if stopped:
            stopped_pid = candidate
            break
    if not stopped and not candidates:
        with contextlib.suppress(OSError):
            stopped = stop_uds_listen(os.getpid(), home=home)
    # Worth a record only when host_pid is what actually stopped something
    # and roster_pid was tried first and failed -- that is the caller
    # correcting a real roster/host-pid mismatch, not a mistake. A
    # caller-supplied host_pid that never stops anything, with a different
    # roster_pid that does, is the actual mistake: a stale or mistyped pid
    # that happened not to matter because the roster answered correctly.
    if host_pid and roster_pid and host_pid != roster_pid and stopped_pid == roster_pid:
        log.warn(
            "leave: host_pid disagrees with roster, using roster's",
            name=name, host_pid=host_pid, roster_pid=roster_pid,
        )
    try:
        return store.unregister(name, home=home) or stopped
    except OSError:
        return stopped


@logged
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
