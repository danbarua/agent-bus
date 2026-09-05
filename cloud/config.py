"""What the container is, assembled from the environment and checked once.

Separate from the server so the assembly is testable without binding a port --
that seam is where the missing `oauth_config` wiring hid, leaving `/register`,
`/authorize`, `/token` and `/bridge` refusing everyone while discovery kept
answering.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

import oauth
import webhooks


def version() -> str:
    """The running build, read at runtime.

    The predecessor's primary staleness detector, and it caught a real one: the
    MCP tool contract is pinned per client at connection time, so an operator
    sees a feature deployed while the attached session sees a schema without it
    -- and both are looking at the truth.
    """
    return os.environ.get("AGENT_BUS_CLOUD_VERSION") or "0+unknown"


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

    A key ending in `*` matches any redirect URI sharing its prefix -- see
    `oauth.address_for_redirect`. Needed for ChatGPT specifically: it mints a
    fresh `.../connector/oauth/<id>` callback per connector rather than
    reusing one fixed one the way Claude's and Grok's do, so a plain string
    key would need a new deploy for every connector it ever creates.
    """

    key: bytes
    allowlist: dict[str, str]
    passphrase: str


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
    # Absent is the ordinary case: a deployment with no webhook peer answers
    # 404 on the route and needs no secret to do it.
    webhook_secrets: dict[str, str]


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
                        or None,
                        webhook_secrets=webhooks.secrets_from_env(os.environ))
