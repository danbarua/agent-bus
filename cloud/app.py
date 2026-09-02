"""The public surface: one JSON-RPC endpoint, five metadata documents, 405 else.

Everything here that looks arbitrary was found by watching a real connector
fail, in the predecessor (`c2c-mcp`). Three of them cost a bring-up each:

**Discovery is anonymous; only `tools/call` is gated.** ChatGPT's connector
pings `initialize`, `tools/list` and friends *before it ever attaches a token*,
and attaches `Authorization` only once a tool is actually invoked. Gating every
method uniformly made discovery itself 401, so **no tool was visible at all**,
whether or not OAuth had worked. Safe, because discovery exposes schemas and
never mailbox contents -- the reads and writes are all `tools/call`.

**`resources/list` and `prompts/list` must answer.** Some clients call them
unconditionally during discovery, not gated on the advertised capabilities. A
`Method not found` there did not mean "no resources"; it killed tool discovery
entirely. So both capabilities are declared and both methods return valid
empties, along with `resources/templates/list`.

**`/.well-known/openid-configuration` must be 200, never 404.** ChatGPT
hard-aborts on a 404 there and does not fall back to RFC 8414 -- and a failed
discovery is cached client-side, producing *no server traffic* on retry. It is
the one mistake that cannot be iterated out of.

No framework, deliberately. This is one POST route, five GETs and a 405: a
framework for that is the dependency AGENTS.md warns about, and the bus's whole
identity is `dependencies = []`. The concurrency ceiling is `ThreadingHTTPServer`
and that is a choice, not a default -- single-user traffic behind Cloud Run,
which terminates TLS and forwards plain HTTP on $PORT.
"""

from __future__ import annotations

import html
import json
import logging
import os
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import logs
import oauth
from contract import TOOLS
from store import INBOX, OUTBOX, Rejected, queue

PROTOCOL_VERSION = "2025-06-18"

# Exempt from the bearer check. Read-only schema and capability methods only;
# `tools/call` is deliberately absent, being the only one that touches a
# mailbox. Taken from the predecessor, where the set was arrived at by watching
# discovery 401 in its own logs.
DISCOVERY_METHODS = frozenset({
    "initialize",
    "notifications/initialized",
    "ping",
    "tools/list",
    "resources/list",
    "resources/templates/list",
    "prompts/list",
})

# Logged in full. Everything else is redacted -- an allowlist, because a
# denylist forgets the header someone adds next year. These logs exist to be
# read during a connector mystery, which is exactly when they get pasted
# somewhere public.
LOGGED_HEADERS = frozenset({"content-type", "content-length", "user-agent", "accept"})

log = logging.getLogger(logs.LOGGER_NAME)


def version() -> str:
    """The running build, read at runtime.

    The predecessor's primary staleness detector, and it caught a real one: the
    MCP tool contract is pinned per client at connection time, so an operator
    sees a feature deployed while the attached session sees a schema without it
    -- and both are looking at the truth.
    """
    return os.environ.get("AGENT_BUS_CLOUD_VERSION") or "0+unknown"


def redact(headers: Any) -> dict[str, str]:
    return {
        k.lower(): (v if k.lower() in LOGGED_HEADERS else "<redacted>")
        for k, v in headers.items()
    }


def metadata(issuer: str) -> dict[str, dict[str, Any]]:
    """The five documents a connector reads before it will talk to us.

    `openid-configuration` and `oauth-authorization-server` are served from the
    same structure on purpose: ChatGPT probes the OIDC path unconditionally and
    aborts on 404, and answering it with the RFC 8414 document is what stops
    that without pretending to be an OpenID Provider.
    """
    as_doc = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "registration_endpoint": f"{issuer}/register",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }
    prm = {
        "resource": f"{issuer}/mcp",
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
    }
    return {
        "/.well-known/oauth-authorization-server": as_doc,
        "/.well-known/openid-configuration": as_doc,
        "/.well-known/oauth-protected-resource": prm,
        "/.well-known/oauth-protected-resource/mcp": prm,
        "/.well-known/jwks.json": {"keys": []},
    }


