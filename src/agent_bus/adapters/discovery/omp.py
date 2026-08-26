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
                with open(cli_json, encoding="utf-8") as f:
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
            except (ValueError, KeyError, TypeError):
                # One malformed entry, not the whole registry.
                continue
    except (OSError, ValueError, KeyError, TypeError):
        # The harness's registry is gone, not JSON, or has changed shape.
        # A harness we cannot read is one we report nothing for.
        pass
    return out
