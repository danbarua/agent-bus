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
    by_dir: dict[str, list[dict[str, Any]]] = {}
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
                encoded_dir = _encode_project_dir(cwd)
                by_dir.setdefault(encoded_dir, []).append(data | {"cwd": cwd, "pid": pid})
            except (ValueError, KeyError, TypeError):
                # One malformed entry, not the whole registry.
                continue
    except (OSError, ValueError, KeyError, TypeError):
        # The harness's registry is gone, not JSON, or has changed shape.
        # A harness we cannot read is one we report nothing for.
        pass

    for encoded_dir, clients in by_dir.items():
        # A client record carries no session id, only the project directory
        # it was launched in. Two records for the *same* pid are one omp
        # process holding two daemon connections -- unambiguous, one row.
        # Two different live pids in one project genuinely are
        # indistinguishable from here: rather than hand both the same name,
        # skip the whole directory -- wrong silence beats a wrong or
        # duplicate name.
        pids = {c["pid"] for c in clients}
        if len(pids) > 1:
            continue
        header = titles.get(encoded_dir)
        # Only register sessions that were user-named
        if not header or not header["title"]:
            continue

        client = clients[0]
        pid = client["pid"]
        cwd = client["cwd"]
        session_id = header["session_id"]
        conn_id = client.get("id") or f"pid:{pid}"
        out.append({
            # Keyed on the session id, not the daemon connection id: the
            # latter changes on every reconnect (a fresh id per connection),
            # while the session id is the one thing that stays stable across
            # one -- see _session_id_of. discover_agents() derives a
            # discovered-only entry's inbox path from this id, so a
            # per-connection value here would mint a second inbox, with the
            # first one's unread mail stranded behind it, on every reconnect.
            "id": f"omp:{session_id}",
            "name": header["title"],
            "kind": "omp",
            "pid": pid,
            "cwd": cwd,
            "status": "unknown",
            "native": {"id": conn_id, "projectDir": cwd, "sessionId": session_id},
            "registeredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
    return out


def _encode_project_dir(path: str) -> str:
    """OMP's own session-directory name for a project path: home-relative,
    every "/" turned into "-". Confirmed against real session data --
    "/Users/dan/Code/AI/labkit" -> "-Code-AI-labkit",
    "/Users/dan/.omp/wt/labkit-assistant-5cbb478" -> "-.omp-wt-labkit-assistant-5cbb478".

    This is the only thing a live daemon client's own record can be turned
    into and matched against: the client carries a `projectDir`, never a
    session id -- session ids live only inside the session files themselves.

    The home prefix must end at a path separator, not just a common prefix:
    "/home/user2/proj" is not under home "/home/user" even though the raw
    string starts with it.
    """
    home = os.path.expanduser("~")
    if path == home:
        rel = ""
    elif path.startswith(home + os.sep):
        rel = path[len(home):]
    else:
        rel = path
    return rel.replace("/", "-")


def _session_id_of(session_jsonl: str) -> str:
    """The session id is the name of the session file -- real OMP output
    names it `<timestamp>_<session-id>.jsonl` (the timestamp itself uses
    dashes, never underscores, so splitting on the first "_" isolates it)."""
    stem = os.path.splitext(os.path.basename(session_jsonl))[0]
    return stem.split("_", 1)[-1] if "_" in stem else stem


def get_session_header_rows() -> dict[str, dict[str, str]]:
    """Reads: ~/.omp/agent/sessions/<encoded-project-dir>/*.jsonl

    Returns a dict of encoded-project-dir -> {title, session_id}, one entry
    per directory that has been user-titled **exactly once**. Keyed by
    directory rather than session id, because that is the only thing a live
    daemon client record can be matched against -- it carries a
    `projectDir`, never a session id (see `_encode_project_dir`).

    A directory holding more than one user-assigned title is ambiguous: no
    field here reliably says which is newest (`updatedAt` is caller-supplied,
    not guaranteed present, numeric, or zero-padded). Silently attaching the
    wrong title to a live client is worse than surfacing none, so such a
    directory is dropped entirely rather than guessed at.
    """
    candidates: dict[str, list[dict[str, str]]] = {}
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
                if not data.get("title"):
                    continue

                encoded_dir = os.path.basename(os.path.dirname(session_jsonl))
                candidates.setdefault(encoded_dir, []).append({
                    "title": data["title"],
                    "session_id": _session_id_of(session_jsonl),
                })
            except (ValueError, KeyError, TypeError):
                # One malformed entry, not the whole registry.
                continue
    except (OSError, ValueError, KeyError, TypeError):
        # The harness's registry is gone, not JSON, or has changed shape.
        # A harness we cannot read is one we report nothing for.
        pass
    return {d: rows[0] for d, rows in candidates.items() if len(rows) == 1}
