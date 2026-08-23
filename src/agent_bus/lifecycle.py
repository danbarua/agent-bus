"""Session lifecycle: who is starting, under what identity.

Formerly plugin_host.py, which was the mixing bowl of the repo -- detect_kind()
sniffed env vars, host_pid() was one branch per vendor, and session_start()
reached into adapters.grok for a session title. There was one adapter axis
(discovery) where there needed to be three, so everything vendor-specific that
was not discovery accumulated here.

Core now asks each adapter three questions and never sniffs for a harness
itself:

    detect(env)                     -- am I present
    host_pid(session_id, env)       -- which process is the session
    session_name(session_id, cwd)   -- what does the harness call it

and takes an explicit descriptor rather than deriving one from ambient state.
The name is also no longer a plugin reference: the MCP server is the tool
surface now, and hooks are only one of two entry points into lifecycle.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .adapters import claude as claude_adapter
from .adapters import grok as grok_adapter
from .listener import start_uds_listen, stop_uds_listen
from .protocol import FALLBACK_KIND, RosterEntry
from .store import get_home, register, unregister_by_pid


class HarnessLifecycle(Protocol):
    """What core needs from a harness to place a session on the bus."""

    KIND: str

    def detect(self, env: dict[str, str]) -> bool: ...
    def session_id(self, payload: dict[str, Any] | None, env: dict[str, str]) -> str | None: ...
    def host_pid(self, session_id: str | None, env: dict[str, str]) -> int | None: ...
    def session_name(self, session_id: str | None, cwd: str | None) -> str | None: ...
    def workspace(self, env: dict[str, str]) -> str | None: ...


# Order matters only in that the first match wins; the adapters' detect()
# predicates are meant to be mutually exclusive.
ADAPTERS: tuple[Any, ...] = (grok_adapter, claude_adapter)


@dataclass
class SessionDescriptor:
    """Everything core needs, resolved once, explicitly."""

    kind: str
    session_id: str | None
    pid: int | None
    cwd: str
    name: str


def derive_name(kind: str, session_id: str | None, pid: int | None = None) -> str:
    raw = (session_id or "").strip()
    token = re.sub(r"[^A-Za-z0-9_-]", "", raw)[:8]
    if token:
        return f"{kind}-{token}"
    if pid:
        return f"{kind}-{pid}"
    return kind


def detect_kind(env: dict[str, str] | None = None) -> str:
    e = dict(os.environ if env is None else env)
    for adapter in ADAPTERS:
        if adapter.detect(e):
            return adapter.KIND
    return FALLBACK_KIND


def _adapter_for(kind: str) -> Any | None:
    for adapter in ADAPTERS:
        if adapter.KIND == kind:
            return adapter
    return None


def host_pid(
    kind: str,
    session_id: str | None = None,
    env: dict[str, str] | None = None,
) -> int | None:
    """Which process is the session, according to its harness.

    Core does not know how any harness answers this -- grok reads
    active_sessions.json, claude reads its session files -- so it asks. The
    fallback when nothing claims it is our parent, which for a hook is the
    process that ran the hook.
    """
    e = dict(os.environ if env is None else env)
    adapter = _adapter_for(kind)
    if adapter is not None:
        if session_id is None:
            session_id = adapter.session_id(None, e)
        pid = adapter.host_pid(session_id, e)
        if pid:
            return pid
    ppid = os.getppid()
    return ppid if ppid > 1 else None


def describe(
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> SessionDescriptor:
    """Resolve the session's identity by asking whichever adapter claims it.

    An unrecognised harness is not an error: it gets the fallback kind, a
    pid-derived name and its own cwd, which is enough to register and be
    addressed. Refusing to describe it would be the closed-enum mistake again.
    """
    e = dict(os.environ if env is None else env)
    kind = detect_kind(e)
    adapter = _adapter_for(kind)

    sid = adapter.session_id(payload, e) if adapter else None
    pid = host_pid(kind, sid, e)

    cwd = (adapter.workspace(e) if adapter else None) or os.getcwd()
    name = (adapter.session_name(sid, cwd) if adapter else None) or derive_name(
        kind, sid, pid=pid
    )
    return SessionDescriptor(kind=kind, session_id=sid, pid=pid, cwd=cwd, name=name)


def session_start(
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    home: str | None = None,
    descriptor: SessionDescriptor | None = None,
) -> RosterEntry:
    """Register the session and publish a listener for it.

    `descriptor` lets a caller state the identity outright; without one it is
    resolved from the environment, which is what the hook path does.
    """
    desc = descriptor or describe(payload, env)
    entry = register(desc.name, desc.kind, cwd=desc.cwd, pid=desc.pid, home=home)
    # Every non-Claude peer needs the shim listener to appear in Claude's native
    # ListAgents and to receive native SendMessage. Claude sessions already have
    # their own socket, so they are the only kind that must not get one.
    if desc.kind != claude_adapter.KIND and desc.pid:
        try:
            start_uds_listen(entry.name, desc.pid, home=home)
        except OSError:
            pass
    return entry


def session_end(
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    home: str | None = None,
    descriptor: SessionDescriptor | None = None,
) -> bool:
    desc = descriptor or describe(payload, env)
    # Mirror session_start: it starts a listener for every non-claude kind, so
    # stopping only one kind's would leak a listener process per session.
    if desc.kind != claude_adapter.KIND and desc.pid:
        stop_uds_listen(desc.pid, home=home)
    return unregister_by_pid(desc.pid, home=home)


__all__ = [
    "HarnessLifecycle",
    "SessionDescriptor",
    "derive_name",
    "describe",
    "detect_kind",
    "host_pid",
    "get_home",
    "session_end",
    "session_start",
]