def _ok(mid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text(body: str, **structured: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": body}]}
    if structured:
        out["structuredContent"] = structured
    return out


def call_tool(name: str, args: dict[str, Any], store: Any, kind: str,
              peer: str) -> dict[str, Any]:
    """The tools. `kind:peer` is who is calling, from the token, never args."""
    inbox, outbox = queue(kind, peer, INBOX), queue(kind, peer, OUTBOX)

    # Every tool call, not just the one that writes. Until this line the read
    # path -- get_inbox, read_message, ack_message, list_agents -- emitted
    # nothing, so "did the connector actually fetch that message?" could only
    # be answered by asking the person looking at the client. The request log
    # says a POST reached /mcp; it cannot say which tool ran.
    #
    # The tool name and the caller, never the arguments: `send_message` carries
    # the message body, and these logs exist to be read during a connector
    # mystery, which is exactly when they get pasted somewhere public. Same
    # reasoning as LOGGED_HEADERS above.
    log.info("connector call", extra={"tool": name, "peer": f"{kind}:{peer}"})

    if name == "list_agents":
        agents = store.roster(f"{kind}:{peer}")
        if not agents:
            return _text(
                "Nobody is on the bus, or the bridge is not running. Its roster "
                "expires on its own, so an empty list means it stopped "
                "publishing rather than that the team went home.",
                agents=[],
            )
        lines = [f"- **{a['name']}** ({a.get('kind', '?')})" for a in agents]
        return _text(f"{len(agents)} on the bus:\n\n" + "\n".join(lines), agents=agents)

    if name == "get_inbox":
        msgs = store.read(inbox, unread_only=args.get("unread_only", True))
        if not msgs:
            return _text("Nothing waiting.", messages=[])
        lines = [f"- `{m['id']}` from **{m.get('from', '?')}**: {m.get('summary') or ''}"
                 for m in msgs]
        return _text(f"{len(msgs)} waiting:\n\n" + "\n".join(lines), messages=msgs)

    if name == "read_message":
        mid = args.get("message_id")
        if not isinstance(mid, str) or not mid:
            return _text("read_message needs the message_id get_inbox gave you.")
        msg = store.read_one(inbox, mid)
        if msg is None:
            return _text(f"No message `{mid}`. Ids expire with the message, and "
                         f"only ids from your own inbox resolve.", message=None)
        # The listing carries summaries; this is the one place the body is
        # rendered, so it goes in the text block rather than structured content
        # alone -- a connector that reads only the prose still gets the message.
        return _text(f"From **{msg.get('from', '?')}**"
                     + (f" -- {msg['summary']}" if msg.get("summary") else "")
                     + f"\n\n{msg.get('text', '')}", message=msg)

    if name == "ack_message":
        ids = args.get("ids")
        if not isinstance(ids, list) or not ids:
            return _text("ack_message needs a list of ids. There is no 'everything' mode.")
        return _text(f"Acked {store.ack(inbox, ids)} of {len(ids)}.")

    if name == "send_message":
        try:
            mid = store.write(outbox, {
                "to": args.get("to"),
                "text": args.get("text"),
                "summary": args.get("summary") or "",
                "from": args.get("from"),
            })
        except Rejected as e:
            return _text(f"Refused: {e}")
        # The id this mints is the one the bridge carries down to the local
        # bus, so it is where a connector's journey starts.
        log.info("connector write", extra={"trace_id": mid, "to": args.get("to")})
        return _text(f"Queued as `{mid}`. It reaches the team when the bridge "
                     f"next polls; nobody has read it yet.")

    return _text(f"No such tool: {name}")


def dispatch(msg: dict[str, Any], store: Any, kind: str, peer: str,
             authed: bool) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out. None means "notification".

    Pure: no sockets, no globals. The HTTP layer above is thin enough that
    almost everything worth testing is testable here, and the store is injected
    so the dispatch tests need no emulator.
    """
    method = msg.get("method")
    mid = msg.get("id")

    if method and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _ok(mid, {
            "protocolVersion": PROTOCOL_VERSION,
            # resources and prompts are declared despite having none of either.
            # See the module docstring: not declaring them is what killed tool
            # discovery in the predecessor.
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "agent-bus-cloud", "version": version()},
        })
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": list(TOOLS)})
    if method == "resources/list":
        return _ok(mid, {"resources": []})
    if method == "resources/templates/list":
        return _ok(mid, {"resourceTemplates": []})
    if method == "prompts/list":
        return _ok(mid, {"prompts": []})

    if method == "tools/call":
        if not authed:
            return _err(mid, -32001, "unauthenticated")
        params = msg.get("params") or {}
        return _ok(mid, call_tool(params.get("name") or "",
                                  params.get("arguments") or {},
                                  store, kind, peer))

    return _err(mid, -32601, f"method not found: {method}")


def bearer_verifier(key: bytes) -> Callable[[str | None], tuple[str, str] | None]:
    """Claims decide which queues a caller may touch -- never its arguments.

    `kind` separates a 30-day refresh credential from a 1-hour access one, and
    they are signed by the same key, so checking it is the only thing stopping a
    refresh token being used to read a mailbox.
    """
    def verify(token: str | None) -> tuple[str, str] | None:
        claims = oauth.verify_token(token or "", key)
        if not claims or claims.get("kind") != "access":
            return None
        address = claims.get("address") or ""
        kind, _, name = address.partition(":")
        return (kind, name) if kind and name else None

    return verify


@dataclass(frozen=True)
class OAuthConfig:
    """What the flow needs, and the two halves of the consent gate.

    `allowlist` maps a permitted redirect URI to the peer address it identifies.
    It does double duty on purpose: it refuses a stranger anywhere to have a
    code delivered, and it says which peer a client is. The redirect URI is the
    only thing in the flow that names the vendor, and it is one we control
    rather than one the client asserts.
    """

    key: bytes
    allowlist: dict[str, str]
    passphrase: str


# A face for the hostname, and nothing more.
#
# Certificate transparency publishes the name the moment a cert issues, so
# anyone can find this address; what they should not get for free is who is
# behind it or what is on the other end. The page names no operator, no agent
# and no peer, and there is a test that keeps it that way.
#
# It also stops the bare domain being a 404, which reads as misconfigured
# rather than deliberate.
FRONT_PAGE = """<!doctype html>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=robots content="noindex,nofollow">
<title>agent-bus</title>
<style>
  :root { color-scheme: light dark; --ink: #1a1a1a; --bg: #fbfbfa; --dim: #6b6b6b; }
  @media (prefers-color-scheme: dark) {
    :root { --ink: #e8e6e3; --bg: #16161a; --dim: #8b8b8b; }
  }
  body { background: var(--bg); color: var(--ink); margin: 0;
         font: 16px/1.6 ui-sans-serif, system-ui, -apple-system, sans-serif;
         display: grid; place-items: center; min-height: 100vh; }
  main { max-width: 30rem; padding: 2rem; }
  h1 { font-size: 1.1rem; font-weight: 600; letter-spacing: .02em; margin: 0 0 .75rem; }
  p { color: var(--dim); margin: 0; }
  svg { display: block; margin-bottom: 1.25rem; }
</style>
<main>
  <svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden="true">
    <circle cx="20" cy="8" r="3.5" fill="currentColor"/>
    <circle cx="8" cy="30" r="3.5" fill="currentColor"/>
    <circle cx="32" cy="30" r="3.5" fill="currentColor"/>
    <path d="M20 11.5v9m0 0-9 6.5m9-6.5 9 6.5" stroke="currentColor"
          stroke-width="1.5" stroke-linecap="round" opacity=".55"/>
  </svg>
  <h1>agent-bus</h1>
  <p>A message endpoint. Nothing here is served to a browser.</p>
</main>
"""

FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    '<circle cx="20" cy="8" r="4"/><circle cx="8" cy="30" r="4"/>'
    '<circle cx="32" cy="30" r="4"/></svg>'
)

# Exact paths to (content type, bytes). **A map, not a directory.**
#
# This is an OAuth server. Serving files by path is how `/../../etc/passwd`
# becomes a feature, and no amount of normalising is as safe as having no path
# handling at all: a request either names a key here or it is a 404. Adding an
# asset means adding an entry, which is the point.
STATIC: dict[str, tuple[str, bytes]] = {
    "/": ("text/html; charset=utf-8", FRONT_PAGE.encode()),
    "/favicon.svg": ("image/svg+xml", FAVICON.encode()),
    "/favicon.ico": ("image/svg+xml", FAVICON.encode()),
}


CONSENT_PAGE = """<!doctype html><meta charset=utf-8>
<title>agent-bus</title>
<h1>Connect this client to the bus?</h1>
<p>Client <code>{client_id}</code> wants to send and read messages as
<strong>{address}</strong>, with replies delivered to <code>{redirect_uri}</code>.</p>
<form method=post>
{hidden}
<label>Passphrase <input type=password name=passphrase autofocus></label>
<button type=submit>Allow</button>
</form>
<p><small>The hostname of this server is public: certificate transparency logs
are world-readable, so anyone can find it. The passphrase is what stops them
finishing this form.</small></p>"""


def make_handler(store: Any, issuer: str,
                 verify: Callable[[str | None], tuple[str, str] | None],
                 oauth_config: OAuthConfig | None = None) -> type:
    docs = metadata(issuer)
    cfg = oauth_config

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, payload: Any, method: str | None = None) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
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
            # Successes too, and the JSON-RPC method by name. A failed ChatGPT
            # discovery is cached client-side and retries produce no traffic at
            # all, so "nothing in the log" has to be distinguishable from
            # "nothing happened".
            log.info("%s %s -> %s", self.command, self.path, code,
                     extra={"method": method, "headers": redact(self.headers)})

        def _send_html(self, code: int, body: str) -> None:
            raw = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _redirect(self, to: str) -> None:
            self.send_response(302)
            self.send_header("Location", to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            log.info("302 -> %s", to.split("?", maxsplit=1)[0])

        def _form(self) -> dict[str, str]:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            return {k: v[0] for k, v in
                    urllib.parse.parse_qs(raw.decode(), keep_blank_values=True).items()}

        def _consent(self, params: dict[str, str], refused: bool = False) -> None:
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
            ok = super().parse_request()
            if ok:
                logs.TRACE.set(logs.trace_field(
                    self.headers.get("X-Cloud-Trace-Context", ""),
                    os.environ.get("GOOGLE_CLOUD_PROJECT", "")))
            return ok

        def do_GET(self) -> None:  # stdlib's spelling
            if cfg and self.path.split("?")[0] == "/authorize":
                query = urllib.parse.urlparse(self.path).query
                self._consent({k: v[0] for k, v in
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
                log.info("%s %s -> %s", self.command, self.path, 200,
                         extra={"headers": redact(self.headers)})
            elif self.path in docs:
                self._send(200, docs[self.path])
            elif self.path == "/health":
                self._send(200, {"ok": True, "version": version()})
            elif self.path == "/mcp":
                self._send(405, {"error": "POST only"})
            else:
                self._send(404, {"error": "not found"})

        def do_DELETE(self) -> None:
            self._send(405, {"error": "POST only"})

        def do_POST(self) -> None:
            path = self.path.split("?")[0]
            if cfg and path == "/register":
                self._register()
                return
            if cfg and path == "/authorize":
                self._authorize()
                return
            if cfg and path == "/token":
                self._token()
                return
            if path == "/bridge":
                self._bridge()
                return
            if path != "/mcp":
                self._send(404, {"error": "not found"})
                return
            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                msg = json.loads(raw or b"{}")
            except (ValueError, OSError):
                self._send(400, _err(None, -32700, "parse error"))
                return

            method = msg.get("method")
            token = None
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            who = verify(token)

            # Anonymous discovery, gated tool calls. The whole point.
            if method in DISCOVERY_METHODS:
                kind, peer = who or ("", "")
            elif who is None:
                self._send(401, _err(msg.get("id"), -32001, "unauthenticated"), method)
                return
            else:
                kind, peer = who

            reply = dispatch(msg, store, kind, peer, authed=who is not None)
            if reply is None:
                self._send(202, {}, method)
                return
            self._send(200, reply, method)

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
                self._send(401, {"error": "unauthenticated"})
                return
            # A connector's token is valid and names the same address. Without
            # this it could push into its own inbox, forging mail that looks
            # like it came from the team.
            if claims.get("client_id") != "bridge":
                self._send(403, {"error": "not a bridge token"})
                return

            address = claims.get("address") or ""
            kind, _, name = address.partition(":")
            if not (kind and name):
                self._send(403, {"error": "token names no address"})
                return

            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw or b"{}")
            except ValueError:
                self._send(400, {"error": "invalid_request"})
                return

            # The address is the token's. There is no field to override it with,
            # which is why a bridge cannot ask to be someone else.
            inbox, outbox = queue(kind, name, INBOX), queue(kind, name, OUTBOX)
            op = body.get("op")
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
                    self._send(400, {"error": f"unknown op: {op}"})
            except Rejected as e:
                log.info("bridge push refused: %s", e)
                self._send(400, {"error": "refused", "detail": str(e)})

        def _token_presented(self) -> str:
            auth = self.headers.get("Authorization") or ""
            return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

        # ------------------------------------------------------------ OAuth

        def _register(self) -> None:
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
                log.info("register refused: %s", e)
                self._send(400, {"error": "invalid_redirect_uri"})
                return
            self._send(200, {"client_id": record["client_id"],
                             "redirect_uris": record["redirect_uris"],
                             "token_endpoint_auth_method": "none"})

        def _authorize(self) -> None:
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
                log.info("consent refused for client %s", form.get("client_id"))
                self._consent({k: v for k, v in form.items() if k != "passphrase"},
                              refused=True)
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

        def _token(self) -> None:
            form = self._form()
            grant = form.get("grant_type")
            if grant == "refresh_token":
                claims = oauth.verify_token(form.get("refresh_token", ""), cfg.key)
                if not claims or claims.get("kind") != "refresh":
                    self._send(400, {"error": "invalid_grant"})
                    return
                self._issued(claims["address"], claims.get("client_id", ""))
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
                log.info("token refused: %s", e)
                self._send(400, {"error": "invalid_grant"})
                return
            self._issued(cfg.allowlist[record["redirect_uri"]], record["client_id"])

        def _issued(self, address: str, client_id: str) -> None:
            self._send(200, {
                "access_token": oauth.mint_access(address, cfg.key, client_id),
                "refresh_token": oauth.mint_refresh(address, cfg.key, client_id),
                "token_type": "Bearer",
                "expires_in": oauth.ACCESS_TTL_SECONDS,
                "scope": "mcp",
            })

        def log_message(self, *args: Any) -> None:
            """stdlib logs to stderr in its own format; we do our own above."""

    return Handler


@dataclass(frozen=True)
class ServerConfig:
    """Everything a container needs, assembled once and checked before it binds
    a port. Separate from `serve()` so the assembly is testable -- that seam is
    where the missing wiring hid."""

    issuer: str
    port: int
    oauth: OAuthConfig
    verify: Callable[[str | None], tuple[str, str] | None]
    # None means Firestore's `(default)`, which is what production runs.
    # A staging service names its own so it can share a project without
    # sharing the records.
    database: str | None


def config_from_env() -> ServerConfig:
    """Refuse to start rather than serve a surface that authenticates nobody.

    That is the failure mode worth engineering against: `/health` answers,
    discovery answers, and only a connector attempting a tool call ever finds
    out. It looks like a healthy deployment for as long as nobody uses it.
    """
    issuer = (os.environ.get("AGENT_BUS_CLOUD_ISSUER") or "").strip()
    if not issuer:
        raise RuntimeError(
            "AGENT_BUS_CLOUD_ISSUER is required. It is the OAuth issuer and the "
            "base of every URL a connector caches, so a container that guessed "
            "one would be worse than one that would not start.")

    raw_key = (os.environ.get("AGENT_BUS_CLOUD_SIGNING_KEY") or "").strip()
    try:
        key = bytes.fromhex(raw_key)
    except ValueError:
        key = b""
    # 32 bytes, because that is what the runbook mints. A short key is a
    # truncated or mistyped secret, never a deliberate one.
    if len(key) < 32:
        raise RuntimeError(
            "AGENT_BUS_CLOUD_SIGNING_KEY must be at least 32 bytes of hex "
            "(openssl rand -hex 32). Without it nothing authenticates and every "
            "tool call is refused.")

    raw_allow = (os.environ.get("AGENT_BUS_CLOUD_ALLOWLIST") or "").strip()
    allowlist: dict[str, str] = {}
    if raw_allow:
        try:
            allowlist = json.loads(raw_allow)
            if not isinstance(allowlist, dict):
                raise ValueError
        except ValueError:
            raise RuntimeError(
                "AGENT_BUS_CLOUD_ALLOWLIST must be a JSON object mapping each "
                'permitted redirect URI to a peer address, e.g. {"https://'
                'claude.ai/api/mcp/auth_callback": "desktop:claude"}.') from None

    passphrase = os.environ.get("AGENT_BUS_CLOUD_PASSPHRASE") or ""
    # Required exactly when the flow it gates becomes reachable. A bridge token
    # is minted out of band and never sees the consent page, so a bridge-only
    # deployment needs no passphrase -- and demanding one there would be a
    # prerequisite that buys nothing. An empty one does fail closed, but
    # silently: a connector that cannot be consented to looks identical to one
    # that is broken.
    if allowlist and not passphrase:
        raise RuntimeError(
            "AGENT_BUS_CLOUD_PASSPHRASE is required once a connector is "
            "allowlisted: it is the human half of the consent gate, and without "
            "it no client can ever be authorized.")

    return ServerConfig(issuer=issuer, port=int(os.environ.get("PORT") or 8080),
                        oauth=OAuthConfig(key=key, allowlist=allowlist,
                                          passphrase=passphrase),
                        verify=bearer_verifier(key),
                        database=(os.environ.get("AGENT_BUS_CLOUD_DATABASE") or "").strip()
                        or None)


def handler_for(store: Any, config: ServerConfig) -> type:
    """The one line `serve()` used to get wrong, somewhere a test can reach.

    It dropped `oauth_config`, which left `/register`, `/authorize`, `/token`
    and `/bridge` permanently refusing everyone while discovery kept answering.
    A test of the config alone does not catch that -- it caught nothing until
    this became a seam.
    """
    return make_handler(store, config.issuer, config.verify,
                        oauth_config=config.oauth)


def main(store_factory: Callable[[], Any]) -> None:
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
    log.info("serving %s on :%s, %d connector(s) allowlisted, database=%s",
             cfg.issuer, cfg.port, len(cfg.oauth.allowlist),
             cfg.database or "(default)")
    ThreadingHTTPServer(("", cfg.port), handler_for(store, cfg)).serve_forever()
