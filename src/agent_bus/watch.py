"""Follow an inbox and emit one line per inbound message.

The wake source for a harness whose watch mechanism has nothing to watch: a
peer runs this under its own monitor tool and inbound traffic arrives as
conversation events. Claude needs none of it — its harness delivers peer
messages into the conversation itself.

The line says who and what about, and it starts from the end of the inbox: a
peer arming a watch has already handled its history via `inbox`/`get_inbox`.
The body of what watch reports is fetched by the id the line gives, with
`read` (CLI) or `read_message` (MCP). What each harness's monitor does with
the line is in docs/harnesses/<harness>.md.

Why a command rather than `tail -f` on the inbox file: the JSONL path, the
id-based filename and the record shape are implementation details. Welding
them into every peer's prompt breaks every running monitor on any change to
storage.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any, TextIO

from . import log
from .protocol import AgentTarget
from .store import (
    MESSAGE_TTL_SECONDS,
    _entry_for_current_process,
    _inbox_path_for,
    compact_inbox,
    find_entry,
)

# How often a running watcher enforces the TTL on its own inbox. Deliberately
# coarse: get_inbox already filters expired messages on every read, so this is
# housekeeping and precision buys nothing. Frequent enough that a long-lived
# watcher does not let the file grow without bound.
COMPACT_EVERY_SECONDS = MESSAGE_TTL_SECONDS / 4

# Wide enough to carry sender, id and a useful summary; short enough that no
# consumer has to truncate it.
MAX_LINE = 240
MAX_SUMMARY = 120
POLL_SECONDS = 1.0


def _resolve_inbox(target: AgentTarget | None, home: str | None) -> tuple[str, str] | None:
    """Return (entry_id, inbox_path), or None if we cannot tell who we are."""
    entry = find_entry(target, home) if target else _entry_for_current_process(home)
    if entry is None:
        return None
    return entry.id, _inbox_path_for(entry.id, home)


def format_event(msg: dict[str, Any]) -> str:
    """One line, compact, bounded.

    Carries who it is from and the message id, because those are what a peer
    needs to fetch the body and address a reply. The text itself is summarised,
    never included whole -- the body is what `read` (CLI) or `read_message`
    (MCP) is for, fetched by the id this line just gave.
    """
    sender = (msg.get("from") or {}).get("name") or "unknown"
    mid = str(msg.get("id") or "")[:8]
    summary = (msg.get("summary") or msg.get("text") or "").strip()
    summary = " ".join(summary.split())
    if len(summary) > MAX_SUMMARY:
        summary = summary[: MAX_SUMMARY - 1] + "…"
    line = f"[agent-bus] from={sender} id={mid} summary={summary}"
    return line[:MAX_LINE]


def _read_records(path: str, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read whole JSONL records from offset. A partial trailing line is left."""
    # The file can shrink under us: watch compacts its own inbox at the TTL, and
    # `reap` may collect at 2x TTL while we are running. Seeking past a shrunken
    # file returns nothing forever -- the offset never advances and the watcher
    # goes silent for good. Standard tail-follow behaviour: notice, restart.
    try:
        if os.path.getsize(path) < offset:
            offset = 0
    except OSError:
        return [], offset

    try:
        with open(path, encoding="utf-8") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset

    if not data:
        return [], offset

    # Only consume up to the last newline; a writer may be mid-record.
    cut = data.rfind("\n")
    if cut == -1:
        return [], offset
    consumed = data[: cut + 1]
    records = []
    for raw in consumed.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records, offset + len(consumed.encode("utf-8"))


def watch(
    target: AgentTarget | None = None,
    *,
    home: str | None = None,
    from_start: bool = False,
    once: bool = False,
    out: TextIO | None = None,
    poll_seconds: float = POLL_SECONDS,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Follow an inbox, printing one line per message. Blocks until stopped.

    `once` drains what is already pending and returns, which is what the tests
    use; a real watch runs until the process is killed, because exiting would
    end the monitor that is watching it.
    """
    stream = out or sys.stdout
    resolved = _resolve_inbox(target, home)
    if resolved is None:
        # Kept as a print, unlike mcp_server.py's daemon-only diagnostics
        # (#197): `agent-bus watch` is also run directly by a person, who
        # needs this failure on their own terminal, not only in a log file
        # they may not be tailing. `log.warn` is additive here, not a
        # replacement.
        print(
            f"[agent-bus] cannot resolve inbox for {target or 'this process'}",
            file=sys.stderr,
        )
        log.warn("cannot resolve inbox", target=target)
        return 1
    _, path = resolved

    # Start from the end unless asked otherwise: a peer arming a watch has
    # already handled whatever is in the file.
    offset = 0
    if not from_start:
        try:
            offset = os.path.getsize(path)
        except OSError:
            offset = 0

    last_compact = time.monotonic()
    while True:
        records, offset = _read_records(path, offset)
        for msg in records:
            stream.write(format_event(msg) + "\n")
            # Line-buffered by contract: a monitor that sees output in clumps
            # is indistinguishable from one seeing nothing.
            stream.flush()
        if once:
            return 0
        if should_stop is not None and should_stop():
            return 0

        # Enforce the TTL while we are running. We hold the only offset over this
        # file, so compacting here and correcting the offset in the same breath
        # leaves no window in which a stale one exists -- which is why this lives
        # in the watcher and not in send_message. Everything still in the file
        # after a compaction has already been emitted, so the new end of file is
        # the correct place to resume.
        now = time.monotonic()
        if now - last_compact >= COMPACT_EVERY_SECONDS:
            last_compact = now
            try:
                if compact_inbox(path):
                    offset = os.path.getsize(path)
            except OSError:
                pass

        time.sleep(poll_seconds)
