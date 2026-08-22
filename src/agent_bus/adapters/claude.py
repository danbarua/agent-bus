"""Claude Code adapter: read-only best-effort discovery.

Scans ~/.claude/sessions/<pid>.json (or AGENT_BUS_SESSIONS_DIR override for tests).
Only returns entries where pid is alive. Never writes. Never touches sockets.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, get_args

from ..protocol import Kind
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
                    k = data.get("agent") or "other"
                    if k not in get_args(Kind):
                        k = "other"
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
