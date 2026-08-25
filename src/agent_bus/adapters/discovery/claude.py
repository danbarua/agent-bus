"""Claude Code adapter: read-only best-effort discovery.

Scans ~/.claude/sessions/<pid>.json (or AGENT_BUS_SESSIONS_DIR override for tests).
Only returns entries where pid is alive. Never writes. Never touches sockets.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from ...paths import claude_sessions_dir as _sessions_dir
from ...process import is_pid_alive
from ...protocol import normalize_kind

KIND = "claude"


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
                with open(path, encoding="utf-8") as f:
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
