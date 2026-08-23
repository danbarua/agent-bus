"""Claude Code adapter: read-only best-effort discovery.

Scans ~/.claude/sessions/<pid>.json (or AGENT_BUS_SESSIONS_DIR override for tests).
Only returns entries where pid is alive. Never writes. Never touches sockets.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from ..protocol import normalize_kind
from ..store import is_pid_alive


def _sessions_dir() -> str:
    # override for tests: AGENT_BUS_SESSIONS_DIR
    env = os.environ.get("AGENT_BUS_SESSIONS_DIR")
    if env:
        return env
    return os.path.expanduser("~/.claude/sessions")


def discover() -> list[dict[str, Any]]:
    """Return list of discovered claude agents (live pids only)."""
    sdir = _sessions_dir()
    out: list[dict[str, Any]] = []
    if not os.path.isdir(sdir):
        return out
    try:
        for fn in os.listdir(sdir):
            if not fn.endswith(".json") or not fn.split(".")[0].isdigit():
                continue
            path = os.path.join(sdir, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pid = int(data.get("pid", 0))
                if not is_pid_alive(pid):
                    continue
                session_id = data.get("sessionId") or f"pid:{pid}"
                is_ab = bool(data.get("agentBus"))
                if is_ab:
                    rid = f"agentbus:{session_id}"
                    nm = data.get("name") or f"agentbus-{pid}"
                    # normalize_kind, not a membership test against Kind:
                    # Kind became a plain str when the enum was opened, so
                    # get_args(Kind) is () and this branch forced *every*
                    # shim-published peer to "other" -- a grok peer was
                    # invisible to `list --kind grok`, in the one view whose
                    # whole job is to unify the harnesses.
                    k = normalize_kind(data.get("agent"))
                else:
                    rid = f"claude:{session_id}"
                    nm = data.get("name") or f"claude-{pid}"
                    k = "claude"
                cwd = data.get("cwd")
                status = data.get("status", "unknown")
                out.append({
                    "id": rid,
                    "name": nm,
                    "kind": k,
                    "pid": pid,
                    "cwd": cwd,
                    "status": status,
                    "native": {
                        "sessionId": session_id,
                        "messagingSocketPath": data.get("messagingSocketPath"),
                        "version": data.get("version"),
                        "kind": data.get("kind"),
                    },
                    "registeredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
            except Exception:
                continue
    except Exception:
        pass
    return out


# --------------------------------------------------------------- lifecycle

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
        return None
    sdir = _sessions_dir()
    try:
        for fn in os.listdir(sdir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(sdir, fn), encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            sid = data.get("sessionId") or data.get("session_id")
            if str(sid or "") == session_id:
                pid = data.get("pid")
                if pid and is_pid_alive(int(pid)):
                    return int(pid)
    except OSError:
        pass
    return None


def session_name(session_id: str | None, cwd: str | None) -> str | None:
    return None


def workspace(env: dict[str, str]) -> str | None:
    return env.get("CLAUDE_PROJECT_DIR")
