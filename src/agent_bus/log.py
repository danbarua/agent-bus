"""Structured logging for agent-bus: stdlib only, stderr by default.

One line of JSON per event, on stderr, where whoever started the process
already collects it -- a harness's own MCP server log, `listeners/<pid>.log`,
docker, or Cloud Run. Nothing is written to a directory of our own, because a
log file nobody asked for is a file nobody deletes.

`AGENT_BUS_LOG_FILE` names one file when you want one. **One file, not a
directory**: every record carries who emitted it, so a single file
demultiplexes with `jq` and keeps the ordering *between* agents -- which is the
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


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, with `severity` so Cloud Logging reads it."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "severity": record.levelname,
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


def configure(force: bool = False) -> logging.Logger:
    """Attach a handler once. Idempotent, so any entry point may call it."""
    log = logging.getLogger(LOGGER_NAME)
    if log.handlers and not force:
        return log
    for h in list(log.handlers):
        log.removeHandler(h)
    dest = os.environ.get("AGENT_BUS_LOG_FILE")
    handler: logging.Handler
    if dest:
        try:
            handler = logging.FileHandler(dest, encoding="utf-8")
        except OSError:
            handler = logging.StreamHandler(sys.stderr)
    else:
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
        log.log(TRACE, message, extra={"fields": fields})
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
            _emit(func.__name__, _args(args, kwargs), started, ok=False, error=str(e))
            raise
        _emit(func.__name__, _args(args, kwargs), started, ok=True)
        return result

    return wrapper


def _emit(verb: str, kwargs: dict[str, Any], started: float, *,
          ok: bool, error: str | None = None) -> None:
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
        if error is not None:
            fields["error"] = error
        log.log(level, verb, extra={"fields": fields})
    except Exception:  # noqa: BLE001, S110  # a logger must never fail a call
        pass
