"""Structured logging for agent-bus: stdlib only, one file, the standard place.

One line of JSON per event, in `$XDG_STATE_HOME/agent-bus/agent-bus.jsonl` --
`~/.local/state` when that is unset, which is where the sibling projects on this
contract keep theirs. Somewhere findable beats somewhere clever: the previous
default was stderr, and the answer to "where does agent-bus log" was nowhere a
person could look.

Falls back to stderr if that path cannot be opened. A log must never stop a
process starting.

**One file, not a directory**: every record carries who emitted it, so a single
file demultiplexes with `jq` and keeps the ordering *between* agents -- which is the
thing you need when A sent and B never saw it. Concurrent writers are safe
because POSIX appends under PIPE_BUF are atomic, and these records are small by
construction: message bodies are recorded as lengths, never copied.

Destination and volume are separate knobs, and unset does not mean silent:

    unset            WARNING -- a verb that FAILED, with its error. Nothing
                     else. This is the level everything runs at, so it is the
                     level a failure has to reach.
    INFO             every verb call too: arguments, timing, outcome. The
                     envelope record -- who sent what to whom, and when.
    TRACE            the firehose. One line per UDS frame, contents included.
    off / none /     nothing at all, for when a harness renders stderr in
    quiet / silent   the conversation and you do not want it there.

DEBUG is not listed because nothing emits at it. It used to be advertised as
"more, when something is being taken apart", and there was no more: the whole
package made exactly one logging call. An advertised level with nothing on it
is worse than a missing one -- you turn it on, see the same records, and
conclude the thing you are hunting did not happen.

**TRACE records message content. Every other level measures a body and never
copies it**, because a log that copies message text is a second inbox with a
different lifetime and no TTL. TRACE is the deliberate exception: it exists to
take the wire apart, nothing selects it by accident, and it should not be left
on.

Set it once in your shell and every agent you start inherits it.

The JSON is shaped for Cloud Logging, which reads `severity` and `message` from
structured lines, so agent-bridge can forward these without agent-bus knowing
that GCP exists.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import sys
import time
from typing import Any

LOGGER_NAME = "agent_bus"

# Above CRITICAL, so nothing is emitted. `logging` has no OFF, and somebody
# running five harnesses will type one of these rather than look it up.
# Python has no TRACE. 5 is the conventional slot beneath DEBUG, and the name
# is registered so a record says "TRACE" rather than "Level 5".
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

SILENT = logging.CRITICAL + 1
OFF_WORDS = frozenset({"off", "none", "silent", "quiet", "no", "0"})

# TRACE is the one level that may copy a body, and a body here can be a million
# characters. One `write()` that large can be split, which does not lose a
# record -- it produces a file `jq` dies halfway through, and only ever while
# someone is taking the wire apart. So a traced string is capped and the
# untruncated length goes beside it, because a record that silently shortened
# its own evidence is worse than one that says how much it left out.
TRACE_FIELD_CAP = 8192

# What a message body is. These are recorded as lengths; everything else in a
# call is addressing, and addressing is what you need to reconstruct it.
CONTENT_KEYS = frozenset({"text", "summary", "message"})

# What cannot be derived from the roster: `surface`, which entry point is
# running -- cli, mcp, listen, bridge -- and `client`, which harness is on the
# other end of an MCP handshake. Both are fixed for the life of the process, so
# caching them cannot go stale.
#
# `surface` is stated by the entry point rather than inferred. The alternative
# was reading it off `client`, which only exists for MCP and only names the
# transport by accident: `codex-mcp-client` says so, `omp-coding-agent` and
# `grok-shell-agent-bus` do not.
# `service` is part of the contract in docs/structured-logging.md, and it is
# seeded rather than set by a caller: three projects' logs join on it, so a
# record without one is unattributable the moment it leaves this machine.
_identity: dict[str, Any] = {"service": "agent-bus"}


def _who() -> dict[str, Any]:
    """This process's bus identity, resolved as the record is written.

    Asked for, never pushed in. A logger that needs `register()` to tell it
    things is a logger that can change what `register()` does -- and the cached
    version was wrong anyway, still saying `pending-<pid>` long after the agent
    had named itself. Only runs for records that pass the level filter.
    """
    try:
        from .store import get_self

        me = get_self()
        return {"agent": me.name, "kind": me.kind} if me else {}
    except Exception:  # noqa: BLE001  # never fail a record over identity
        return {}


def _version() -> str:
    from . import __version__

    return __version__


# Cloud Logging's LogSeverity has no TRACE. A record carrying one is not
# rejected, it is silently read as DEFAULT -- the same trap `WARN` is, and the
# one this contract warns about. The level stays TRACE; what the record carries
# is the nearest severity that exists. Nothing else emits at DEBUG, so DEBUG in
# a record means it came from the firehose.
_SEVERITY = {"TRACE": "DEBUG"}


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, with `severity` so Cloud Logging reads it."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "severity": _SEVERITY.get(record.levelname, record.levelname),
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "message": record.getMessage(),
            "v": _version(),
            "pid": record.process,
            **_who(),
            **_identity,
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def identify(**fields: Any) -> None:
    """Say who this process is, for every record from here on.

    Called at startup and again when a peer claims a name, because a record
    that cannot say who emitted it is why the old per-pid files existed.
    """
    _identity.update({k: v for k, v in fields.items() if v is not None})


def _default_log_file(service: str = "agent-bus") -> str:
    """Where every other tool on the machine keeps this.

    `$XDG_STATE_HOME/agent-bus/`, falling back to `~/.local/state`, which is
    where state that survives a restart and is not config belongs. Naming a
    path of our own meant nobody could answer "where does agent-bus log".

    One file per binary (#197), `{service}.jsonl` in the same directory --
    `agent-bridge.jsonl` beside `agent-bus.jsonl`, not one shared stream
    split by the `service` field. A launchd bridge restarts on its own
    schedule and a person tailing it wants only its own traffic; a single
    interleaved file makes that a `jq` query every time instead of a `tail`.
    Multiple bridge processes (one per address: `desktop:claude`,
    `desktop:chatgpt`, ...) still share the one `agent-bridge.jsonl` --
    `identify(address=...)` is what tells their records apart, the same
    demultiplexing-by-field this function's own docstring above rejects at
    the file level, deliberately kept one level down.
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "agent-bus", f"{service}.jsonl")


