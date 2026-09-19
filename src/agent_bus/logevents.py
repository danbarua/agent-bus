"""The closed vocabulary of a log record, and the typed events written in it.

`FIELDS` is the one owner of every key a record may carry. Its order is the
column order of every record, so a line reads the same shape wherever it came
from: who wrote it, then closed-set categories, then ids, then numbers, then
free text -- what does not change on the left, what does on the right.

An event is a frozen dataclass. It cannot carry a key the registry does not
name, and a message-scoped one cannot be built without the message's id.
Process constants live in `identify()`; the per-message trace id lives in a
ContextVar bound for the duration of the work (`bind_trace`).

`docs/structured-logging.md` says why each field exists; a test holds the two
together.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Literal, get_args

from .protocol import MessageId

Level = Literal["trace", "info", "warning", "error"]

#: How an emitter was reached. A closed set: a new entry point is a decision.
ADAPTERS: Final = ("cli", "mcp", "listen", "bridge")


@dataclass(frozen=True, slots=True)
class Spec:
    type: type
    doc: str
    #: May carry message content. Only a TRACE event may declare one.
    content: bool = False
    #: Written by the logger itself, never by an event.
    envelope: bool = False


def _fields(*rows: tuple[str, type, str, dict[str, bool]]) -> dict[str, Spec]:
    return {name: Spec(type_, doc, **flags) for name, type_, doc, flags in rows}


_E = {"envelope": True}
_N: dict[str, bool] = {}
_C = {"content": True}

FIELDS: Final[dict[str, Spec]] = _fields(
    # -- envelope: who wrote this, and which message it is about -------------
    ("time", str, "ISO 8601 UTC, millisecond precision, fixed width", _E),
    ("severity", str, "a Cloud Logging severity: DEBUG INFO WARNING ERROR CRITICAL", _E),
    ("service", str, "which binary: agent-bus, agent-bridge, agent-bus-cloud", _E),
    ("adapter", str, "how it was reached: cli, mcp, listen, bridge", _E),
    ("version", str, "the build that wrote the line", _E),
    ("pid", int, "the writing process", _E),
    ("ppid", int, "its parent -- 1 means orphaned", _E),
    ("address", str, "which bridge, when several share one file", _E),
    ("agent", str, "the emitter's name on the bus", _E),
    ("kind", str, "the emitter's harness kind", _E),
    ("client", str, "the harness on the far end of an MCP handshake", _E),
    ("trace_id", str, "the message id: one id, both sides of the boundary", _E),
    ("message", str, "the event name; for a verb call, the verb", _E),
    # -- closed-set categories ------------------------------------------------
    ("verb", str, "what a caller asked for, never the transport", _N),
    ("method", str, "the JSON-RPC method an MCP client called", _N),
    ("tool", str, "the MCP tool an MCP client called", _N),
    ("framing", str, "how MCP messages are delimited on stdio: ndjson or content-length", _N),
    ("op", str, "which cloud operation: roster, pull, push or ack", _N),
    ("status", int, "HTTP status from the cloud, when there was one", _N),
    ("error", str, "the exception class -- filterable, never prose", _N),
    ("token_source", str, "environment, keychain, file or none", _N),
    ("version_source", str, "distribution or source-tree", _N),
    ("install", str, "installed, editable or source-tree", _N),
    ("reason", str, "why a process is stopping", _N),
    ("auto_reply", bool, "whether the bridge answers each sender with a receipt", _N),
    ("why", str, "the rule a decision applied, or why a send failed; a closed set per event", _N),
    ("via", str, "which of several ways found a socket or a token", _N),
    ("wrapped", bool, "whether a frame's text arrived inside a cross-session-message envelope", _N),
    ("input_ready", bool, "whether a wake was caused by input on stdin", _N),
    ("dir_changed", bool, "whether a wake was caused by a watched directory changing", _N),
    ("notified", bool, "whether an update notification was sent to the subscriber", _N),
    ("found", bool, "whether a lookup found an entry", _N),
    ("unread_only", bool, "whether an inbox read asked for unread messages only", _N),
    ("decision", str, "the branch a decision took; a closed set per event", _N),
    ("matched_by", str, "how an address matched an entry: id, name, alias, former_name or pid", _N),
    ("entry_kind", str, "a roster entry's harness kind, as against the emitter's `kind`", _N),
    ("presence", str, "the status an agent reported: idle, busy or unknown", _N),
    ("space", str, "an address space: bus, session, thread or one nobody has named", _N),
    ("reused_id", bool, "whether a registration inherited an existing entry's id and mailbox", _N),
    ("derived", bool, "whether a name is exactly what the pid-derived default would be", _N),
    ("live", bool, "whether a roster entry's process is running", _N),
    # -- who and what it concerned --------------------------------------------
    ("to", str, "the recipient", _N),
    ("sender", str, "the originator of a message", _N),
    ("peer", str, "the declared relay partner", _N),
    ("name", str, "an agent's name on the bus", _N),
    ("target", str, "the address a caller asked to reach, as typed", _N),
    ("holder", str, "the name on a roster entry that holds an address", _N),
    ("topic", str, "a subscription topic", _N),
    ("gh_event", str, "the GitHub event name", _N),
    ("delivered_id", str, "the id of the local copy a delivery produced", _N),
    ("entry_id", str, "a roster entry's id, which names its mailbox", _N),
    ("final_name", str, "the name a registration was granted, after any numeric suffix", _N),
    ("previous_name", str, "the name an entry held before a rename", _N),
    ("ref", str, "a message id as the caller typed it, possibly a prefix", _N),
    ("value", str, "the part of an address after its space", _N),
    ("frame_id", str, "the id a peer put on a frame, which a status-back acknowledges", _N),
    ("requested", str, "the name a caller asked for, which may differ from the one it got", _N),
    ("alias", str, "an address recorded against an agent", _N),
    ("uri", str, "an MCP resource address", _N),
    ("rpc_id", str, "the JSON-RPC request id, as text", _N),
    ("session_id", str, "the harness's own id for its session", _N),
    ("client_name", str, "clientInfo.name from an MCP initialize", _N),
    ("kind_hint", str, "the harness kind an MCP handshake identified", _N),
    ("claimed_kind", str, "the kind a caller asked to register as", _N),
    ("resolved_kind", str, "the kind a registration used", _N),
    ("missing_field", str, "the first required tool argument that was absent or empty", _N),
    # -- numbers ----------------------------------------------------------------
    ("count", int, "how many", _N),
    ("listener_pid", int, "the UDS listener process serving a session", _N),
    ("unread", int, "unread messages waiting in a mailbox", _N),
    ("candidates", int, "how many entries or messages qualified for a decision", _N),
    ("removed", int, "how many entries or messages a step deleted", _N),
    ("live_entries", int, "live roster entries when a decision was made", _N),
    ("signal", int, "the signal number a process received", _N),
    ("watch_pid", int, "the host process a listener stays alive for", _N),
    ("bytes", int, "the size of a frame or line, never its content", _N),
    ("text_len", int, "the length of a message's text, never the text", _N),
    ("token_len", int, "the length of a credential, never the credential", _N),
    ("waited_seconds", float, "how long a process waited for something before going on", _N),
    ("holder_pid", int, "the pid on a roster entry that holds an address", _N),
    ("host_pid", int, "the pid a caller said its host process has", _N),
    ("roster_pid", int, "the pid the roster records for the same name", _N),
    ("target_pid", int, "the pid a registration or listener is for", _N),
    ("frame_bytes", int, "the size of one framed MCP message", _N),
    ("duration_ms", int, "how long a request took", _N),
    ("rpc_code", int, "the JSON-RPC error code that was returned", _N),
    ("consecutive", int, "failures in a row, this one included", _N),
    ("suppressed", int, "failures since the last record of this outage", _N),
    ("failures", int, "how many failures an outage held", _N),
    ("outage_seconds", float, "how long an outage lasted", _N),
    ("retry_in_seconds", float, "when the next attempt is due", _N),
    ("days", float, "days until a credential expires; negative once it has", _N),
    ("inbound_poll_seconds", float, "idle interval between cloud polls", _N),
    ("outbound_poll_seconds", float, "interval between local inbox drains", _N),
    # -- free text, always last ------------------------------------------------
    ("since", str, "when an outage began, ISO 8601 UTC", _N),
    ("url", str, "the cloud endpoint", _N),
    ("spool_dir", str, "where a spooling bridge writes", _N),
    ("cwd", str, "the working directory of the process or session", _N),
    ("watching", list, "the directories a watcher covers", _N),
    ("aliases", list, "the alternate addresses recorded against a roster entry", _N),
    ("patched", list, "the keys written to a published session file", _N),
    ("uris", list, "MCP resource addresses", _N),
    ("subscriptions", list, "the MCP resources a client is subscribed to", _N),
    ("args", dict, "a tool call's arguments with message content measured, not copied", _N),
    ("params", dict, "an MCP request's params as received", _N | {"content": True}),
    ("client_capabilities", dict, "the capabilities an MCP client declared", _N),
    ("server_capabilities", dict, "the capabilities this MCP server declared", _N),
    ("module_path", str, "where the running package was imported from", _N),
    ("executable", str, "the python interpreter", _N),
    ("python", str, "its version", _N),
    ("argv", list, "how the process was started", _N),
    ("log_file", str, "where this record is being written", _N),
    ("log_level", str, "the level in force", _N),
    ("path", str, "a file or socket path the event concerns", _N),
    ("socket", str, "the unix socket a listener is bound to, or a sender names as its own", _N),
    ("session", str, "the session file a listener published", _N),
    ("error_message", str, "str(exception), capped; may name an agent, never a body", _N),
    ("frame", str, "a parsed frame as JSON, credentials redacted", _C),
)

_RANK: Final = {name: i for i, name in enumerate(FIELDS)}
ENVELOPE: Final = tuple(n for n, s in FIELDS.items() if s.envelope)

#: How long `error_message` may run. A cause is short; a body never belongs.
ERROR_MESSAGE_CAP: Final = 1000


_JSON_TYPE: Final = {str: "string", int: "integer", float: "number", bool: "boolean",
                     list: "array", dict: "object"}


def fields_table() -> str:
    """The field table in `docs/structured-logging.md`, in column order."""
    rows = ["| field | type | written by | meaning |", "|---|---|---|---|"]
    rows += [
        f"| `{name}` | {_JSON_TYPE[spec.type]} | {'logger' if spec.envelope else 'event'} "
        f"| {spec.doc} |"
        for name, spec in FIELDS.items()
    ]
    return "\n".join(rows)


def rank(name: str) -> int:
    """Column position. An unregistered name sorts last rather than failing a
    write -- a logger must not break a call -- and the conformance test is what
    says it is missing."""
    return _RANK.get(name, len(_RANK))


# -- process identity ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Identity:
    service: str = "agent-bus"
    adapter: str | None = None
    address: str | None = None
    agent: str | None = None
    kind: str | None = None
    client: str | None = None


_process = Identity()
_ALIASES = {"surface": "adapter"}


def identify(**changes: Any) -> None:
    """Say who this process is, for every record from here on.

    Whole-object replacement of a frozen value, not a mutable dict, and a
    module global rather than a ContextVar: a thread started with plain
    `threading.Thread` does not inherit context, and would otherwise write
    records claiming to be nobody.

    `None` is ignored; an unknown key or adapter is an error at the call site.
    `surface` is accepted as an alias for `adapter`.
    """
    global _process  # noqa: PLW0603  # one process, one identity
    clean: dict[str, Any] = {}
    for given, value in changes.items():
        key = _ALIASES.get(given, given)
        if key not in Identity.__dataclass_fields__:
            raise TypeError(f"identify() has no field {given!r}")
        if value is not None:
            clean[key] = value
    if "adapter" in clean and clean["adapter"] not in ADAPTERS:
        raise ValueError(f"adapter must be one of {ADAPTERS}, not {clean['adapter']!r}")
    _process = dataclasses.replace(_process, **clean)


def reset_identity() -> None:
    global _process  # noqa: PLW0603  # tests, and a process starting clean
    _process = Identity()


def identity() -> Identity:
    return _process


def process_fields() -> dict[str, Any]:
    """`pid` and `ppid` are asked for, not remembered: a forked child or a
    reparented process must not keep writing its parent's."""
    return {"pid": os.getpid(), "ppid": os.getppid()}


