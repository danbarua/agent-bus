"""`thread` -- a Codex thread. The space with no liveness rule.

A thread is a durable document; processes attach to it and detach. Verified
against a real app-server: every thread reports status `notLoaded` and
`canAcceptDirectInput: null`, and every one accepts a queued message anyway,
because thread/queue/add writes to SQLite before any wake attempt. Codex's
own registry records no pid and no socket to check
(docs/harnesses/codex-messaging-reference.md section 5).

So existence is the only question, and the answer is always yes. Asking
"is the process alive" of a thread is a category error -- and asking it is
exactly what made a pid-less entry invisible.

No mailbox: a thread is written to via its own transport, so a file inbox
here would be write-only.
"""

from __future__ import annotations

from typing import Any

from ...address import THREAD

SPACE = THREAD


def is_live(entry: Any) -> bool:
    return True


def has_mailbox(entry: Any) -> bool:
    return False
