"""Grok adapter: read-only."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from ..store import is_pid_alive


def _grok_dir() -> str:
    return os.path.expanduser("~/.grok")


def discover() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    gdir = _grok_dir()
    active = os.path.join(gdir, "active_sessions.json")
    if not os.path.isfile(active):
        return out
    try:
        with open(active, "r", encoding="utf-8") as f:
            sessions = json.load(f)
        if not isinstance(sessions, list):
            return out
        for s in sessions:
            pid = s.get("pid")
            if not is_pid_alive(pid):
                continue
            sid = s.get("session_id") or f"pid:{pid}"
            rid = f"grok:{sid}"
            name = s.get("agent_name") or f"grok-{pid}"
            cwd = s.get("cwd")
            out.append({
                "id": rid,
                "name": name,
                "kind": "grok",
                "pid": pid,
                "cwd": cwd,
                "status": "unknown",
                "native": {"session_id": sid, "opened_at": s.get("opened_at")},
                "registeredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
    except Exception:
        pass
    return out
