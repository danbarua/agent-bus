"""Harness adapters, split by capability rather than by vendor.

See contracts.py for why, and docs/harness-compatibility.md for the matrix
this tree mirrors.
"""

from __future__ import annotations

from . import addressing, discovery, lifecycle, transport
from .contracts import AddressSpace, Discovery, HarnessLifecycle, Transport
from .discovery import discover_all

__all__ = [
    "AddressSpace",
    "Discovery",
    "HarnessLifecycle",
    "Transport",
    "addressing",
    "discover_all",
    "discovery",
    "lifecycle",
    "transport",
]
