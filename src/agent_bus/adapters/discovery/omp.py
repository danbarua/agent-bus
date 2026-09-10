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
                # unverified -- if a client record ever carries only that,
                # it may be a different fact (a live working directory, not
                # the project root) run through a projectDir-shaped
                # encoding, and correlate wrongly rather than not at all.
                cwd = data.get("projectDir") or data.get("cwd")
                if not cwd:
                    continue
                encoded_dir = _encode_project_dir(cwd)
                by_dir.setdefault(encoded_dir, []).append(data | {"cwd": cwd, "pid": pid})
            except (ValueError, KeyError, TypeError, OSError):
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
        # The encoding is not injective (see _encode_project_dir), so two
        # genuinely different projects can land in one bucket even with a
        # single pid -- one process with connections opened from two
        # colliding directories. `cwd` is the raw, unencoded value, so a
        # disagreement here is that collision, not a client-record quirk.
        # The encoding is not injective (see _encode_project_dir), so two
        # genuinely different projects can land in one bucket even with a
        # single pid -- one process with connections opened from two
        # colliding directories. `cwd` is the raw, unencoded value, so a
        # disagreement here is that collision, not a client-record quirk.
        cwds = {c["cwd"] for c in clients}
        if len(cwds) > 1:
            continue
        header = titles.get(encoded_dir)
        # Only register sessions that were user-named
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
    "-Code-agent-bus". `discover()` guards against this itself (a raw `cwd`
    disagreement within one encoded bucket is skipped), rather than relying
    on this function to be collision-free.

    Unverified: a project run in `$HOME` itself encodes to `""` here, which
    cannot match any real directory name (`os.path.basename` of a session
    directory is never empty) -- so such a session, whatever OMP actually
    calls its directory, silently never correlates. No sample of that case
    exists in the capture this was verified against.
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

    Returns a dict of encoded-project-dir -> {title, session_id} for a
    directory whose most recently modified session -- titled or not -- is
    the user-titled one. Keyed by directory rather than session id, because
    that is the only thing a live daemon client record can be matched
    against -- it carries a `projectDir`, never a session id (see
    `_encode_project_dir`).

    That directory accumulates one file per session ever run in the
    project, not just the live one, so two things can go wrong, and both
    are checked here rather than just picking a title and hoping:

    - **Which title, if the live session is titled at all.** mtime picks
      the newest rather than the session file's own `updatedAt` field:
      `updatedAt` is caller-supplied, not guaranteed present, numeric, or
      zero-padded, while mtime is a filesystem fact and tracks the
      transcript actually being appended to right now.
    - **Whether the live session is titled at all.** Titling is the
      consent signal (`source == "user"`) that gates a name being
      surfaced; a live-but-untitled session must not silently wear an
      older, dead session's title just because it's the only candidate.
      So the newest *titled* file only counts if no untitled file in the
      directory is at least as new -- otherwise something more recent (and
      untitled, or too close in time to tell) is a real candidate for
      being the one actually running, and the directory is skipped, same
      as if it had never been titled.

    Both checks compare mtimes to the whole second (`int(mtime)`), not
    exactly: two files written in the same second are a real ambiguity no
    finer comparison resolves either way, and exact-float equality would
    almost never fire on a modern filesystem's nanosecond resolution while
    a same-second write is common (a restore, a `cp -p`). Silently
    attaching the wrong title, or a title at all, is worse than surfacing
    none, so an ambiguous directory is dropped rather than guessed at --
    consistently: a same-second tie is treated as ambiguous whether it's
    two titled files or a titled one and an untitled one.

    Known gap, not fixed here: mtime alone cannot tell a newer *dead*
    session's file from a newer *live* one, so a dead session that ran
    after a live, idle, titled session's last write can still suppress the
    live one's title until it next writes to its own transcript -- which
    it cannot do while undiscoverable. Distinguishing them would need a
    floor dated to the live connection (e.g. the daemon client record's
    own mtime), unverified against real data here.

    Unverified against a real rename: if OMP appends a second `type: title`
    record to the *same* file rather than starting a new one, this still
    reports the original title, since only the first line is ever read.
    """
    files: dict[str, list[tuple[float, dict[str, str] | None]]] = {}
    base = omp_dir()
    try:
        for session_jsonl in glob.glob(os.path.join(base, "agent", "sessions", "*", "*.jsonl")):
            try:
                mtime = os.path.getmtime(session_jsonl)
            except OSError:
                continue
            header: dict[str, str] | None = None
            try:
                with open(session_jsonl, encoding="utf-8") as f:
                    data = json.loads(f.readline(256))
                if (data.get("type") == "title" and data.get("source") == "user"
                        and data.get("title")):
                    header = {
                        "title": data["title"],
                        "session_id": _session_id_of(session_jsonl),
                    }
            except (ValueError, KeyError, TypeError, OSError):
                # Not a title record we recognise (or the file vanished, or
                # is a directory glob matched literally as "*.jsonl") -- it
                # still counts toward "what's the newest file in this
                # directory", just untitled. One bad file must not abort
                # the scan: an IsADirectoryError or PermissionError here
                # used to escape to the outer handler and silently truncate
                # every directory glob hadn't reached yet.
                pass
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
        if len(titled) > 1 and int(titled[0][0]) == int(titled[1][0]):
            continue
        if any(int(mtime) >= int(newest_titled_mtime)
               for mtime, header in rows if header is None):
            continue
        out[encoded_dir] = newest_header
    return out
