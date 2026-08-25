"""A record of what agents actually asked this server to do.

Nothing observed MCP tool calls. `captures/` records UDS frames, which is the
other half of the traffic, so the half that agents drive was invisible: the
tool descriptions and two response shapes were changed without any way to tell
whether a harness had stopped calling something, or had never started.

The failure this exists for is silence. A client that gives up mid-handshake,
or caches a failed discovery and stops retrying, produces *no* traffic -- and
"it is quiet because nothing is wrong" looks exactly like "it is quiet because
it broke". Only a log of what did arrive tells them apart, which is why
successful calls are recorded and not just errors.

Three rules it holds to:

**Shapes, not payloads.** Argument names and the length of any text, never the
text. Message bodies are the thing being carried; copying them here would
duplicate every inbox into a file with a different lifetime and no TTL.

**Never breaks the caller.** Every write is best-effort. A server that fell over
because it could not write its own diagnostics would be worse than one with no
diagnostics.

**On by default.** A switch that has to be thrown first is off at the moment it
is needed, which is the only moment it matters.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .paths import get_home

# Per pid, because an MCP server is a child of whichever harness started it and
# several run at once. One file per process keeps their stories separate.
DIR_NAME = "mcp-calls"

# A cap rather than rotation. Long-lived servers would otherwise grow without
# limit, and losing the newest lines is worse than losing nothing, so writing
# stops with a marker instead of discarding history silently.
MAX_BYTES = 2 * 1024 * 1024

# Argument values that are content rather than addressing. Their length is
# recorded; their contents are not.
CONTENT_KEYS = frozenset({"text", "summary"})


def log_path(pid: int | None = None, home: str | None = None) -> str:
    h = home or get_home()
    return os.path.join(h, DIR_NAME, f"{pid or os.getpid()}.jsonl")


def describe_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """What was passed, without what was said.

    `to`, `name`, `kind` and the like are addressing and are recorded as given
    -- they are what you need to reconstruct a call. `text` and `summary` are
    the message itself, so only their size is kept.
    """
    if not isinstance(args, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k in CONTENT_KEYS:
            out[f"{k}_len"] = len(v) if isinstance(v, str) else None
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = type(v).__name__
    return out


def record(entry: dict[str, Any], home: str | None = None) -> None:
    """Append one line. Silent on failure, by design."""
    try:
        path = log_path(home=home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) >= MAX_BYTES:
                return
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def read(pid: int | None = None, home: str | None = None) -> list[dict[str, Any]]:
    """Everything this process recorded. For tests and for reading it back."""
    out: list[dict[str, Any]] = []
    try:
        with open(log_path(pid, home), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return []
    return out
