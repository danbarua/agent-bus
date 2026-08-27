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

log = logging.getLogger("agent-bus-cloud")


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
    """The four tools. `kind:peer` is who is calling, from the token, never args."""
    inbox, outbox = queue(kind, peer, INBOX), queue(kind, peer, OUTBOX)

    if name == "list-agents":
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

    if name == "read":
        msgs = store.read(inbox, unread_only=args.get("unread_only", True))
        if not msgs:
            return _text("Nothing waiting.", messages=[])
        lines = [f"- `{m['id']}` from **{m.get('from', '?')}**: {m.get('summary') or ''}"
                 for m in msgs]
        return _text(f"{len(msgs)} waiting:\n\n" + "\n".join(lines), messages=msgs)

    if name == "ack":
        ids = args.get("ids")
        if not isinstance(ids, list) or not ids:
            return _text("ack needs a list of ids. There is no 'everything' mode.")
        return _text(f"Acked {store.ack(inbox, ids)} of {len(ids)}.")

    if name == "write":
        try:
            mid = store.write(outbox, {
                "to": args.get("to"),
                "text": args.get("text"),
                "summary": args.get("summary") or "",
                "from": args.get("from"),
            })
        except Rejected as e:
            return _text(f"Refused: {e}")
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


def no_one_is_authenticated(token: str | None) -> tuple[str, str] | None:
    """The default when no signing key is configured. Fails closed: discovery
    works, every tool call is refused. A server that cannot verify anyone must
    not fall back to trusting everyone."""
    return None


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

        def _consent(self, params: dict[str, str]) -> None:
            """Render the form. Deliberately not the predecessor's single Allow
            button: that survived on hostname obscurity, and CT logs mean the
            hostname was never secret."""
            address = (cfg.allowlist or {}).get(params.get("redirect_uri", ""), "")
            if not address:
                self._send_html(400, "<p>redirect_uri is not permitted.</p>")
                return
            hidden = "\n".join(
                f'<input type=hidden name="{html.escape(k)}" value="{html.escape(v)}">'
                for k, v in params.items() if k != "passphrase")
            self._send_html(200, CONSENT_PAGE.format(
                client_id=html.escape(params.get("client_id", "")),
                address=html.escape(address),
                redirect_uri=html.escape(params.get("redirect_uri", "")),
                hidden=hidden))

        def do_GET(self) -> None:  # stdlib's spelling
            if cfg and self.path.split("?")[0] == "/authorize":
                query = urllib.parse.urlparse(self.path).query
                self._consent({k: v[0] for k, v in
                               urllib.parse.parse_qs(query).items()})
                return
            if self.path in docs:
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
                self._consent({k: v for k, v in form.items() if k != "passphrase"})
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


def serve(store: Any, issuer: str | None = None, port: int | None = None,
          verify: Callable[[str | None], tuple[str, str] | None] | None = None) -> None:
    issuer = issuer or os.environ["AGENT_BUS_CLOUD_ISSUER"]
    port = port or int(os.environ.get("PORT") or 8080)
    handler = make_handler(store, issuer, verify or no_one_is_authenticated)
    ThreadingHTTPServer(("", port), handler).serve_forever()
