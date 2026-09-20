"""Claude lifecycle: what core asks to place a Claude session on the bus."""
from __future__ import annotations

import json
import os
from typing import Any

from ... import log as bus_log
from ... import registry_events as ev
from ...paths import claude_sessions_dir as _sessions_dir
from ...process import is_pid_alive

KIND = "claude"


def detect(env: dict[str, str]) -> bool:
    return bool(env.get("CLAUDE_PLUGIN_ROOT") or env.get("CLAUDE_PROJECT_DIR"))


def session_id(payload: dict[str, Any] | None, env: dict[str, str]) -> str | None:
    if payload:
        sid = payload.get("sessionId") or payload.get("session_id")
        if sid:
            return str(sid)
    return None


def host_pid(session_id: str | None, env: dict[str, str]) -> int | None:
    """Resolve via the published session files, trusting only a live pid.

    listdir order is arbitrary, so after a crash a stale <oldpid>.json can match
    first; registering that dead pid gets pruned on the next roster read and the
    session is invisible on the bus for its whole lifetime.
    """
    if not session_id:
        bus_log.emit(ev.HostPidLookup(entry_kind=KIND, decision="no_session_id"))
        return None
    sdir = _sessions_dir()
    stale: tuple[int, str] | None = None
    try:
        for fn in os.listdir(sdir):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(sdir, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            sid = data.get("sessionId") or data.get("session_id")
            if str(sid or "") == session_id:
                pid = data.get("pid")
                if pid and is_pid_alive(int(pid)):
                    bus_log.emit(ev.HostPidLookup(
                        entry_kind=KIND, decision="found", session_id=session_id,
                        holder_pid=int(pid), path=path))
                    return int(pid)
                if pid:
                    stale = (int(pid), path)
    except OSError:
        pass
    bus_log.emit(ev.HostPidLookup(
        entry_kind=KIND, decision="stale_pid" if stale else "not_found",
        session_id=session_id,
        holder_pid=stale[0] if stale else None, path=stale[1] if stale else None))
    return None


def session_name(session_id: str | None, cwd: str | None) -> str | None:
    return None


def workspace(env: dict[str, str]) -> str | None:
    return env.get("CLAUDE_PROJECT_DIR")
