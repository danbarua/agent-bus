"""Grok / Claude plugin session hooks: register the host agent, not the hook pid."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from typing import Any

from .adapters.grok import _grok_dir as grok_home
from .adapters.grok import _session_title
from .protocol import Kind, RosterEntry
from .store import get_home, is_pid_alive, register, unregister_by_pid


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
    if e.get("GROK_HOOK_EVENT") or e.get("GROK_PLUGIN_ROOT"):
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
                    # Only trust a live pid. listdir order is arbitrary, so after a
                    # crash a stale <oldpid>.json can match first; registering that
                    # dead pid gets pruned on the next roster read and the session
                    # is invisible on the bus for its whole lifetime.
                    if pid and is_pid_alive(int(pid)):
                        return int(pid)
        except OSError:
            pass
    ppid = os.getppid()
    return ppid if ppid > 1 else None


def _listener_dir(home: str | None = None) -> str:
    d = os.path.join(home or get_home(), "listeners")
    os.makedirs(d, exist_ok=True)
    return d


def _listener_pid_path(host_pid: int, home: str | None = None) -> str:
    return os.path.join(_listener_dir(home), f"{host_pid}.pid")


def start_uds_listen(name: str, host_pid: int, home: str | None = None) -> int | None:
    """Detached Claude-compatible UDS peer for this host pid. Does not change /list-agents."""
    if not host_pid:
        return None
    sess_path = os.path.join(_claude_sessions_dir(), f"{host_pid}.json")
    if os.path.isfile(sess_path):
        try:
            with open(sess_path, encoding="utf-8") as f:
                existing = json.load(f)
            if not existing.get("agentBus"):
                return None
        except (OSError, json.JSONDecodeError):
            return None
    pid_path = _listener_pid_path(host_pid, home)
    if os.path.isfile(pid_path):
        try:
            old = int(open(pid_path, encoding="utf-8").read().strip())
            os.kill(old, 0)
            return old
        except (OSError, ValueError):
            try:
                os.unlink(pid_path)
            except OSError:
                pass
    log_path = os.path.join(_listener_dir(home), f"{host_pid}.log")
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_bus",
                "listen",
                "--name",
                name,
                "--pid",
                str(host_pid),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
    with open(pid_path, "w", encoding="utf-8") as f:
        f.write(str(proc.pid) + "\n")
    return proc.pid


def stop_uds_listen(host_pid: int, home: str | None = None) -> bool:
    if not host_pid:
        return False
    pid_path = _listener_pid_path(host_pid, home)
    if not os.path.isfile(pid_path):
        return False
    try:
        daemon_pid = int(open(pid_path, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(daemon_pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        os.unlink(pid_path)
    except OSError:
        pass
    return True


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
    entry = register(name, kind, cwd=cwd, pid=pid, home=home)
    # Every non-Claude peer needs the shim listener to appear in Claude's native
    # ListAgents and to receive native SendMessage. Claude sessions already have
    # their own socket, so they are the only kind that must not get one.
    if kind != "claude" and pid:
        try:
            start_uds_listen(entry.name, pid, home=home)
        except OSError:
            pass
    return entry


def session_end(
    payload: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    home: str | None = None,
) -> bool:
    e = dict(os.environ if env is None else env)
    kind = detect_kind(e)
    sid = _session_id_from_payload(payload, e)
    pid = host_pid(kind, session_id=sid, env=e)
    # Mirror session_start: it starts a listener for every non-claude kind, so
    # stopping only grok's would leak a listener process per omp/codex session.
    if kind != "claude" and pid:
        stop_uds_listen(pid, home=home)
    return unregister_by_pid(pid, home=home)
