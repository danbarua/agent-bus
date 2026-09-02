"""The HTTP layer: routing, the bridge transport, and the OAuth flow.

One POST route, five GETs and a 405. No framework, deliberately: a framework
for that is the dependency AGENTS.md warns about, and the bus's whole identity
is `dependencies = []`. The concurrency ceiling is `ThreadingHTTPServer` and
that is a choice, not a default -- single-user traffic behind Cloud Run, which
terminates TLS and forwards plain HTTP on $PORT.

What this file is *not* is the protocol. `rpc.py` holds the JSON-RPC surface
and the reasons it looks the way it does; `pages.py` holds what is served as a
document; `config.py` holds what the container is. Each of those is testable
without binding a port, which is why they left.
"""

from __future__ import annotations

import html
import json
import logging
import os
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import logs
import oauth
from config import OAuthConfig, ServerConfig, config_from_env, version
from pages import CONSENT_PAGE, STATIC, metadata
from rpc import DISCOVERY_METHODS, dispatch, err
from store import INBOX, OUTBOX, Rejected, queue

# Logged in full. Everything else is redacted -- an allowlist, because a
# denylist forgets the header someone adds next year. These logs exist to be
# read during a connector mystery, which is exactly when they get pasted
# somewhere public.
LOGGED_HEADERS = frozenset({"content-type", "content-length", "user-agent", "accept"})

log = logging.getLogger(logs.LOGGER_NAME)


def redact(headers: Any) -> dict[str, str]:
    return {
        k.lower(): (v if k.lower() in LOGGED_HEADERS else "<redacted>")
        for k, v in headers.items()
    }


