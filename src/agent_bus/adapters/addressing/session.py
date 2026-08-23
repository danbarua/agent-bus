"""`session` -- a harness's own session id: `claude:<sid>`, `grok:<sid>`, `omp:<id>`.

Every one of these is backed by a live process in its harness's registry, so
liveness is that process.

The exception is the mailbox. A Claude session has no inbox and never polls
one -- its harness hands it peer messages directly (see
adapters/transport/claude.py) -- so a file inbox for one is write-only, and
writing to it leaves an unread nobody can ever clear. That is how four inboxes
on this machine were orphaned.

The exception is declared here rather than derived from transport.for_kind:
transport/filebus.py imports store, so asking transport would make store's
import of this module a cycle.
"""

from __future__ import annotations

from typing import Any

from ...address import SESSION
from ._process_backed import _get
from ._process_backed import is_live as _is_live

SPACE = SESSION

NO_MAILBOX_KINDS: tuple[str, ...] = ("claude",)


def is_live(entry: Any) -> bool:
    return _is_live(entry)


def has_mailbox(entry: Any) -> bool:
    return _get(entry, "kind") not in NO_MAILBOX_KINDS
