"""Codex adapter: read-only, best effort, silent skip on catalog."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from ...paths import codex_dir
from ...process import is_pid_alive

KIND = "codex"


def discover() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    base = codex_dir()
    pm = os.path.join(base, "process_manager", "chat_processes.json")
    if not os.path.isfile(pm):
        return out
    try:
        with open(pm, "r", encoding="utf-8") as f:
            procs = json.load(f)
        # expected shape unknown, try list or dict of pids
        items = procs if isinstance(procs, list) else (procs.get("processes", []) if isinstance(procs, dict) else [])
        for item in items if isinstance(items, list) else []:
            pid = None
            if isinstance(item, int):
                pid = item
            elif isinstance(item, dict):
                pid = item.get("pid") or item.get("process_id")
            if not is_pid_alive(pid):
                continue
            rid = f"codex:pid:{pid}"
            name = f"codex-{pid}"
            cwd = item.get("cwd") if isinstance(item, dict) else None
            out.append({
                "id": rid,
                "name": name,
                "kind": "codex",
                "pid": pid,
                "cwd": cwd,
                "status": "unknown",
                "native": {"source": "chat_processes"},
                "registeredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
    except Exception:
        # silent on error or empty
        pass
    return out