def make_handler(store: Any, issuer: str,
                 verify: Callable[[str | None], tuple[str, str] | None],
                 oauth_config: OAuthConfig | None = None) -> type:
    docs = metadata(issuer)
    cfg = oauth_config

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _path(self) -> str:
            """The path with the query stripped, and safe before parsing.

            A request line malformed enough to fail parsing reaches the error
            path before `self.path` exists at all.
            """
            return (getattr(self, "path", "") or "").split("?")[0]

        # Ops that happen on a timer rather than because anything occurred.
        # A bridge polls every two minutes forever and republishes its roster
        # on the same treadmill, so at INFO these are the only two records most
        # deployments would ever show -- and a level whose every line is noise
        # trains people to stop reading the level.
        QUIET_OPS = frozenset({"pull", "roster"})

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

        def _consent(self, cfg: OAuthConfig, params: dict[str, str],
                     refused: bool = False) -> None:
            """Render the form. Deliberately not the predecessor's single Allow
            button: that survived on hostname obscurity, and CT logs mean the
            hostname was never secret.

            **The same two checks the POST makes, in the same place.** They used
            to differ -- this rendered on the redirect_uri alone -- so anyone
            could produce a URL that showed this page naming any client id they
            liked. No code could be issued, but the page is the only thing a
            person is ever asked to trust, and one that renders for a client the
            server has never heard of teaches them to click through it.
            """
            address = (cfg.allowlist or {}).get(params.get("redirect_uri", ""), "")
            if not address or not store.client(params.get("client_id", "")):
                self._send_html(400, "<p>Unknown client, or redirect_uri is "
                                     "not permitted.</p>")
                return
            hidden = "\n".join(
                f'<input type=hidden name="{html.escape(k)}" value="{html.escape(v)}">'
                for k, v in params.items() if k != "passphrase")
            # Failing closed is right; failing silently is not. An unchanged
            # form cannot be told apart from a broken server, and the second
            # reading ends with someone abandoning a system that works. There
            # is nothing to leak: the client knows it submitted a passphrase.
            note = ("<p><strong>That was not the right passphrase.</strong> "
                    "Nothing has been connected.</p>" if refused else "")
            self._send_html(200, note + CONSENT_PAGE.format(
                client_id=html.escape(params.get("client_id", "")),
                address=html.escape(address),
                redirect_uri=html.escape(params.get("redirect_uri", "")),
                hidden=hidden))

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

        def do_GET(self) -> None:  # stdlib's spelling
            if cfg and self.path.split("?")[0] == "/authorize":
                query = urllib.parse.urlparse(self.path).query
                self._consent(cfg, {k: v[0] for k, v in
                                    urllib.parse.parse_qs(query).items()})
                return
            if self.path in STATIC:
                ctype, body = STATIC[self.path]
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(body)
                self._log_response(200)
            elif self.path in docs:
                self._send(200, docs[self.path])
            elif self.path == "/health":
                self._send(200, {"ok": True, "version": version()})
            elif self.path == "/mcp":
                self._problem(405, "Method not allowed", "this endpoint takes POST")
            else:
                self._problem(404, "Not found")

        def do_DELETE(self) -> None:
            self._problem(405, "Method not allowed", "this endpoint takes POST")

        def do_POST(self) -> None:
            path = self.path.split("?")[0]
            # `cfg` is passed rather than read from the closure. These four
            # are the OAuth surface and cannot run without it, and saying so in
            # the signature puts the guarantee in one place -- it used to live
            # in these routing checks *and* again inside each method, two
            # copies that nothing kept in step.
            if cfg and path == "/register":
                self._register(cfg)
                return
            if cfg and path == "/authorize":
                self._authorize(cfg)
                return
            if cfg and path == "/token":
                self._token(cfg)
                return
            if path == "/bridge":
                self._bridge()
                return
            if path != "/mcp":
                self._problem(404, "Not found")
                return
            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                msg = json.loads(raw or b"{}")
            except (ValueError, OSError):
                self._send(400, err(None, -32700, "parse error"))
                return

            method = msg.get("method")
            # What the caller asked for, on the request record itself. `tool`
            # as well as `verb`, because every mailbox read and write is one
            # `tools/call` and the tool name is the only thing separating them.
            self._intent = {"verb": method}
            if method == "tools/call":
                self._intent["tool"] = (msg.get("params") or {}).get("name")
            token = None
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            who = verify(token)

            # Anonymous discovery, gated tool calls. The whole point.
            if method in DISCOVERY_METHODS:
                kind, peer = who or ("", "")
            elif who is None:
                self._send(401, err(msg.get("id"), -32001, "unauthenticated"))
                return
            else:
                kind, peer = who

            reply = dispatch(msg, store, kind, peer, authed=who is not None)
            if reply is None:
                self._send(202, {})
                return
            self._send(200, reply)

        # ----------------------------------------------------------- bridge

        def _bridge(self) -> None:
            """The mirror of the connector's tools, for our own client.

            Deliberately its own verbs, not the connector's with the meaning
            flipped by role: a connector's `get_inbox` drains the inbox this
            fills, its `send_message` fills the outbox this drains. These are
            transport ops between two pieces of our own code, so they answer to
            what the bridge needs; the connector surface answers to the bus's
            vocabulary. One set moving must not drag the other.
            """
            claims = oauth.verify_token(self._token_presented(), cfg.key) if cfg else None
            if not claims or claims.get("kind") != "access":
                self._problem(401, "Unauthenticated", "no usable bearer token was presented")
                return
            # A connector's token is valid and names the same address. Without
            # this it could push into its own inbox, forging mail that looks
            # like it came from the team.
            if claims.get("client_id") != "bridge":
                self._problem(403, "Not a bridge token",
                              "this endpoint is for a bridge, not a connector")
                return

            address = claims.get("address") or ""
            kind, _, name = address.partition(":")
            if not (kind and name):
                self._problem(403, "Token names no address",
                              "the token carries no peer address to act for")
                return

            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw or b"{}")
            except ValueError:
                self._problem(400, "Malformed request", "the body is not JSON")
                return

            # The address is the token's. There is no field to override it with,
            # which is why a bridge cannot ask to be someone else.
            inbox, outbox = queue(kind, name, INBOX), queue(kind, name, OUTBOX)
            op = body.get("op")
            self._intent = {"verb": op}
            if op in self.QUIET_OPS:
                self._log_level = logging.DEBUG
            try:
                if op == "push":
                    # `to` is the token's address, not the body's. The bridge
                    # never names the recipient of an inbound message -- the
                    # queue already is the recipient -- so there is nothing here
                    # to spoof.
                    message = {**(body.get("message") or {}), "to": address}
                    mid = store.write(inbox, message)
                    # The message id is the journey; the request trace above is
                    # one hop within it. Both, not one -- see
                    # docs/structured-logging.md.
                    log.info("bridge push", extra={"trace_id": mid, "to": address})
                    self._send(200, {"id": mid})
                elif op == "pull":
                    msgs = store.read(outbox, unread_only=True)
                    # A record either way. Logging only per message meant an
                    # empty poll emitted nothing at all, so a bridge that had
                    # stopped polling looked exactly like one that was healthy
                    # and idle -- and a bridge polls every two minutes forever,
                    # so "nothing waiting" is the overwhelmingly common case
                    # and belongs at DEBUG.
                    if msgs:
                        log.info("bridge pull", extra={"count": len(msgs),
                                                       "to": address})
                        for m in msgs:
                            log.info("bridge pull message",
                                     extra={"trace_id": m.get("id"), "to": m.get("to")})
                    else:
                        log.debug("bridge pull", extra={"count": 0, "to": address})
                    self._send(200, {"messages": msgs})
                elif op == "ack":
                    ids = body.get("ids") or []
                    acked = store.ack(outbox, ids)
                    log.info("bridge ack", extra={"count": len(ids), "acked": acked,
                                                  "to": address})
                    for mid in ids:
                        log.debug("bridge ack message", extra={"trace_id": mid})
                    self._send(200, {"acked": acked})
                elif op == "read":
                    # Where a message got to, inside its lifetime. Both queues,
                    # because *which one holds it* is the whole diagnostic:
                    # unread in `inbox` means the connector has not looked;
                    # unread in `outbox` means the bridge has not pulled it;
                    # absent means delivered and expired, or it never arrived.
                    #
                    # No special case for a send-only peer -- a webhook's inbox
                    # is simply empty, per the note in `store.py`.
                    #
                    # Does not consume. This is a query, and an operator asking
                    # where a message went must not be the reason it stops
                    # being redelivered.
                    mid = body.get("message_id")
                    if not isinstance(mid, str) or not mid:
                        self._problem(400, "Missing field", "read needs a message_id")
                        return
                    found, where = None, None
                    for name, q in (("inbox", inbox), ("outbox", outbox)):
                        found = store.read_one(q, mid)
                        if found is not None:
                            where = name
                            break
                    log.info("bridge read", extra={"trace_id": mid, "to": address,
                                                   "queue": where or "not found"})
                    self._send(200, {"queue": where, "message": found})
                elif op == "roster":
                    agents = body.get("agents") or []
                    store.publish_roster(address, agents)
                    # The liveness signal every `list_agents` answer rests on,
                    # and it logged nothing at any level: an empty roster and a
                    # bridge that stopped publishing were indistinguishable
                    # from outside, which is the exact confusion `list_agents`
                    # own empty-case message exists to explain.
                    log.debug("bridge roster", extra={"count": len(agents),
                                                      "to": address})
                    self._send(200, {"ok": True})
                else:
                    self._problem(
                        400, "Unknown operation",
                        f"this server does not implement the `{op}` op. A newer "
                        "client against an older deployment reaches here.")
            except Rejected as e:
                log.warning(op or "bridge", extra={"verb": op, "ok": False,
                                                   "reason": str(e)})
                self._problem(400, "Refused", str(e))

        def _token_presented(self) -> str:
            auth = self.headers.get("Authorization") or ""
            return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

        # ------------------------------------------------------------ OAuth

        def _register(self, cfg: OAuthConfig) -> None:
            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw or b"{}")
            except ValueError:
                self._send(400, {"error": "invalid_request"})
                return
            try:
                record = oauth.register_client(store, body,
                                               allowlist=list(cfg.allowlist))
            except oauth.Refused as e:
                # The reason goes to the log, not to the client: an error that
                # says which half was wrong tells a stranger what to try next.
                log.warning("register", extra={"verb": "register", "ok": False,
                                               "reason": str(e)})
                self._send(400, {"error": "invalid_redirect_uri"})
                return
            self._send(200, {"client_id": record["client_id"],
                             "redirect_uris": record["redirect_uris"],
                             "token_endpoint_auth_method": "none"})

        def _authorize(self, cfg: OAuthConfig) -> None:
            form = self._form()
            redirect_uri = form.get("redirect_uri", "")
            # Kept alongside the identical check in `_consent`, not folded into
            # it: this is the path that mints a code, and a POST need never have
            # been preceded by a GET.
            address = (cfg.allowlist or {}).get(redirect_uri, "")
            if not address or not store.client(form.get("client_id", "")):
                self._send_html(400, "<p>Unknown client, or redirect_uri is "
                                     "not permitted.</p>")
                return
            if not oauth.consent_granted(form.get("passphrase"), cfg.passphrase):
                # Re-render rather than redirect. A refused consent must not
                # reach the callback at all -- no code, and nothing for a
                # watcher of the redirect to learn.
                log.warning("authorize", extra={"verb": "authorize", "ok": False,
                                                "reason": "passphrase",
                                                "client_id": form.get("client_id")})
                self._consent(cfg, {k: v for k, v in form.items()
                                    if k != "passphrase"}, refused=True)
                return
            code = oauth.issue_code(
                store,
                client_id=form["client_id"],
                redirect_uri=redirect_uri,
                code_challenge=form.get("code_challenge", ""),
                resource=f"{issuer}/mcp",
                scope=form.get("scope") or "mcp",
            )
            query = {"code": code}
            if form.get("state"):
                # Round-tripped or the client aborts, and it is the client's
                # CSRF defence rather than ours to skip.
                query["state"] = form["state"]
            self._redirect(f"{redirect_uri}?{urllib.parse.urlencode(query)}")

        def _token(self, cfg: OAuthConfig) -> None:
            form = self._form()
            grant = form.get("grant_type")
            if grant == "refresh_token":
                claims = oauth.verify_token(form.get("refresh_token", ""), cfg.key)
                if not claims or claims.get("kind") != "refresh":
                    self._send(400, {"error": "invalid_grant"})
                    return
                self._issued(cfg, claims["address"], claims.get("client_id", ""))
                return
            if grant != "authorization_code":
                self._send(400, {"error": "unsupported_grant_type"})
                return
            try:
                record = oauth.redeem_code(
                    store, code=form.get("code", ""),
                    verifier=form.get("code_verifier", ""),
                    redirect_uri=form.get("redirect_uri", ""),
                    client_id=form.get("client_id", ""))
            except oauth.Refused as e:
                log.warning("token", extra={"verb": "token", "ok": False,
                                            "reason": str(e)})
                self._send(400, {"error": "invalid_grant"})
                return
            self._issued(cfg, cfg.allowlist[record["redirect_uri"]],
                         record["client_id"])

        def _issued(self, cfg: OAuthConfig, address: str, client_id: str) -> None:
            self._send(200, {
                "access_token": oauth.mint_access(address, cfg.key, client_id),
                "refresh_token": oauth.mint_refresh(address, cfg.key, client_id),
                "token_type": "Bearer",
                "expires_in": oauth.ACCESS_TTL_SECONDS,
                "scope": "mcp",
            })

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

    return Handler