def _log_file_env_var(service: str) -> str:
    """The override name for `service` -- `AGENT_BUS_LOG_FILE` for
    `agent-bus` itself (there is nothing to derive: it already is that name),
    `AGENT_BRIDGE_LOG_FILE` for `agent-bridge`, and the same pattern for
    anything named `agent-<x>` later. One name transform rather than a second
    place that has to be told about each new binary."""
    suffix = service.removeprefix("agent-")
    return f"AGENT_{suffix.upper().replace('-', '_')}_LOG_FILE"


def configure(force: bool = False, service: str = "agent-bus") -> logging.Logger:
    """Attach a handler once. Idempotent, so any entry point may call it.

    `service` picks the destination file AND the `service` field, from one
    call -- a verb the bridge calls through `commands.agents`/`.messages`
    (`join`, `register`, plain bus traffic with nothing bridge-specific
    about it) never calls `identify()` itself, so if `configure()` did not
    also set `_identity` here, every one of those records would sit in
    `agent-bridge.jsonl` still claiming `service: agent-bus`. A later
    `identify(service=...)` can still override it, but nothing has had to
    remember to call one for the common case.

    The destination itself, in order: `AGENT_BRIDGE_LOG_FILE` (or whichever
    name `_log_file_env_var(service)` derives) if that specific override is
    set; else `AGENT_BUS_LOG_FILE`, which is what `tests/agent_bridge/
    conftest.py`'s autouse fixture already sets for the whole suite and what
    collapses every service into one file when that is wanted on purpose;
    else the per-service default. For `agent-bus` itself the first two steps
    are the same variable, so nothing about its own resolution changes.
    """
    log = logging.getLogger(LOGGER_NAME)
    identify(service=service)
    if log.handlers and not force:
        return log
    for h in list(log.handlers):
        log.removeHandler(h)
    dest = (os.environ.get(_log_file_env_var(service))
            or os.environ.get("AGENT_BUS_LOG_FILE")
            or _default_log_file(service))
    handler: logging.Handler
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        handler = logging.FileHandler(dest, encoding="utf-8")
    except OSError:
        # Never stdout: the MCP server speaks JSON-RPC on it, and the CLI's
        # own output is there for a human to read.
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    log.addHandler(handler)
    log.setLevel(_level())
    log.propagate = False
    return log


def _level() -> int:
    """Unset means WARNING, not silence: a failure still has to reach someone."""
    raw = (os.environ.get("AGENT_BUS_LOG_LEVEL") or "").strip().lower()
    if not raw:
        return logging.WARNING
    if raw in OFF_WORDS:
        return SILENT
    named = logging.getLevelName(raw.upper())
    return named if isinstance(named, int) else logging.WARNING


def describe(args: dict[str, Any] | None) -> dict[str, Any]:
    """What was passed, without what was said."""
    if not isinstance(args, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k in CONTENT_KEYS:
            out[f"{k}_len"] = len(v) if isinstance(v, str) else None
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = type(v).__name__
    return out


def _capped(fields: dict[str, Any]) -> dict[str, Any]:
    """Truncate traced strings, and say by how much.

    `<field>_len` appears only when the field was cut, so its presence is the
    truncation marker. Nothing is appended to the value itself: an ellipsis in
    a copied frame is a character that was never on the wire, and this level
    exists precisely to be read as what the wire carried.
    """
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, str) and len(value) > TRACE_FIELD_CAP:
            out[key] = value[:TRACE_FIELD_CAP]
            out[f"{key}_len"] = len(value)
        else:
            out[key] = value
    return out


