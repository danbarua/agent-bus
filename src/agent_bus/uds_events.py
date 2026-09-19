"""Every record the UDS listener and `send-peer` write, as a type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .logevents import Event, Level, MessageEvent

# -- the listener's life ------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerStarted(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "listener_started"
    name: str
    socket: str
    session: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerSignalled(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "listener_signalled"
    signal: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerAdoptedHost(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "listener_adopted_host"
    name: str
    watch_pid: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerFoundNoHost(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "listener_found_no_host"
    watch_pid: int
    waited_seconds: float


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerRenamed(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "listener_renamed"
    name: str
    requested: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerTokenUnreadable(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "listener_token_unreadable"
    path: str
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerPidFileFailed(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "listener_pid_file_failed"
    path: str
    watch_pid: int
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerPidFileWritten(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "listener_pid_file_written"
    path: str
    watch_pid: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerSocketBound(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "listener_socket_bound"
    socket: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerAliasRegistered(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "listener_alias_registered"
    name: str
    alias: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerSessionPublished(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "listener_session_published"
    name: str
    session: str
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerCleanedUp(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "listener_cleaned_up"
    socket: str
    count: int


# -- one inbound connection ---------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameReceived(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "frame_received"
    bytes: int


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameUnparseable(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "frame_unparseable"
    bytes: int
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameRefused(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "frame_refused"
    #: `not_authenticated` or `token_mismatch`.
    why: str
    bytes: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ConnectionAuthenticated(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "connection_authenticated"


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameParsed(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "frame_parsed"
    frame: str


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameTextExtracted(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "frame_text_extracted"
    sender: str
    wrapped: bool
    text_len: int


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameDelivered(MessageEvent):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "frame_delivered"
    to: str
    sender: str
    text_len: int


@dataclass(frozen=True, slots=True, kw_only=True)
class FramePersistFailed(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "frame_persist_failed"
    to: str
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ConnectionReset(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "connection_reset"
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ConnectionHandlerRaised(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "connection_handler_raised"
    error: str
    error_message: str


# -- the acknowledgement dialled back to the sender --------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusBackTargeted(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "status_back_targeted"
    frame_id: str
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusBackSkipped(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "status_back_skipped"
    #: `own_socket`.
    why: str
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusBackAuthSent(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "status_back_auth_sent"
    token_len: int


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusBackDelivered(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "status_back_delivered"
    path: str
    frame: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusBackFailed(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "status_back_failed"
    #: `no_peer_token`, `send_error` or `handler_raised`.
    why: str
    path: str | None = None
    error: str | None = None
    error_message: str | None = None


# -- sending to a peer -------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SendPeerDelivered(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "send_peer_delivered"
    path: str
    frame_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SendPeerFailed(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "send_peer_failed"
    #: `no_own_socket`, `no_peer_token` or `send_error`.
    why: str
    path: str | None = None
    error: str | None = None
    error_message: str | None = None


# -- resolving sockets, names and tokens ------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnSocketResolved(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "own_socket_resolved"
    socket: str
    #: `env`, `own_pid`, `listener_pid_file` or `ancestor`.
    via: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnSocketUnresolved(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "own_socket_unresolved"


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerTokenResolved(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "peer_token_resolved"
    path: str
    #: `exact_key`, `procstart_match`, `first_key` or `none`.
    via: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerKeyUnreadable(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "peer_key_unreadable"
    path: str
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SocketNameUnparseable(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "socket_name_unparseable"
    path: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AdvertisedNameDefaulted(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "advertised_name_defaulted"
    socket: str
    name: str
    error: str
    error_message: str
