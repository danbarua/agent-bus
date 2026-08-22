"""Grok adapter: read-only."""
from __future__ import annotations

import glob
import json
import os
import time
from typing import Any
from urllib.parse import quote

from ..store import is_pid_alive


def _grok_dir() -> str:
    env = os.environ.get("AGENT_BUS_GROK_DIR")
    if env:
        return env
    return os.path.expanduser("~/.grok")


def _session_title(gdir: str, session_id: str, cwd: str | None) -> str | None:
    """Display title from summary.json (dashboard rename), not agent_name (persona)."""
    paths: list[str] = []
    if cwd:
        paths.append(
            os.path.join(gdir, "sessions", quote(cwd, safe=""), session_id, "summary.json")
        )
    paths.extend(
        glob.glob(os.path.join(gdir, "sessions", "*", session_id, "summary.json"))
    )
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            title = data.get("generated_title")
            if isinstance(title, str) and title.strip():
                return title.strip()
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return None


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
            cwd = s.get("cwd")
            name = (
                _session_title(gdir, str(sid), cwd)
                or s.get("agent_name")
                or f"grok-{pid}"
            )
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
