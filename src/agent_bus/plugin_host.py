"""Grok / Claude plugin session hooks: register the host agent, not the hook pid."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .adapters.grok import _grok_dir as grok_home
from .adapters.grok import _session_title
from .protocol import Kind, RosterEntry
from .store import register, unregister


def _grok_dir() -> str:
    env = os.environ.get("AGENT_BUS_GROK_DIR")
    if env:
        return env
    return os.path.expanduser("~/.grok")


def _claude_sessions_dir() -> str:
    env = os.environ.get("AGENT_BUS_SESSIONS_DIR")
    if env:
        return env
    return os.path.expanduser("~/.claude/sessions")


def detect_kind(env: dict[str, str] | None = None) -> Kind:
    e = env if env is not None else os.environ
    if e.get("GROK_SESSION_ID") or e.get("GROK_HOOK_EVENT"):
        return "grok"
    if e.get("CLAUDE_PLUGIN_ROOT") or e.get("CLAUDE_PROJECT_DIR"):
        return "claude"
    return "other"


def derive_name(kind: str, session_id: str | None, pid: int | None = None) -> str:
    raw = (session_id or "").strip()
    token = re.sub(r"[^A-Za-z0-9_-]", "", raw)[:8]
    if token:
        return f"{kind}-{token}"
    if pid:
        return f"{kind}-{pid}"
    return kind


def _session_id_from_payload(payload: dict[str, Any] | None, env: dict[str, str]) -> str | None:
    if payload:
        sid = payload.get("sessionId") or payload.get("session_id")
        if sid:
            return str(sid)
    return env.get("GROK_SESSION_ID") or None


def host_pid(
    kind: str,
    session_id: str | None = None,
    env: dict[str, str] | None = None,
) -> int | None:
    e = env if env is not None else os.environ
    if session_id is None:
        session_id = e.get("GROK_SESSION_ID")
    if kind == "grok" and session_id:
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
    if kind == "claude" and session_id:
        sdir = _claude_sessions_dir()
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
                    if pid:
                        return int(pid)
        except OSError:
            pass
    ppid = os.getppid()
    return ppid if ppid > 1 else None


def session_start(
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    home: str | None = None,
) -> RosterEntry:
    e = dict(os.environ if env is None else env)
    kind = detect_kind(e)
    sid = _session_id_from_payload(payload, e)
    pid = host_pid(kind, session_id=sid, env=e)
    cwd = e.get("GROK_WORKSPACE_ROOT") or e.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    name = derive_name(kind, sid, pid=pid)
    if kind == "grok" and sid:
        title = _session_title(grok_home(), sid, cwd)
        if title:
            name = title
    return register(name, kind, cwd=cwd, pid=pid, home=home)


def session_end(
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    home: str | None = None,
) -> bool:
    e = dict(os.environ if env is None else env)
    kind = detect_kind(e)
    sid = _session_id_from_payload(payload, e)
    pid = host_pid(kind, session_id=sid, env=e)
    name = derive_name(kind, sid, pid=pid)
    if kind == "grok" and sid:
        title = _session_title(grok_home(), sid, cwd=e.get("GROK_WORKSPACE_ROOT") or os.getcwd())
        if title:
            name = title
    return unregister(name, home=home)
