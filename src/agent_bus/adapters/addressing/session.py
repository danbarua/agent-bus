"""`session` -- a harness's own session id: `claude:<sid>`, `grok:<sid>`, `omp:<id>`.

Every one of these is backed by a live process in its harness's registry, so
liveness is that process.

Every session has a mailbox, including Claude.

That was not always true. A Claude session never polls an inbox -- its harness
hands it peer messages directly (adapters/transport/claude.py) -- so a file
inbox for one was write-only, and writing to it left an unread nobody could
ever clear. That is how four inboxes on this machine were orphaned, and it is
why `NO_MAILBOX_KINDS = ("claude",)` used to live here.

The objection was unclearable unreads, and it is now dissolved rather than
overruled: commands/messages.send writes the durable copy **already acked**
once a native transport has delivered, so the unread never exists. Do not
re-add the exclusion citing the orphans -- the orphans were real, and pre-acking
is what fixed them.

What that buys: one code path for every peer, the MCP server safe to install
into Claude Code rather than something we caution against, and "you've got
mail" meaning something precise -- a message stays unread only when the native
transport failed.
"""

from __future__ import annotations

from typing import Any

from ...address import SESSION
from ._process_backed import is_live as _is_live

SPACE = SESSION

def is_live(entry: Any) -> bool:
    return _is_live(entry)


def has_mailbox(entry: Any) -> bool:
    return True
