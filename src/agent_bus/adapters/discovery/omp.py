"""OMP adapter: read-only best effort."""
from __future__ import annotations

import glob
import json
import os
import time
from typing import Any

from ...paths import omp_dir
from ...process import is_pid_alive

KIND = "omp"


def discover() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    base = omp_dir()
    # daemons clients
    try:
        for cli_json in glob.glob(os.path.join(base, "run", "daemons", "*", "clients", "*.json")):
            try:
                with open(cli_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pid = data.get("pid")
                if not is_pid_alive(pid):
                    continue
                aid = data.get("id") or f"pid:{pid}"
                rid = f"omp:{aid}"
                name = data.get("id", f"omp-{pid}")
                cwd = data.get("projectDir") or data.get("cwd")
                out.append({
                    "id": rid,
                    "name": name,
                    "kind": "omp",
                    "pid": pid,
                    "cwd": cwd,
                    "status": "unknown",
                    "native": {"id": aid, "projectDir": cwd},
                    "registeredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
            except Exception:
                continue
    except Exception:
        pass

    # terminal sessions fallback (ttys*)
    try:
        for ts in glob.glob(os.path.join(base, "agent", "terminal-sessions", "ttys*")):
            # these are dirs? assume have info, but for simplicity if dir take name
            pid_str = os.path.basename(ts).replace("ttys", "")
            try:
                pid = int(pid_str) if pid_str.isdigit() else None
            except Exception:
                pid = None
            if pid and is_pid_alive(pid):
                rid = f"omp:tty:{pid}"
                out.append({
                    "id": rid,
                    "name": f"omp-tty-{pid}",
                    "kind": "omp",
                    "pid": pid,
                    "cwd": None,
                    "status": "unknown",
                    "native": {"tty": os.path.basename(ts)},
                    "registeredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
    except Exception:
        pass
    return out
