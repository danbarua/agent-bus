"""Harness adapters, split by capability rather than by vendor.

See contracts.py for why, and docs/harness-compatibility.md for the matrix
this tree mirrors.
"""

from __future__ import annotations

from . import discovery, lifecycle, transport
from .contracts import Discovery, HarnessLifecycle, Transport
from .discovery import discover_all

__all__ = [
    "Discovery",
    "HarnessLifecycle",
    "Transport",
    "discover_all",
    "discovery",
    "lifecycle",
    "transport",
]
