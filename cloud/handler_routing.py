"""Which path gets which handler, and the bearer check on the way in.

Last in the chain because it is the only part that needs the others: `do_POST`
reaches into the OAuth flow and the bridge transport, and inherits both.
"""

from __future__ import annotations

import contextlib
import json
import urllib.parse

from config import version
from handler_bridge import BridgeOps
from handler_oauth import OAuthFlow
from handler_webhook import WebhookIngress
from pages import STATIC
from rpc import DISCOVERY_METHODS, dispatch, err


class Routing(BridgeOps, OAuthFlow, WebhookIngress):
    def do_GET(self) -> None:  # stdlib's spelling
        cfg, docs = self.deps.cfg, self.deps.docs
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

    def _drain(self) -> None:
        """Read the request body and throw it away.

        Every POST carries one, and HTTP/1.1 keeps the connection open: a body
        left on the socket is read as the *next* request line, and the garbage
        that produces is answered as if it were a request. The symptom is a
        record naming a method of `?` and the path of the request before it,
        and it appears nowhere near its cause.

        Observed in production, not deduced. GitHub was posting to
        `/webhooks/github` -- plural, and not a route here -- and every delivery
        produced two records: the 404 it deserved, then a 400 for the payload
        being read as a request line.
        """
        with contextlib.suppress(Exception):
            self._body = self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self) -> None:
        store, verify, cfg = self.deps.store, self.deps.verify, self.deps.cfg
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
        # `/webhook/<name>` -- the name is the peer, so a second source is
        # configuration rather than a route. Split once and rejected if it
        # carries anything further: a path is not a place to be creative.
        if path.startswith("/webhook/"):
            name = path[len("/webhook/"):]
            if not name or "/" in name:
                self._drain()
                self._problem(404, "Not found")
                return
            self._webhook(name)
            return
        if path != "/mcp":
            self._drain()
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
