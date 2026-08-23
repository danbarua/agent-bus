"""The Claude-shaped listener a peer publishes, and its session file.

Moved out of the old plugin_host.py, which mixed lifecycle with this. These
functions are transport concerns, not lifecycle: they write into Claude Code's
discovery surface (~/.claude/sessions) and manage the detached listener process
that makes a non-Claude peer visible to it.

Kept as one module rather than folded into a transport abstraction. There are
now two live transports -- this and the Codex client -- but three of the four
capability flags in docs/transport-seam.md are constant under Claude alone, so
extracting an interface still risks designing around a sample of one.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import Any

from .paths import claude_sessions_dir
from .store import get_home


def _epoch_ms() -> int:
    return int(time.time() * 1000)


def _claude_sessions_dir() -> str:
    """Kept as a name because callers import it; the logic lives in paths.py."""
    return claude_sessions_dir()


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
    # The listener is a separate process and registers itself, so it has to be
    # told which bus it belongs to. Passing `home` here but not to the child
    # meant a caller that set it by argument -- rather than by env -- got a
    # listener that quietly registered in the *default* home instead. Under
    # test that wrote real entries into the developer's own ~/.agent-bus.
    child_env = os.environ.copy()
    if home:
        child_env["AGENT_BUS_HOME"] = home
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
            env=child_env,
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