def warn(message: str, **fields: Any) -> None:
    """A call that succeeded, but on input that says something is off
    elsewhere -- not a failure of this call, so `@logged` (which only
    reaches WARNING when the wrapped verb raises) never reaches it.

    Emits at the default level: unset means "a failure, with its error",
    and this is the same signal for a caller that got corrected rather than
    refused. A stale or mistyped argument silently overridden is exactly the
    kind of thing that looks fine here and is a symptom fifty lines up the
    stack -- worth a record even though nothing here raised.
    """
    try:
        log = logging.getLogger(LOGGER_NAME)
        if not log.isEnabledFor(logging.WARNING):
            return
        log.warning(message, extra={"fields": fields})
    except Exception:  # noqa: BLE001, S110  # a logger must never fail a call
        pass


def info(message: str, **fields: Any) -> None:
    """Something happened, worth keeping when the level asks for it, but not
    evidence anything is wrong -- a bridge starting up, a backlog draining
    on the way back up, a departure. `@logged` already covers this shape for
    a verb call, recording it at INFO on success; this is the same severity
    for an event that is not a verb call at all (#197's bridge loop is the
    first caller with nothing to wrap).

    Same gate as a verb's own success record: unset means WARNING, so this
    is silent by default and appears at `AGENT_BUS_LOG_LEVEL=info` or louder.
    """
    try:
        log = logging.getLogger(LOGGER_NAME)
        if not log.isEnabledFor(logging.INFO):
            return
        log.info(message, extra={"fields": fields})
    except Exception:  # noqa: BLE001, S110  # a logger must never fail a call
        pass


def trace(message: str, **fields: Any) -> None:
    """The firehose. Everything, when the wire itself is in question.

    **TRACE is the one level that may record message content.** Everywhere
    else a body is measured and never copied, because a log that copies
    message text is a second inbox with a different lifetime and no TTL. This
    exists to take the protocol apart -- one line per frame, contents and all
    -- and it is never on by accident: nothing selects it but an explicit
    `AGENT_BUS_LOG_LEVEL=trace`.

    Do not leave it on. It writes what your agents said to each other.
    """
    try:
        log = logging.getLogger(LOGGER_NAME)
        if not log.isEnabledFor(TRACE):
            return
        log.log(TRACE, message, extra={"fields": _capped(fields)})
    except Exception:  # noqa: BLE001, S110  # a logger must never fail a call
        pass


def logged(func: Any) -> Any:
    """Record that this verb was called, with what, and how it went.

    Applied at the command layer so both surfaces are covered once: the CLI and
    the MCP server route through the same verbs, which is what makes "what do
    agents actually call" answerable without asking either of them.

    Never changes what the verb does. A logger that can break a call is worse
    than no logger, so emitting is wrapped and failures are dropped.
    """

    # Bound once, not per call: callers pass positionally as often as not, and
    # a record that says `register` with no arguments answers nothing.
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        sig = None

    def _args(a: tuple, kw: dict) -> dict[str, Any]:
        if sig is None:
            return dict(kw)
        try:
            bound = sig.bind_partial(*a, **kw)
            return dict(bound.arguments)
        except TypeError:
            return dict(kw)

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            # No result, so no message, so nothing to correlate. The record is
            # still emitted -- at WARNING -- and says so by omission rather
            # than by an empty trace_id.
            _emit(func.__name__, _args(args, kwargs), started, ok=False, error=str(e))
            raise
        _emit(func.__name__, _args(args, kwargs), started, ok=True,
              trace_id=_trace_of(result))
        return result

    return wrapper


def _trace_of(result: Any) -> str | None:
    """The id of the message this verb produced, if it produced one.

    Looked for rather than required. Verbs return different shapes and most
    return no message at all -- `list_agents` and `self` must not grow an empty
    trace_id, because an empty one groups every unrelated record in the world
    under a single meaningless trace.
    """
    if isinstance(result, dict):
        mid = result.get("id")
        if isinstance(mid, str) and mid:
            return mid
    return None


def _emit(verb: str, kwargs: dict[str, Any], started: float, *,
          ok: bool, error: str | None = None, trace_id: str | None = None) -> None:
    try:
        log = logging.getLogger(LOGGER_NAME)
        # A failure is a warning; a call that worked is traffic. They were
        # both INFO, and the package logs nowhere else, so at the default
        # level -- WARNING -- agent-bus was silent even when a send raised.
        # The module docstring promised the opposite, and no test could catch
        # it: every piece of the machinery was correct.
        level = logging.INFO if ok else logging.WARNING
        if not log.isEnabledFor(level):
            return
        # Nested, not merged. A verb takes `kind` and so does an agent's
        # identity; flattened, a `list_agents(kind="omp")` call would make the
        # record claim that is what the caller *is*.
        fields = {"verb": verb, "ok": ok,
                  "ms": int((time.monotonic() - started) * 1000),
                  "args": describe(kwargs)}
        # Top level, not inside `args`. It was in args only when a caller
        # passed message_id= explicitly -- present on inbound bridge
        # deliveries, absent on everything else, which is the most confusing
        # possible arrangement. See docs/structured-logging.md.
        if trace_id:
            fields["trace_id"] = trace_id
        if error is not None:
            fields["error"] = error
        log.log(level, verb, extra={"fields": fields})
    except Exception:  # noqa: BLE001, S110  # a logger must never fail a call
        pass
