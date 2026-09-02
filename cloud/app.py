"""The server: what to build, and how to start it.

One POST route, five GETs and a 405. No framework, deliberately: a framework
for that is the dependency AGENTS.md warns about, and the bus's whole identity
is `dependencies = []`. The concurrency ceiling is `ThreadingHTTPServer` and
that is a choice, not a default -- single-user traffic behind Cloud Run, which
terminates TLS and forwards plain HTTP on $PORT.

The surface itself lives next door, in the order a request meets it:

    rpc.py             the JSON-RPC surface, pure and socket-free
    pages.py           what is served as a document
    config.py          what the container is
    handler_base.py    sending, logging, and `Deps`
    handler_oauth.py   consent, registration, tokens
    handler_bridge.py  the `/bridge` transport
    handler_routing.py which path gets which of those

`make_handler` is the seam where they become one class, and the only place a
server's dependencies are named.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from typing import Any

import logs
from config import OAuthConfig, ServerConfig, config_from_env
from handler_base import Deps
from handler_routing import Routing
from pages import metadata

log = logging.getLogger(logs.LOGGER_NAME)


def make_handler(store: Any, issuer: str,
                 verify: Callable[[str | None], tuple[str, str] | None],
                 oauth_config: OAuthConfig | None = None) -> type:
    """One class out of the four that make it up, bound to one server's deps.

    A subclass per server rather than a parameter per request: the handler is
    constructed by `ThreadingHTTPServer` for every connection and is handed the
    class, not an instance, so the class is the only place values can be
    attached.
    """
    class Handler(Routing):
        deps = Deps(store=store, issuer=issuer, verify=verify,
                    cfg=oauth_config, docs=metadata(issuer))

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
