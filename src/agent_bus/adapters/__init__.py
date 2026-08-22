"""Adapters package: read-only discovery for native agent registries."""
from __future__ import annotations

from typing import Any

from . import claude, codex, grok, omp


def discover_all() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mod in (claude, grok, omp, codex):
        try:
            out.extend(mod.discover())
        except Exception:
            # never throw from adapters
            continue
    return out
