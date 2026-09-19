"""Every record agent-bridge writes, as a type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from agent_bus.logevents import Event, Level, MessageEvent

# -- one message's journey ----------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Forwarded(MessageEvent):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "forwarded"
    to: str
    sender: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AckedLocally(MessageEvent):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "acked_locally"
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ForwardFailed(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "forward_failed"
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoverFailed(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "recover_failed"
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReceiptNotDelivered(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "receipt_not_delivered"
    sender: str
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Delivered(MessageEvent):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "delivered"
    to: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplyHeld(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "reply_held"
    to: str
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplyWithoutAddressee(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "reply_without_addressee"


@dataclass(frozen=True, slots=True, kw_only=True)
class AckedInCloud(MessageEvent):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "acked_in_cloud"


@dataclass(frozen=True, slots=True, kw_only=True)
class AckFailed(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "ack_failed"
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SpoolFileSkipped(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "spool_file_skipped"
    error: str
    error_message: str
    status: int | None = None


# -- a webhook bridge ---------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Control(MessageEvent):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "control"
    sender: str
    verb: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlWithoutSender(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "control_without_sender"


@dataclass(frozen=True, slots=True, kw_only=True)
class SubscriptionsNotPersisted(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "subscriptions_not_persisted"
    sender: str
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class EventNotJson(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "event_not_json"
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class EventMatchedNobody(MessageEvent):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "event_matched_nobody"
    gh_event: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EventDelivered(MessageEvent):
    """`message_id` is the cloud's id for the source event; `delivered_id` the
    local message it went into. A digest is one local message and many events."""

    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "event_delivered"
    to: str
    topic: str
    gh_event: str
    count: int
    delivered_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class EventNotDelivered(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "event_not_delivered"
    to: str
    topic: str
    gh_event: str
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageWithoutId(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "message_without_id"


# -- the process --------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BridgeStarted(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "bridge_started"
    token_source: str | None = None
    version_source: str
    install: str
    auto_reply: bool
    name: str
    peer: str | None = None
    inbound_poll_seconds: float
    outbound_poll_seconds: float
    url: str | None = None
    spool_dir: str | None = None
    module_path: str
    executable: str
    python: str
    argv: list[str] | None = None
    log_file: str
    log_level: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BridgeStopped(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "bridge_stopped"
    reason: str
    error: str | None = None
    error_message: str | None = None
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BridgeNotStarted(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "bridge_not_started"
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BacklogForwarded(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "backlog_forwarded"
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LeftBus(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "left_bus"
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerDeclared(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "peer_declared"
    peer: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PeerNotDeclared(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "peer_not_declared"
    peer: str
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SubscriptionsRestored(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "subscriptions_restored"
    name: str
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SubscriptionsNotRestored(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "subscriptions_not_restored"
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenExpiry(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "token_expiry"
    days: float
    token_source: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenExpiryWarning(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "token_expiry_warning"
    days: float
    token_source: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class NoCloudEndpoint(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "no_cloud_endpoint"
    spool_dir: str
    token_source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class KeychainUnreadable(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "keychain_unreadable"
    error: str | None = None
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenFileUnreadable(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "token_file_unreadable"
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadFailed(MessageEvent):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "read_failed"
    error: str
    error_message: str
    status: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CloudCallFailed(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "cloud_call_failed"
    op: str
    error: str
    error_message: str
    status: int | None = None
    consecutive: int
    suppressed: int
    retry_in_seconds: float
    since: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CloudCallRefused(Event):
    level: ClassVar[Level] = "error"
    message: ClassVar[str] = "cloud_call_refused"
    op: str
    error: str
    error_message: str
    status: int | None = None
    consecutive: int
    suppressed: int
    retry_in_seconds: float
    since: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CloudCallRecovered(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "cloud_call_recovered"
    op: str
    failures: int
    outage_seconds: float
    since: str
