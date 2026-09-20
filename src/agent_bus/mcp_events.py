"""Every record the MCP server writes, as a type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from .logevents import Event, Level

# -- the process ---------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class McpServerStarted(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "mcp_server_started"
    cwd: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpStdinClosed(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "mcp_stdin_closed"


@dataclass(frozen=True, slots=True, kw_only=True)
class McpServerStopped(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "mcp_server_stopped"


@dataclass(frozen=True, slots=True, kw_only=True)
class McpSessionStarted(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_session_started"
    name: str
    resolved_kind: str
    session_id: str | None = None
    target_pid: int | None = None
    cwd: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpSessionSkipped(Event):
    """`AGENT_BUS_NAME` is not set: this connection registers nothing."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_session_skipped"


@dataclass(frozen=True, slots=True, kw_only=True)
class McpSessionEnded(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_session_ended"
    name: str
    target_pid: int | None = None


# -- frames --------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class McpFrameRead(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_frame_read"
    framing: str
    frame_bytes: int


@dataclass(frozen=True, slots=True, kw_only=True)
class McpFrameWritten(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_frame_written"
    framing: str
    frame_bytes: int


@dataclass(frozen=True, slots=True, kw_only=True)
class McpParseFailed(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "mcp_parse_failed"
    framing: str
    error: str
    error_message: str


# -- one request ---------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class McpDispatch(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_dispatch"
    method: str | None = None
    rpc_id: str | None = None
    params: dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class McpRequestHandled(Event):
    level: ClassVar[Level] = "info"
    message: ClassVar[str] = "mcp_request_handled"
    method: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None
    duration_ms: int


@dataclass(frozen=True, slots=True, kw_only=True)
class McpRequestFailed(Event):
    """The request raised, or the reply was a JSON-RPC error."""

    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "mcp_request_failed"
    method: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None
    duration_ms: int
    rpc_code: int | None = None
    error: str | None = None
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpResponseDropped(Event):
    """A frame with no `method` is a response, and this server never sends a request."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_response_dropped"
    rpc_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class McpNotificationDropped(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_notification_dropped"
    method: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpMethodUnknown(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_method_unknown"
    method: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class McpInitialized(Event):
    """What the handshake told this server about its client, and what it told the client."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_initialized"
    client_name: str | None = None
    kind_hint: str | None = None
    client_capabilities: dict[str, Any]
    server_capabilities: dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class McpEagerDiscoveryAnswered(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_eager_discovery_answered"
    method: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpToolsListed(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_tools_listed"
    kind_hint: str | None = None
    count: int


# -- tool calls ----------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class McpToolUnknown(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_tool_unknown"
    tool: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpToolFieldMissing(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "mcp_tool_field_missing"
    tool: str
    missing_field: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpToolAccepted(Event):
    """The tool exists and its required arguments are present."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_tool_accepted"
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class McpToolRaised(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "mcp_tool_raised"
    tool: str
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpPresenceTouchSkipped(Event):
    """The session file could not be touched. Presence is best-effort."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_presence_touch_skipped"
    target_pid: int | None = None
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpRegisterKindResolved(Event):
    """`claimed_kind` is what the caller sent. `resolved_kind` is what was used:
    the handshake's answer wins when there is one."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_register_kind_resolved"
    name: str
    claimed_kind: str | None = None
    kind_hint: str | None = None
    resolved_kind: str | None = None
    target_pid: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class McpListenerStartFailed(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_listener_start_failed"
    name: str
    target_pid: int
    error: str
    error_message: str


# -- resources -----------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class McpResourcesListed(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_resources_listed"
    uris: list[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class McpResourceRead(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_resource_read"
    uri: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpResourceUnknown(Event):
    """A read, subscribe or unsubscribe named a resource this server does not have."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_resource_unknown"
    method: str
    uri: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class McpSubscriptionChanged(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_subscription_changed"
    method: str
    uri: str
    subscriptions: list[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class McpWatchCreated(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_watch_created"
    watching: list[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class McpWatchClosed(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_watch_closed"
    watching: list[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class McpWatchFailed(Event):
    """Notifications are off for this connection."""

    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "mcp_watch_failed"
    watching: list[str]
    error: str
    error_message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class McpWatchFired(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_watch_fired"
    input_ready: bool
    dir_changed: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class McpNotifyChecked(Event):
    """`count` is how many unread messages, or roster entries, differ from
    what this connection last saw."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_notify_checked"
    uri: str
    count: int
    notified: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class McpNotifyWithoutRegistration(Event):
    """The inbox resource is subscribed and this connection has no roster entry."""

    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "mcp_notify_without_registration"
    uri: str
