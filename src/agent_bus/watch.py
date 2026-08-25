"""Follow an inbox and emit one line per inbound message.

This is the wake source for a harness that has a watch mechanism but nothing to
watch. Grok's `monitor` tool runs a shell command and turns **each stdout line
into a conversation event**, so a peer starts

    monitor(command="agent-bus watch --name me", persistent=true)

once at session start and inbound traffic arrives as events. Claude needs none
of this -- its harness delivers peer messages into the conversation on its own.

Why a command rather than `tail -f` on the inbox file: the JSONL path, the
id-based filename and the record shape are implementation details. Welding them
into every peer's prompt means any change to storage breaks every running
monitor.

The output shape is dictated by the monitor tool's limits, not by taste
(docs/harnesses/grok-build-monitor-reference.md):

- a token bucket of 10 refilling one per 2s, so sustained output above roughly
  0.5 lines/s is suppressed
- 30s of continuous suppression auto-kills the watch outright
- 500 chars per line, 3000 per batch
- exit ends the watch

Hence: one compact line per message, bounded width, and -- the one that bites --
**start from now**. Replaying an existing backlog on startup is the fastest way
to trip the limiter and be killed within the first second. History is what
`agent-bus inbox` is for.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any, TextIO

from .store import _entry_for_current_process, _inbox_path_for, find_entry

# Well inside the monitor tool's 500-char line limit, leaving room for the
# sender and id to survive truncation of the summary.
MAX_LINE = 240
MAX_SUMMARY = 120
POLL_SECONDS = 1.0


def _resolve_inbox(name: str | None, home: str | None) -> tuple[str, str] | None:
    """Return (entry_id, inbox_path), or None if we cannot tell who we are."""
    entry = find_entry(name, home) if name else _entry_for_current_process(home)
    if entry is None:
        return None
    return entry.id, _inbox_path_for(entry.id, home)


def format_event(msg: dict[str, Any]) -> str:
    """One line, compact, bounded.

    Carries who it is from and the message id, because those are what a peer
    needs to fetch the body and address a reply. The text itself is summarised,
    never included whole -- a message body would blow the line limit and tell
    the peer nothing it cannot get from get_inbox.
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
    for line in consumed.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records, offset + len(consumed.encode("utf-8"))


def watch(
    name: str | None = None,
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
    target = _resolve_inbox(name, home)
    if target is None:
        print(
            f"[agent-bus] cannot resolve inbox for {name or 'this process'}",
            file=sys.stderr,
        )
        return 1
    _, path = target

    # Start from the end unless asked otherwise. Replaying a backlog is what
    # gets a monitor rate-limited to death in its first second.
    offset = 0
    if not from_start:
        try:
            offset = os.path.getsize(path)
        except OSError:
            offset = 0

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
        time.sleep(poll_seconds)
