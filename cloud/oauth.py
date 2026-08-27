"""A single-user OAuth 2.1 authorization server: DCR, PKCE S256, and nothing else.

Ported from the predecessor's `oauth.ts` with its reasoning intact, because
almost every decision in it was made by watching a real connector fail. Scoped
deliberately narrow: one consenting user, public clients only, no client
secrets, PKCE required.

**The consent page is the one thing deliberately not ported.** Its `/authorize`
was a single Allow button with no login, and it survived on hostname obscurity.
Certificate Transparency logs are world-readable and indexed, so the hostname is
enumerable by strangers within minutes of the certificate issuing -- it was
never a defence, there or here. So consent needs *both* a redirect-URI allowlist
and a passphrase: the allowlist stops a stranger having the code delivered
somewhere they control, and the passphrase stops them completing a flow at all.

Tokens are stateless -- payload plus HMAC, both base64url -- so access and
refresh survive a restart with nothing persisted. Known limitation, inherited
and accepted: refresh "rotation" issues a new token and cannot revoke the old
one without a store. Acceptable for a single-user tool, and written down rather
than discovered.

Clients and codes *are* persisted, and that was found rather than anticipated:
ChatGPT caches the `client_id` from an earlier registration and reuses it
directly against `/authorize` instead of re-registering, so an in-memory
registry orphaned a live client on restart and broke reconnection with
"Invalid authorization request".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

# Matches Claude's own auth-code lifetime expectations. Short because a code is
# a bearer credential in a URL, and URLs end up in logs and history.
CODE_TTL_SECONDS = 60
ACCESS_TTL_SECONDS = 60 * 60
REFRESH_TTL_SECONDS = 60 * 60 * 24 * 30


class Refused(Exception):
    """The request is not going to be honoured. Carries a reason for the log,
    never for the client: an error that distinguishes "no such code" from
    "wrong verifier" tells an attacker which half to keep guessing."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ------------------------------------------------------------------- tokens

def sign_token(claims: dict[str, Any], key: bytes) -> str:
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str, key: bytes, now: float | None = None) -> dict[str, Any] | None:
    """Claims, or None. Never raises: anyone can send an Authorization header,
    and a malformed one is a 401 rather than a 500."""
    parts = (token or "").split(".")
    if len(parts) != 2:
        return None
    payload, sig = parts
    expected = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    # Constant time. The comparison is the whole security property.
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        claims = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        return None
    if not isinstance(claims, dict):
        return None
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp < (time.time() if now is None else now):
        return None
    return claims


# --------------------------------------------------------------------- PKCE

def pkce_challenge(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode()).digest())


def pkce_matches(verifier: str, challenge: str) -> bool:
    return hmac.compare_digest(pkce_challenge(verifier), challenge or "")


# ---------------------------------------------------------------------- DCR

def register_client(store: Any, body: dict[str, Any],
                    allowlist: list[str]) -> dict[str, Any]:
    """RFC 7591, with the allowlist applied *before* anything is stored.

    Refusing after writing would leave a registered client whose redirect the
    server would then have to remember to reject, which is one place too many
    for the check to live.
    """
    uris = body.get("redirect_uris") or []
    if not isinstance(uris, list) or not uris:
        raise Refused("redirect_uris is required")
    for uri in uris:
        if uri not in allowlist:
            raise Refused(f"redirect_uri not permitted: {uri}")
    record = {
        "client_id": secrets.token_urlsafe(24),
        "redirect_uris": list(uris),
        "created": time.time(),
    }
    store.put_client(record)
    return record


# -------------------------------------------------------------------- codes

def issue_code(store: Any, *, client_id: str, redirect_uri: str,
               code_challenge: str, resource: str, scope: str) -> str:
    code = secrets.token_urlsafe(24)
    store.put_code(code, {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "resource": resource,
        "scope": scope,
        "expiresAt": time.time() + CODE_TTL_SECONDS,
    })
    return code


def redeem_code(store: Any, *, code: str, verifier: str, redirect_uri: str,
                client_id: str, now: float | None = None) -> dict[str, Any]:
    """Consume a code, or refuse. Every check here is one an attacker fails.

    `take_code` consumes on read, so replay is stopped by the store rather than
    by remembering to delete at each call site -- there is only one path out of
    here that does not raise.
    """
    now = time.time() if now is None else now
    record = store.take_code(code)
    if record is None:
        raise Refused("no such code, or it has been used already")
    if (record.get("expiresAt") or 0) < now:
        raise Refused("code expired")
    if record.get("client_id") != client_id:
        raise Refused("code was issued to another client")
    if record.get("redirect_uri") != redirect_uri:
        raise Refused("redirect_uri does not match the one consented to")
    if not pkce_matches(verifier, record.get("code_challenge") or ""):
        raise Refused("verifier does not match the challenge")
    return record


# ------------------------------------------------------------------ consent

def consent_granted(given: str | None, expected: str) -> bool:
    """The half the predecessor did not have.

    Constant time, and an empty expected passphrase grants nothing -- a
    misconfigured deployment must fail closed, not open to everyone who found
    the hostname in a CT log.
    """
    if not expected or not given:
        return False
    return hmac.compare_digest(given, expected)


# ------------------------------------------------------------------ minting

def mint_access(address: str, key: bytes, client_id: str = "",
                ttl: int = ACCESS_TTL_SECONDS) -> str:
    return sign_token({"sub": "owner", "address": address, "client_id": client_id,
                       "kind": "access", "scope": "mcp",
                       "exp": time.time() + ttl}, key)


def mint_refresh(address: str, key: bytes, client_id: str = "") -> str:
    return sign_token({"sub": "owner", "address": address, "client_id": client_id,
                       "kind": "refresh", "scope": "mcp",
                       "exp": time.time() + REFRESH_TTL_SECONDS}, key)


def mint_bridge_token(address: str, key: bytes,
                      ttl: int = REFRESH_TTL_SECONDS) -> str:
    """A long-lived access token for a bridge, minted out of band.

    The bridge is not a third-party client and does not do the dance: the OAuth
    machinery exists solely for the two connectors that demand it. One header,
    one file at `~/.agent-bus/cloud-token` (0600), no dependencies.

    Deliberately `kind: access` rather than a third kind. A bridge presents it
    exactly as a connector presents its own, so there is one verification path
    rather than two -- and one path is the one that gets tested.
    """
    return mint_access(address, key, client_id="bridge", ttl=ttl)
