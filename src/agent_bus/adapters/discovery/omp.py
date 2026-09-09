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
    """Reads live omp sessions from ~/.omp/run/daemons/*/clients/*.json"""
    out: list[dict[str, Any]] = []
    base = omp_dir()
    titles = get_session_header_rows()
    # daemon clients
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
                name:str = titles.get(data.get("id")).get('title')
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

def get_session_header_rows() -> dict[str, dict[str, str]]:
    """Reads: ~/.omp/agent/sessions/*/*.json"""
    """Returns: dictionary of session_id to { title, updatedAt}"""
    out: dict[str, dict[str,str]] = {}
    base = omp_dir()
    try:
        for session_jsonl in glob.glob(os.path.join(base, "agent", "sessions", "*", "*.jsonl")):
            try:
                with open(session_jsonl, encoding="utf-8") as f:
                    first_line = f.readline(256)
                    if not first_line.startswith('{"type": "title"'):
                        continue
                    data = json.loads(first_line)
                if data.get("source") != "user":
                    continue

                title = data.get("title")
                session_id = session_jsonl.split("/")[-1].split(".")[0]  # file name is session id
                updated_at = data.get("updatedAt")
                out[session_id] = {
                    "title": title,
                    "updated_at": updated_at
                }
            except (ValueError, KeyError, TypeError):
                # One malformed entry, not the whole registry.
                continue
    except (OSError, ValueError, KeyError, TypeError):
        # The harness's registry is gone, not JSON, or has changed shape.
        # A harness we cannot read is one we report nothing for.
        pass
    return out
