"""Structured logs the server actually emits.

Until this existed, `logging.getLogger("agent-bus-cloud")` had no handler
anywhere, so the root logger's default level applied and every `log.info` was
discarded **inside the process**. Nothing to do with Cloud Run collection: the
records never left. Production ran for a day with a redaction allowlist, four
refusal messages and a request log that had never produced a byte -- and a
Claude Desktop bring-up had to be reconstructed from HTTP status codes.

Deliberately a copy of the shape `agent_bus.log` uses rather than an import of
it. The two packages share no code: the bus must never depend on the server,
and the server is a separate deployable with its own pyproject. Two small
formatters is the price of that, and it is the right price.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from typing import Any

LOGGER_NAME = "agent-bus-cloud"

# The current request's Cloud Logging trace, or "". A ContextVar rather than a
# global because ThreadingHTTPServer runs one thread per request, and a global
# would attribute one request's logs to another under any concurrency at all.
TRACE: contextvars.ContextVar[str] = contextvars.ContextVar("trace", default="")

# LogRecord's own attributes. Anything else on the record came from `extra=`
# and is the caller's, so it goes in the output -- which is how the request
# log's `method` and redacted `headers` survive.
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))


def trace_field(header: str, project: str) -> str:
    """`X-Cloud-Trace-Context` to the field Cloud Logging groups on.

    The header is `TRACE_ID/SPAN_ID;o=1`; the field wants
    `projects/<project>/traces/<TRACE_ID>`. Anything unparseable returns "" and
    the field is then omitted rather than emitted empty -- an empty trace id
    groups every record in the world under one meaningless trace.
    """
    if not header or not project:
        return ""
    trace_id = header.split("/", 1)[0].strip()
    if not trace_id or "/" not in header:
        return ""
    return f"projects/{project}/traces/{trace_id}"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with `severity` so Cloud Logging reads it.

    `severity`, not `level` or `levelname`: Cloud Logging looks for that exact
    key, and a line without it is INFO forever however loudly it was logged.
    """

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "severity": record.levelname,
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "message": record.getMessage(),
        }
        trace = TRACE.get()
        if trace:
            out["logging.googleapis.com/trace"] = trace
        out.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(level: int = logging.INFO, stream: Any = None,
              force: bool = False) -> logging.Logger:
    """Attach one handler. Idempotent, so any entry point may call it."""
    log = logging.getLogger(LOGGER_NAME)
    if log.handlers and not force:
        return log
    for h in list(log.handlers):
        log.removeHandler(h)
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.setLevel(level)
    # Ours and only ours. Propagating would hand every record to the root
    # logger as well, which in a container means printing it twice.
    log.propagate = False
    return log
