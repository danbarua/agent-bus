"""What a harness has to implement, per capability.

The adapters used to be one module per vendor, and you could not tell from the
tree which vendor did what: `discover_all()` iterated four modules,
`lifecycle.ADAPTERS` iterated two, and the send transports were not in
adapters at all -- one lived in uds.py, one in codex_client.py, and nothing
enumerated them. The matrix was real but invisible, maintained as hand-written
tuples in three different files.

It is genuinely sparse (docs/harness-compatibility.md is that matrix), so the
package is split by capability rather than by vendor: `ls adapters/transport/`
answers "who can I send to natively", which is a question this project is
about. A vendor that only does discovery contributes one file and no stubs.

The axes are named for the doc's vocabulary -- discovery, lifecycle, transport,
addressing -- rather than listing/send, so the code and the compatibility matrix
use the same words.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Discovery(Protocol):
    """Read a harness's own registry. Read-only, best effort, never raises.

    Returns roster-shaped dicts for live processes only. An adapter that
    cannot read its harness returns [] -- a missing harness is the normal
    case, not an error.
    """

    KIND: str

    def discover(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class HarnessLifecycle(Protocol):
    """What core needs from a harness to place a session on the bus.

    Core asks; it never sniffs for a harness itself. Only harnesses that can
    host an agent-bus session implement this -- a harness we can merely see
    from outside implements Discovery alone.
    """

    KIND: str

    def detect(self, env: dict[str, str]) -> bool: ...
    def session_id(self, payload: dict[str, Any] | None, env: dict[str, str]) -> str | None: ...
    def host_pid(self, session_id: str | None, env: dict[str, str]) -> int | None: ...
    def session_name(self, session_id: str | None, cwd: str | None) -> str | None: ...
    def workspace(self, env: dict[str, str]) -> str | None: ...


@runtime_checkable
class Transport(Protocol):
    """Deliver a message to an agent of this kind, natively.

    `send` raises on failure and never falls back to another channel. Falling
    back would deliver into a channel the recipient does not read: a Claude
    peer has no file inbox and would never see the message, and writing one
    anyway leaves a phantom unread that misreports the bus.
    """

    KIND: str

    def send(self, entry: dict[str, Any], text: str, summary: str = "") -> dict[str, Any]: ...


@runtime_checkable
class AddressSpace(Protocol):
    """A namespace of identifiers that share a liveness rule.

    The fourth axis, and the one core consults most often: every entry on every
    listing is asked whether it still exists. A space is not a vendor -- that a
    space is sometimes *named* after one (`claude:<sessionId>` is Claude's own
    session namespace) is incidental.

    `is_live` is the whole point. The pid-backed spaces answer it with
    is_process_alive; the thread space answers True unconditionally, because a
    Codex thread is a document that processes attach to and detach from, and is
    queueable when nothing is running at all.

    `has_mailbox` means "may a message ever be *written* to a file inbox at this
    address". It does not gate reads: mail already on disk stays readable
    whatever the rule says now, or recovering an orphaned inbox would be
    impossible.
    """

    SPACE: str

    def is_live(self, entry: Any) -> bool: ...
    def has_mailbox(self, entry: Any) -> bool: ...