# -- the per-message trace ------------------------------------------------------

_TRACE: ContextVar[MessageId | None] = ContextVar("agent_bus_trace", default=None)


def current_trace() -> MessageId | None:
    return _TRACE.get()


@contextmanager
def bind_trace(message_id: MessageId | str | None) -> Generator[None]:
    """Every record written inside this block carries `trace_id`.

    Restored on exit, so it cannot leak into the next message. A thread that
    needs it must be started under `contextvars.copy_context().run`.
    """
    token = _TRACE.set(MessageId(message_id) if message_id else None)
    try:
        yield
    finally:
        _TRACE.reset(token)


# -- events -----------------------------------------------------------------


_EVENTS: dict[str, type[Event]] = {}


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Subclass with `level`, `message` and fields named in `FIELDS`.

    A subclass that sets `message` is an event, registered when the class is
    defined; one that does not (`MessageEvent`) is a base and never registered.
    A missing or unknown `level`, or a `message` another event already uses, is
    an error at import.
    """

    level: ClassVar[Level]
    message: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # Explicit form: `dataclass(slots=True)` rebuilds the class, and on 3.11
        # zero-argument `super()` still points at the discarded original.
        super(Event, cls).__init_subclass__(**kwargs)
        name = cls.__dict__.get("message")
        if name is None:
            return
        if not isinstance(name, str) or not name:
            raise TypeError(f"{cls.__qualname__}.message must be a non-empty string")
        if getattr(cls, "level", None) not in get_args(Level):
            raise TypeError(f"{cls.__qualname__}.level must be one of {get_args(Level)}")
        known = _EVENTS.get(name)
        # `dataclass(slots=True)` defines each class twice, the second time
        # under the same name.
        if known is not None and (known.__module__, known.__qualname__) != (
                cls.__module__, cls.__qualname__):
            raise TypeError(f"event name {name!r} is used by {known.__qualname__} "
                            f"and {cls.__qualname__}")
        _EVENTS[name] = cls


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageEvent(Event):
    """About one message. Cannot be built without its id, which is written as
    `trace_id` -- the field that joins a message's journey across processes."""

    message_id: MessageId


def event_fields(event: Event) -> dict[str, Any]:
    """The event's own fields in column order, with unset ones omitted."""
    present = {
        f.name: getattr(event, f.name)
        for f in dataclasses.fields(event)
        if f.name != "message_id" and getattr(event, f.name) is not None
    }
    return dict(sorted(present.items(), key=lambda kv: rank(kv[0])))


def trace_of(event: Event) -> MessageId | None:
    if isinstance(event, MessageEvent):
        return event.message_id
    return current_trace()


def all_events() -> list[type[Event]]:
    """Every event class defined so far, each once, for the conformance tests."""
    return list(_EVENTS.values())


def describe_error(exc: BaseException) -> dict[str, Any]:
    """`error` (the class) and `error_message` (capped), and `status` when the
    exception carries one. Fields for an event that failed."""
    out: dict[str, Any] = {
        "error": type(exc).__name__,
        "error_message": str(exc)[:ERROR_MESSAGE_CAP],
    }
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        out["status"] = status
    return out
