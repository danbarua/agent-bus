"""Grok / Claude plugin session hooks: register the host agent, not the hook pid."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from typing import Any

from .adapters.grok import _grok_dir as grok_home
from .adapters.grok import _session_title
from .protocol import Kind, RosterEntry
from .store import get_home, is_pid_alive, register, unregister_by_pid


def _epoch_ms() -> int:
    return int(time.time() * 1000)


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


def _patch_published_session(host_pid: int, patch: dict[str, Any], home: str | None = None) -> bool:
    """Merge fields into the session file our listener publishes.

    That file is the only thing a Claude peer reads about us, so anything we
    want ListAgents to show has to be written here -- name, status, cwd. The
    listener writes it once at startup; every later change goes through this.
    """
    if not host_pid or not patch:
        return False
    pid_path = _listener_pid_path(host_pid, home)
    try:
        with open(pid_path, encoding="utf-8") as f:
            listener_pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    sess_path = os.path.join(_claude_sessions_dir(), f"{listener_pid}.json")
    try:
        with open(sess_path, encoding="utf-8") as f:
            data = json.load(f)
        if all(data.get(k) == v for k, v in patch.items()):
            return True
        data.update(patch)
        tmp = sess_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, sess_path)
        return True
    except (OSError, json.JSONDecodeError):
        return False


def publish_status(
    host_pid: int,
    status: str,
    cwd: str | None = None,
    home: str | None = None,
) -> bool:
    """Report what this peer is doing, so a listing shows it.

    We wrote status "idle" once at startup and never again, so a peer read as
    idle in Claude's ListAgents no matter what it was doing. Claude updates
    status on transition and stamps statusUpdatedAt; we do the same, and touch
    updatedAt so staleness is visible even to a reader that ignores status.

    Note what this does NOT do: infer idle/busy on the peer's behalf. Nothing
    here can see an agent thinking between tool calls. The peer reports its own
    state, which is the only version of this that is not a guess.
    """
    now = _epoch_ms()
    patch: dict[str, Any] = {
        "status": status,
        "statusUpdatedAt": now,
        "updatedAt": now,
    }
    if cwd:
        patch["cwd"] = cwd
    return _patch_published_session(host_pid, patch, home=home)


def touch_published_session(host_pid: int, home: str | None = None) -> bool:
    """Bump updatedAt only -- evidence the peer is alive and doing something."""
    return _patch_published_session(host_pid, {"updatedAt": _epoch_ms()}, home=home)


def rename_uds_listen(host_pid: int, new_name: str, home: str | None = None) -> bool:
    """Point the peer's published session at its current name.

    The listener's name is fixed when session_start() runs, before an MCP-only
    peer has had a chance to call register(). Without this the roster says
    "omp-peer" while the socket still advertises "other-<pid>", so the name a
    sender sees is not the name that works.
    """
    if not new_name:
        return False
    return _patch_published_session(host_pid, {"name": new_name}, home=home)


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
