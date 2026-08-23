"""Shared by every space whose identity really is a running process.

bus, session and pid differ in where the identifier comes from, not in what
makes it live, so the rule is written once. thread is the space that does not
use this -- which is the entire reason the axis exists.
"""

from __future__ import annotations

from typing import Any

from ...process import is_process_alive


def _get(entry: Any, field: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(field)
    return getattr(entry, field, None)


def is_live(entry: Any) -> bool:
    return is_process_alive(_get(entry, "pid"), _get(entry, "procStart"))
