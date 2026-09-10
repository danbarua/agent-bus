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
                # `projectDir` is the field the real capture this was
                # verified against actually has. The `cwd` fallback is
                # unverified -- see #332.
                cwd = data.get("projectDir") or data.get("cwd")
                if not cwd:
                    continue
                encoded_dir = _encode_project_dir(cwd)
                by_dir.setdefault(encoded_dir, []).append(data | {"cwd": cwd, "pid": pid})
            except (ValueError, KeyError, TypeError, AttributeError, OSError):
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
        # _encode_project_dir is not injective, so one process holding
        # connections in two genuinely different (colliding) directories is
        # possible even with a single pid. `cwd` is the raw, unencoded
        # value, so a disagreement here is that collision. (The case where
        # each colliding project has its own single client is #332.)
        cwds = {c["cwd"] for c in clients}
        if len(cwds) > 1:
            continue
        header = titles.get(encoded_dir)
        # Only register sessions that were user-named (get_session_header_rows
        # only ever returns user-titled headers).
        if not header:
            continue

        # Any of `clients` will do here: pid and cwd are now confirmed
        # identical across every record in the group. Only a per-connection
        # id would differ between them, and that value earns nothing worth
        # carrying -- see `native` below.
        client = clients[0]
        pid = client["pid"]
        cwd = client["cwd"]
        session_id = header["session_id"]
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
            # No connection id here either, for the same reason: it is
            # per-connection and would be exactly as unstable in `native`
            # as it would be in the row's own id, and send_message would
            # freeze whichever one won into a persisted entry forever.
            # `sessionId` and `projectDir` already carry everything a
            # caller needs.
            "native": {"projectDir": cwd, "sessionId": session_id},
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
    "-Code-agent-bus". `discover()` guards the multi-client-per-pid shape of
    that collision; the single-client shape is #332.
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
    recently modified session in the directory, if and only if it is also
    the most recently modified *user-titled* one there. Keyed by directory
    rather than session id, because that is the only thing a live daemon
    client record can be matched against (see `_encode_project_dir`).

    mtime, not the session file's own `updatedAt` field, decides "most
    recent": `updatedAt` is caller-supplied, not guaranteed present,
    numeric, or zero-padded, while mtime is a filesystem fact. Two files
    within the same second of each other are an ambiguity mtime can't
    resolve -- treated the same whether the tie is between two titled
    files, or a titled one and an untitled one -- so the directory is
    dropped rather than guessed at.

    See #332 for the open questions this doesn't attempt to answer (a dead
    session's file racing a live one's, a directory-encoding collision
    between two single-client projects, an in-place rename, the `$HOME` and
    `cwd`-fallback edge cases).
    """
    files: dict[str, list[tuple[float, dict[str, str] | None]]] = {}
    base = omp_dir()
    try:
        for session_jsonl in glob.glob(os.path.join(base, "agent", "sessions", "*", "*.jsonl")):
            try:
                mtime = os.path.getmtime(session_jsonl)
                # A truncated first line is neither a usable title nor
                # evidence of an untitled session, so a tighter bound would
                # let an overlong title silently drop its own directory.
                # Still bounded, so this never reads an unbounded line.
                with open(session_jsonl, encoding="utf-8") as f:
                    data = json.loads(f.readline(4096))
                header: dict[str, str] | None = None
                if (data.get("type") == "title" and data.get("source") == "user"
                        and data.get("title")):
                    header = {
                        "title": data["title"],
                        "session_id": _session_id_of(session_jsonl),
                    }
            except (ValueError, KeyError, TypeError, AttributeError, OSError):
                # Unreadable, unparseable (including valid JSON that isn't
                # an object -- "[]", "null", a bare string), or a directory
                # glob matched literally as "*.jsonl" -- not evidence of
                # anything, so excluded entirely rather than counted as an
                # untitled session (which would let it veto a real title
                # below).
                continue
            encoded_dir = os.path.basename(os.path.dirname(session_jsonl))
            files.setdefault(encoded_dir, []).append((mtime, header))
    except (OSError, ValueError, KeyError, TypeError):
        # The harness's registry is gone, not JSON, or has changed shape.
        # A harness we cannot read is one we report nothing for.
        pass

    out: dict[str, dict[str, str]] = {}
    for encoded_dir, rows in files.items():
        titled = sorted(
            ((mtime, header) for mtime, header in rows if header is not None),
            key=lambda r: r[0], reverse=True,
        )
        if not titled:
            continue
        newest_titled_mtime, newest_header = titled[0]
        # Ambiguous, either way: another titled file in the same second, or
        # an untitled one at least as new. "At least as new" (not "newer"),
        # so a same-second tie against an untitled file is ambiguous too,
        # the same call a same-second tie between two titled files gets.
        if len(titled) > 1 and int(newest_titled_mtime) == int(titled[1][0]):
            continue
        if any(int(mtime) >= int(newest_titled_mtime)
               for mtime, header in rows if header is None):
            continue
        out[encoded_dir] = newest_header
    return out
