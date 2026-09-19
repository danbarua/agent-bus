"""Every record the roster, the mailboxes and the session lifecycle write.

All at TRACE: they are the evidence behind a roster row -- which inputs a
registration had, which rule decided it, what changed on disk -- and are
silent at the default level. None carries message text; a message is measured
(`text_len`) and named (`trace_id`), never copied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .logevents import Event, Level, MessageEvent

# -- registration -------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RegisterDecided(Event):
    """One `register()` call: what it was asked for and which rule answered.

    `decision` is `same_pid_update`, `took_over_dead_entry` or `minted`.
    `why` is the rule: `pid_already_registered`, `dead_entry_under_name_and_kind`,
    `no_dead_entry_under_name_and_kind` or `name_held_by_live_entry`.
    `candidates` counts the dead entries under the requested name and kind,
    and is absent for `same_pid_update`, which never looks for them;
    `holder_pid` is the pid the reused entry had before, and `unread` the mail
    that made it worth keeping.
    """

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "register_decided"
    decision: str
    why: str
    requested: str
    final_name: str
    entry_id: str
    entry_kind: str
    target_pid: int | None
    cwd: str
    aliases: list
    reused_id: bool
    live_entries: int
    candidates: int | None = None
    previous_name: str | None = None
    holder_pid: int | None = None
    unread: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RosterEntrySaved(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "roster_entry_saved"
    entry_id: str
    name: str
    entry_kind: str
    holder_pid: int | None
    presence: str
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RosterEntryRemoved(Event):
    """`decision` is `pruned_dead`, `unregistered_by_name` or `unregistered_by_pid`."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "roster_entry_removed"
    decision: str
    entry_id: str
    name: str
    entry_kind: str
    holder_pid: int | None
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RosterEntryRetained(Event):
    """A dead or departing entry kept because mail is still waiting in it.

    `why` is `dead_entry_with_unread_mail` or `session_ended_with_unread_mail`.
    """

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "roster_entry_retained"
    why: str
    entry_id: str
    name: str
    holder_pid: int | None
    unread: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RosterEntryRemoveFailed(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "roster_entry_remove_failed"
    entry_id: str
    path: str
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RosterFileUnreadable(Event):
    """A roster file that is not JSON or not an entry. It is skipped, so the
    roster is one row short until the file is fixed."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "roster_file_unreadable"
    path: str
    error: str
    error_message: str


# -- resolving an address -----------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetResolved(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "target_resolved"
    target: str
    matched_by: str
    entry_id: str
    name: str
    entry_kind: str
    holder_pid: int | None
    live: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetUnresolved(Event):
    """`candidates` is how many roster entries were compared."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "target_unresolved"
    target: str
    candidates: int


@dataclass(frozen=True, slots=True, kw_only=True)
class AddressParsed(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "address_parsed"
    target: str
    entry_kind: str | None
    space: str
    value: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DiscoveredMatched(Event):
    """A harness-published session that is the same agent as a roster entry,
    so it is not listed a second time. `matched_by` is `alias` or `kind_pid`."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "discovered_matched"
    target: str
    matched_by: str
    entry_id: str
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DiscoveredSkipped(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "discovered_skipped"
    target: str
    why: str
    entry_kind: str
    holder_pid: int | None


# -- mail ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageWritten(MessageEvent):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "message_written"
    to: str
    sender: str
    entry_id: str
    unread: int
    text_len: int
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageRefused(Event):
    """`why` is `too_long`, `no_such_agent`, `no_mailbox` or `inbox_full`."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "message_refused"
    target: str
    why: str
    text_len: int
    unread: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageAcked(MessageEvent):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "message_acked"
    entry_id: str
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageAckMissed(Event):
    """`why` is `no_mailbox` or `no_such_message`."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "message_ack_missed"
    why: str
    ref: str
    target: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageIdResolved(Event):
    """`decision` is `exact`, `prefix`, `none` or `ambiguous`; `candidates` is
    how many ids the reference could have meant."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "message_id_resolved"
    ref: str
    decision: str
    candidates: int


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxLineUnreadable(Event):
    """A torn or malformed line in an inbox file. It is skipped."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "inbox_line_unreadable"
    path: str
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageTimestampUnreadable(MessageEvent):
    """A message whose `ts` cannot be read is treated as not expired."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "message_timestamp_unreadable"
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxCompacted(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "inbox_compacted"
    path: str
    removed: int
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxExpiredHidden(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "inbox_expired_hidden"
    entry_id: str
    removed: int


# -- the session lifecycle ----------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class HostPidLookup(Event):
    """One harness adapter looking up its session's pid.

    `decision` is `found`, `stale_pid`, `not_found` or `no_session_id`.
    """

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "host_pid_lookup"
    entry_kind: str
    decision: str
    session_id: str | None = None
    holder_pid: int | None = None
    path: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class HostPidResolved(Event):
    """`decision` is `adapter` or `parent_process`; `none` when neither answered."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "host_pid_resolved"
    entry_kind: str
    decision: str
    session_id: str | None = None
    target_pid: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionDescribed(Event):
    """`decision` is `adapter_named` or `name_derived`."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "session_described"
    entry_kind: str
    decision: str
    name: str
    cwd: str
    session_id: str | None = None
    target_pid: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionStartResolved(Event):
    """Whether a start keeps the name a live entry on this pid already holds.

    `decision` is `kept_claimed_name` or `used_descriptor_name`. `derived` says
    whether that held name is still the pid-derived default, which is the only
    kind a start may replace.
    """

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "session_start_resolved"
    decision: str
    why: str
    requested: str
    final_name: str
    entry_kind: str
    live_entries: int
    target_pid: int | None = None
    derived: bool | None = None
    previous_name: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionListenerDecided(Event):
    """`decision` is `started`, `declined`, `not_needed`, `no_pid` or `failed`.

    `declined` means the spawner returned no pid; `listener_spawn_decided` says why.
    """

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "session_listener_decided"
    decision: str
    why: str
    name: str
    entry_kind: str
    target_pid: int | None = None
    listener_pid: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionEnded(Event):
    """`decision` is the listener's: `stopped`, `not_needed` or `no_pid`."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "session_ended"
    decision: str
    entry_kind: str
    removed: int
    target_pid: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerSpawnDecided(Event):
    """`decision` is `spawned`, `already_running`, `foreign_session_file` or
    `unreadable_session_file`."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "listener_spawn_decided"
    decision: str
    name: str
    target_pid: int
    path: str
    listener_pid: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerStopped(Event):
    """`decision` is `signalled`, `no_pid_file` or `unreadable_pid_file`."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "listener_stopped"
    decision: str
    target_pid: int
    path: str
    listener_pid: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PublishedSessionPatched(Event):
    """`decision` is `patched`, `unchanged`, `no_listener_pid` or `unreadable`."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "published_session_patched"
    decision: str
    target_pid: int
    patched: list
    path: str | None = None
    listener_pid: int | None = None
