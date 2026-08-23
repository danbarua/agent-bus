"""Grok lifecycle: what core asks to place a grok session on the bus."""
from __future__ import annotations

import json
import os
from typing import Any

from ...paths import grok_dir as _grok_dir

# The session title comes from the same registry read discovery does, so it is
# imported rather than reimplemented. One arrow between capabilities of the
# same vendor, and it points at the module that owns reading ~/.grok.
from ..discovery.grok import _session_title

KIND = "grok"


def detect(env: dict[str, str]) -> bool:
    """Hook-scoped signals only.

    Deliberately not GROK_SESSION_ID. That is set on the Bash/PTY tool's
    environment, so a shell spawned by Grok carries it and anything launched
    from that shell inherits it -- including a Claude session, which would then
    adopt a Grok identity and, on exit, unregister the live Grok one.
    """
    return bool(env.get("GROK_HOOK_EVENT") or env.get("GROK_PLUGIN_ROOT"))


def session_id(payload: dict[str, Any] | None, env: dict[str, str]) -> str | None:
    if payload:
        sid = payload.get("sessionId") or payload.get("session_id")
        if sid:
            return str(sid)
    return env.get("GROK_SESSION_ID") or None


def host_pid(session_id: str | None, env: dict[str, str]) -> int | None:
    """The pid of the grok session, not of the hook process running this."""
    if not session_id:
        return None
    path = os.path.join(_grok_dir(), "active_sessions.json")
    try:
        with open(path, encoding="utf-8") as f:
            sessions = json.load(f)
        if isinstance(sessions, list):
            for s in sessions:
                if str(s.get("session_id") or "") == session_id:
                    pid = s.get("pid")
                    if pid:
                        return int(pid)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def session_name(session_id: str | None, cwd: str | None) -> str | None:
    """Grok titles its sessions; prefer that over a derived name."""
    if not session_id:
        return None
    return _session_title(_grok_dir(), session_id, cwd)


def workspace(env: dict[str, str]) -> str | None:
    return env.get("GROK_WORKSPACE_ROOT")
