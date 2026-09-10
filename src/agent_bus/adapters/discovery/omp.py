"""OMP adapter: read-only best effort."""
from __future__ import annotations

import glob
import json
import os
import re
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

    Not injective -- this is OMP's own scheme, not a choice made here:
    "/home/dan/Code/agent-bus" and "/home/dan/Code/agent/bus" both encode to
    "-Code-agent-bus". `discover()`'s per-directory pid count happens to
    make that safe (two live pids landing in one bucket already means
    "skip"), but that is incidental, not a guarantee this function makes.
    """
    home = os.path.expanduser("~")
    if path == home:
        rel = ""
    elif path.startswith(home + os.sep):
        rel = path[len(home):]
    else:
        rel = path
    return rel.replace("/", "-")


_TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z_")


def _session_id_of(session_jsonl: str) -> str:
    """The session id is the name of the session file -- real OMP output
    names it `<timestamp>_<session-id>.jsonl`. Splitting on the first "_"
    isn't enough: the timestamp itself never contains one, but a session id
    can ("...T08_overlap_bench...") and then the first "_" falls inside the
    id, not after the timestamp. Matching the timestamp's own fixed shape
    (it always ends "...NNNZ_") finds the real boundary; anything that
    doesn't match it is returned whole, on the same footing as a stem with
    no underscore at all -- a value this code has never seen the shape of
    should not be truncated on a guess.
    """
    stem = os.path.splitext(os.path.basename(session_jsonl))[0]
    m = _TIMESTAMP_PREFIX.match(stem)
    return stem[m.end():] if m else stem


def get_session_header_rows() -> dict[str, dict[str, str]]:
    """Reads: ~/.omp/agent/sessions/<encoded-project-dir>/*.jsonl

    Returns a dict of encoded-project-dir -> {title, session_id}: the most
    recently modified user-titled session in each directory. Keyed by
    directory rather than session id, because that is the only thing a live
    daemon client record can be matched against -- it carries a
    `projectDir`, never a session id (see `_encode_project_dir`).

    That directory accumulates one file per session ever run in the
    project, not just the live one, so "pick the single candidate" would
    turn discovery off for good the second time anyone titled a session
    there. mtime is used to pick the newest rather than the session file's
    own `updatedAt` field: `updatedAt` is caller-supplied, not guaranteed
    present, numeric, or zero-padded, while mtime is a filesystem fact and
    tracks the transcript actually being appended to right now. Two
    candidates within the same second are still a real ambiguity mtime
    can't resolve -- silently attaching the wrong title is worse than
    surfacing none, so that directory is dropped rather than guessed at.

    Unverified against a real rename: if OMP appends a second `type: title`
    record to the *same* file rather than starting a new one, this still
    reports the original title, since only the first line is ever read.
    """
    candidates: dict[str, list[dict[str, Any]]] = {}
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
                    "mtime": os.path.getmtime(session_jsonl),
                })
            except (ValueError, KeyError, TypeError, OSError):
                # One malformed entry, not the whole registry.
                continue
    except (OSError, ValueError, KeyError, TypeError):
        # The harness's registry is gone, not JSON, or has changed shape.
        # A harness we cannot read is one we report nothing for.
        pass

    out: dict[str, dict[str, str]] = {}
    for encoded_dir, rows in candidates.items():
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        if len(rows) > 1 and rows[0]["mtime"] == rows[1]["mtime"]:
            continue
        newest = rows[0]
        out[encoded_dir] = {"title": newest["title"], "session_id": newest["session_id"]}
    return out
