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
    # daemons clients
    try:
        for cli_json in glob.glob(os.path.join(base, "run", "daemons", "*", "clients", "*.json")):
            try:
                with open(cli_json, encoding="utf-8") as f:
                    data = json.load(f)
                pid = data.get("pid")
                if not is_pid_alive(pid):
                    continue
                cwd = data.get("projectDir") or data.get("cwd")
                if not cwd:
                    continue
                header = titles.get(_encode_project_dir(cwd))

                # Only register sessions that were user-named
                if not header:
                    continue

                aid = data.get("id") or f"pid:{pid}"
                out.append({
                    "id": f"omp:{aid}",
                    "name": header["title"],
                    "kind": "omp",
                    "pid": pid,
                    "cwd": cwd,
                    "status": "unknown",
                    "native": {"id": aid, "projectDir": cwd, "sessionId": header["session_id"]},
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


def _encode_project_dir(path: str) -> str:
    """OMP's own session-directory name for a project path: home-relative,
    every "/" turned into "-". Confirmed against real session data --
    "/Users/dan/Code/AI/labkit" -> "-Code-AI-labkit",
    "/Users/dan/.omp/wt/labkit-assistant-5cbb478" -> "-.omp-wt-labkit-assistant-5cbb478".

    This is the only thing a live daemon client's own record can be turned
    into and matched against: the client carries a `projectDir`, never a
    session id -- session ids live only inside the session files themselves.
    """
    home = os.path.expanduser("~")
    rel = path[len(home):] if path.startswith(home) else path
    return rel.replace("/", "-")


def _session_id_of(session_jsonl: str) -> str:
    """The session id is the name of the session file -- real OMP output
    names it `<timestamp>_<session-id>.jsonl` (the timestamp itself uses
    dashes, never underscores, so splitting on the first "_" isolates it)."""
    stem = os.path.splitext(os.path.basename(session_jsonl))[0]
    return stem.split("_", 1)[-1] if "_" in stem else stem


def get_session_header_rows() -> dict[str, dict[str, str]]:
    """Reads: ~/.omp/agent/sessions/<encoded-project-dir>/*.jsonl

    Returns a dict of encoded-project-dir -> {title, session_id, updated_at}
    for the most recently updated user-assigned title in that directory.
    Keyed by directory rather than session id, because that is the only
    thing a live daemon client record can be matched against -- it carries a
    `projectDir`, never a session id (see `_encode_project_dir`). The
    session id itself still comes along, read off the session file's own
    name, for a caller that wants to key on it once matched (e.g. a
    reconnect signal keyed on `native.sessionId`).
    Does NOT return directories where the newest title was not user-assigned.
    """
    out: dict[str, dict[str, str]] = {}
    base = omp_dir()
    try:
        for session_jsonl in glob.glob(os.path.join(base, "agent", "sessions", "*", "*.jsonl")):
            try:
                with open(session_jsonl, encoding="utf-8") as f:
                    data = json.loads(f.readline(256))
                if data.get("type") != "title":
                    # could be legacy session file format; not handling those
                    continue
                if data.get("source") != "user":
                    # bail if title was not user-assigned
                    continue

                encoded_dir = os.path.basename(os.path.dirname(session_jsonl))
                updated_at = data.get("updatedAt") or ""
                existing = out.get(encoded_dir)
                if existing is not None and existing["updated_at"] >= updated_at:
                    continue
                out[encoded_dir] = {
                    "title": data.get("title"),
                    "session_id": _session_id_of(session_jsonl),
                    "updated_at": updated_at,
                }
            except (ValueError, KeyError, TypeError):
                # One malformed entry, not the whole registry.
                continue
    except (OSError, ValueError, KeyError, TypeError):
        # The harness's registry is gone, not JSON, or has changed shape.
        # A harness we cannot read is one we report nothing for.
        pass
    return out
