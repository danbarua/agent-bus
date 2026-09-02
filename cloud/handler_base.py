"""The response plumbing every other half of the handler stands on.

`Deps` is what used to be `make_handler`'s closure. Four values were read from
it 49 times across 554 lines, and nothing in a signature said which methods
needed which -- so a method could quietly start depending on `cfg` in a place
the router did not guarantee it. Naming them once, on the class, makes that
visible; `handler_oauth` then takes its config as a parameter because the
router is what knows it exists.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, ClassVar

import logs
from config import OAuthConfig

log = logging.getLogger(logs.LOGGER_NAME)

# Logged in full. Everything else is redacted -- an allowlist, because a
# denylist forgets the header someone adds next year. These logs exist to be
# read during a connector mystery, which is exactly when they get pasted
# somewhere public.
LOGGED_HEADERS = frozenset({"content-type", "content-length", "user-agent", "accept"})


def redact(headers: Any) -> dict[str, str]:
    return {
        k.lower(): (v if k.lower() in LOGGED_HEADERS else "<redacted>")
        for k, v in headers.items()
    }


@dataclass(frozen=True)
class Deps:
    """Everything the handler needs that is not the request.

    Frozen, and built once per server rather than per request: `make_handler`
    assigns it to the generated subclass, which is the one thing that differs
    between a real deployment and a test that binds port 0.
    """

    store: Any
    issuer: str
    verify: Callable[[str | None], tuple[str, str] | None]
    cfg: OAuthConfig | None
    docs: dict[str, dict[str, Any]]


class Base(BaseHTTPRequestHandler):
    """Sending, logging, and the per-request state both depend on."""

    protocol_version = "HTTP/1.1"

    # Assigned on the subclass `make_handler` generates. Declared here so every
    # method below can read it, and so a missing assignment is a type error
    # rather than an AttributeError on the first request.
    deps: ClassVar[Deps]

    # Ops that happen on a timer rather than because anything occurred.
    # A bridge polls every two minutes forever and republishes its roster
    # on the same treadmill, so at INFO these are the only two records most
    # deployments would ever show -- and a level whose every line is noise
    # trains people to stop reading the level.
    QUIET_OPS = frozenset({"pull", "roster"})

    def _path(self) -> str:
        """The path with the query stripped, and safe before parsing.

        A request line malformed enough to fail parsing reaches the error
        path before `self.path` exists at all.
        """
        return (getattr(self, "path", "") or "").split("?")[0]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Per-request state, before the base class handles anything.

        `BaseHTTPRequestHandler.__init__` runs the whole request cycle, so
        assigning ahead of `super()` is what guarantees these exist on
        every path -- including `send_error` answering a request line too
        long to parse, which is a 414 raised before `parse_request` is ever
        called. They are reset again per request in `parse_request`, since
        one connection serves several.

        This replaced `getattr(self, "_log_level", logging.INFO)`, which
        silently defeated the type checker: typeshed's three-argument
        `getattr` returns `Any | _T`, and assigning `Any` to a name
        declared `int | None` keeps the **declared** type -- so
        `if level is None: level = getattr(...)` narrowed nothing and
        `log.log(level, ...)` still saw `int | None`.
        """
        self._intent: dict[str, Any] = {}
        self._log_level: int = logging.INFO
        super().__init__(*args, **kwargs)

    def _log_response(self, code: int, level: int | None = None,
                      **fields: Any) -> None:
        """One record per response, with the values in **fields**.

        `message` is the caller's intent, not a rendered sentence. The
        contract in docs/structured-logging.md is explicit -- "not a
        template, the values go in fields" -- and this was the one surface
        still ignoring it: `"POST /bridge -> 200"` is the identical string
        for a poll that found nothing and a push that carried a message,
        and it left `status` reachable only by matching text.

        **`verb` is the intent, never the HTTP method.** Every call here is
        a POST to one of two paths, so the method is the least informative
        field on the record; the thing worth filtering on is `pull` vs
        `push` vs `tools/call`. The bus already spends `verb` on a CLI verb
        for exactly this reason, and one concept must not have two names
        across two services that agreed to share a vocabulary. The HTTP
        method is `http_method`, which Cloud Run's own request log has too.
        """
        intent = self._intent
        if level is None:
            # A failure is never quiet, whatever op it was. Demoting on the
            # verb alone would put a refused poll at DEBUG -- discarded in
            # process -- and the whole point of moving polls down is that
            # what is left at INFO is worth reading.
            level = self._log_level if code < 400 else logging.INFO
        log.log(level, intent.get("verb") or f"{self.command or '?'} {self._path()}",
                extra={"status": code,
                       "http_method": getattr(self, "command", None),
                       "path": self._path(),
                       "headers": redact(self.headers)
                       if getattr(self, "headers", None) else {},
                       **intent, **fields})

    def _send(self, code: int, payload: Any,
              content_type: str = "application/json") -> None:
        issuer = self.deps.issuer
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # One header, and this is an OAuth endpoint. TLS is Cloud Run's --
        # the container must never try to serve it -- but HSTS is ours.
        self.send_header("Strict-Transport-Security",
                         "max-age=31536000; includeSubDomains")
        if code == 401:
            self.send_header(
                "WWW-Authenticate",
                f'Bearer resource_metadata="{issuer}/.well-known/oauth-protected-resource"')
        self.end_headers()
        self.wfile.write(body)
        # Successes too. A failed ChatGPT discovery is cached client-side
        # and retries produce no traffic at all, so "nothing in the log"
        # has to be distinguishable from "nothing happened".
        self._log_response(code)

    def _problem(self, code: int, title: str, detail: str | None = None,
                 kind: str = "about:blank") -> None:
        """An error in RFC 7807 form, for the endpoints that are ours.

        `title` says what class of thing went wrong and is safe to show a
        person; `detail` says what went wrong *this time*. A client that
        renders `detail or title` is never left with a bare status code --
        which is what sent someone looking at their token when the real
        answer was that the deployment predated the verb they called.

        **Not for the OAuth endpoints, and not for JSON-RPC.** RFC 6749
        §5.2 mandates `{"error": ...}` on the token endpoint and RFC 7591
        §3.2.2 the same for registration; JSON-RPC owns its own envelope.
        Those formats belong to their specs, and a connector parses them by
        those specs -- rewriting them as problem+json would be a spec
        violation dressed up as consistency. `_send` stays for those.
        """
        body: dict[str, Any] = {"type": kind, "title": title, "status": code}
        if detail:
            body["detail"] = detail
        body["instance"] = self.path
        self._send(code, body, content_type="application/problem+json")

    def _send_html(self, code: int, body: str) -> None:
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        # The consent page and its refusals were invisible: `_send_html` is
        # the only response path a person ever sees, and it was the one
        # with no record that it had happened.
        self._log_response(code)

    def _redirect(self, to: str) -> None:
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()
        # The target without its query: on the one redirect this server
        # performs, the query is the authorization code.
        self._log_response(302, redirect_to=to.split("?", maxsplit=1)[0])

    def _form(self) -> dict[str, str]:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return {k: v[0] for k, v in
                urllib.parse.parse_qs(raw.decode(), keep_blank_values=True).items()}

    def parse_request(self) -> bool:
        """Stamp this request's trace, as soon as there are headers to read.

        `parse_request` is what populates `self.headers`, so this is the
        first moment the value exists and the last before dispatch -- and
        overriding here rather than in each `do_*` means nothing on the
        request path can log without it, error paths included. Those are
        the ones worth reading.

        Assigned unconditionally, including to "": HTTP/1.1 keep-alive
        serves several requests on one thread, and a request without the
        header would otherwise inherit the previous one's trace and file
        its logs under someone else's flow.
        """
        # Before `super()`, and unconditionally: a request line too
        # malformed to parse still reaches `send_error`, and on a keep-alive
        # connection it would otherwise be logged under the *previous*
        # request's verb. Same reasoning as the trace below.
        self._intent = {}
        self._log_level = logging.INFO
        ok = super().parse_request()
        if ok:
            logs.TRACE.set(logs.trace_field(
                self.headers.get("X-Cloud-Trace-Context", ""),
                os.environ.get("GOOGLE_CLOUD_PROJECT", "")))
        return ok

    def log_message(self, format: str, *args: Any) -> None:
        """stdlib logs to stderr in its own format; we do our own above.

        The parameter is named `format` because the base class names it
        that and calls it by keyword in places. Shadowing the builtin is
        the stdlib's choice, not ours.
        """

    def send_error(self, code: int, message: str | None = None,
                   explain: str | None = None) -> None:
        """stdlib's error path -- the one that answers a verb we do not
        implement -- which until now logged nothing at all.

        It never passes through `_send`, so `BaseHTTPRequestHandler`
        answered 501 and left no record. In Cloud Run's request log that is
        indistinguishable from a 501 the front end produced without the
        container ever being asked, so "did it reach us?" had no answer
        from the logs alone. Two HEADs from a scanner on 2026-08-27 are the
        case in point.

        WARNING, not INFO: nothing we serve on purpose arrives here.
        """
        super().send_error(code, message, explain)
        # The user-agent is the whole value of this record when the caller
        # is a scanner rather than a client, and `_log_response` carries it.
        self._log_response(code, level=logging.WARNING,
                           ok=False, reason=message)