def handler_for(store: Any, config: ServerConfig) -> type:
    """The one line `serve()` used to get wrong, somewhere a test can reach.

    It dropped `oauth_config`, which left `/register`, `/authorize`, `/token`
    and `/bridge` permanently refusing everyone while discovery kept answering.
    A test of the config alone does not catch that -- it caught nothing until
    this became a seam.
    """
    return make_handler(store, config.issuer, config.verify,
                        oauth_config=config.oauth)


def main(store_factory: Callable[..., Any]) -> None:
    """Config first, then the store. The order is the point.

    `Firestore()` opens a credentials chain and a connection. Building it before
    the config is checked means a container with a missing signing key dies on
    an authentication error from Google -- which reads as an infrastructure
    problem and sends you to look at IAM, instead of the one-line message
    naming the environment variable that is actually wrong.
    """
    # First, before config can fail: a startup refusal nobody can read is the
    # same problem one level earlier.
    logs.configure()
    cfg = config_from_env()
    # The factory takes the database because the config knows it and the
    # container does not: `store.Firestore` is passed in by name from the
    # Dockerfile, with nothing bound to it.
    serve(store_factory(database=cfg.database), cfg)


def serve(store: Any, config: ServerConfig | None = None) -> None:
    # Before anything else: a server that cannot be read is a server that gets
    # diagnosed from HTTP status codes.
    logs.configure()
    cfg = config or config_from_env()
    log.info("serving", extra={"issuer": cfg.issuer, "port": cfg.port,
                               "allowlisted": len(cfg.oauth.allowlist),
                               "database": cfg.database or "(default)"})
    ThreadingHTTPServer(("", cfg.port), handler_for(store, cfg)).serve_forever()
