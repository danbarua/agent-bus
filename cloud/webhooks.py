"""Turning a signed GitHub delivery into a message on a queue.

Two pure functions and no HTTP, so the parts with judgement in them are
testable without binding a port: verification, and the mail-shaping.

**GitHub-specific on purpose, not a generic "verify any webhook" layer.**
Every provider signs differently -- header name, algorithm, what exactly is
covered -- so each gets its own verifier rather than one abstraction that fits
none of them properly. Carried from the predecessor (`c2c-mcp/src/proxy.ts`),
along with everything else here that reads like it was learned rather than
designed, because it was.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from typing import Any

# GitHub's own header and prefix. Both fixed by them, neither ours to choose.
SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"
_PREFIX = "sha256="

# What the *store* can hold, not what GitHub will send. GitHub's own limit is
# 25 MB; a Firestore document is capped near 1 MiB and the body is one field of
# it. So this is deliberately well under, and a delivery above it is refused
# visibly rather than written and truncated.
MAX_EVENT_BYTES = 256 * 1024

# The sender on the queue: provenance, not a correspondent.
#
# `from` became a reply address in #245 and the envelope leans on it (#246) --
# so a value here has to be one of two honest things, and this is the ingress,
# which knows only that GitHub sent it. The *bridge* authors the message an
# agent finally sees, and there `from` is `webhook:github`, which is a real
# address: replying to it is how you subscribe.
#
# The predecessor reserved `no-reply` for exactly this class -- "machine-
# generated notifications that nobody replies to" -- and its lesson holds even
# though the name does not: an event source is not a peer.
SOURCE = "github"


def verify_github(body: bytes, signature: str, secret: str) -> str:
    """`"ok"`, or a reason. Never a bare bool.

    "Rejected" alone cannot tell an unset secret from a wrong one, and those
    have completely different fixes -- an operator left with a boolean reruns
    the delivery to learn nothing again. The reason names the failure without
    echoing any of the caller's bytes back into a log.

    Over the **raw body**, which is why this takes bytes and why the caller
    must not have parsed them yet: re-serialising a parsed body does not
    reproduce what was signed, because key order, whitespace and unicode
    escaping all differ.
    """
    if not secret:
        return "no-secret"
    if not signature.startswith(_PREFIX):
        return "malformed-header"
    expected = _PREFIX + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # Length first: `compare_digest` is constant-time for equal lengths and a
    # mismatch here is a malformed header rather than a wrong digest, which is
    # a different message to the operator.
    if len(expected) != len(signature):
        return "malformed-header"
    return "ok" if hmac.compare_digest(expected, signature) else "digest-mismatch"


def as_message(address: str, event: str, delivery: str, body: bytes) -> dict[str, Any]:
    """One delivery, mail-shaped -- the thing a bridge pulls like any other.

    **The delivery id becomes the message id.** The store keys documents by id,
    so GitHub redelivering an event overwrites rather than duplicates. That is
    idempotency bought with a choice rather than with new machinery, and it
    matters because redelivery is normal: a burst is the common case, not an
    edge -- the predecessor measured three deliveries inside one second.

    **The event type comes from a header, so it has to be carried.** Nothing in
    the body says which event this is, and a bridge that had to infer it would
    be guessing at exactly the point where it filters. It goes in `summary`,
    which is the field a listing shows.

    **The body is carried whole and unparsed.** Filtering is local (#59), so
    the cloud is a dumb ingress: it verifies, shapes, and writes. Reading the
    payload to summarise it better would be this component starting to
    understand GitHub, which is the thing the design puts downstream.
    """
    return {
        "id": delivery or None,
        "to": address,
        "from": SOURCE,
        "summary": event or "unknown",
        "text": body.decode("utf-8", "replace"),
    }


# `AGENT_BUS_CLOUD_WEBHOOK_<PEER>_SECRET`, one variable per peer.
#
# One secret holding one string, which is what every other secret in this
# deployment is. The first version made it a single JSON document keyed by
# peer, which bought "a second source needs no code change" -- and this buys
# the same thing, for the price of a variable rather than a parser, a failure
# mode for malformed JSON, and a secret shaped unlike its neighbours.
#
# Discovering peers from the environment rather than from a list keeps that
# property: adding one is a secret and a mount, both terraform.
_SECRET_VAR = re.compile(r"^AGENT_BUS_CLOUD_WEBHOOK_([A-Z0-9]+)_SECRET$")


def secrets_from_env(env: Mapping[str, str]) -> dict[str, str]:
    """`{"github": "<secret>"}` -- every peer the environment names.

    An empty value is not a peer. A mounted-but-unset secret would otherwise
    register a name whose every delivery fails its HMAC, which reads as a wrong
    secret rather than an absent one -- and those have different fixes.
    """
    found = {}
    for key, value in env.items():
        m = _SECRET_VAR.match(key)
        if m and (value or "").strip():
            found[m.group(1).lower()] = value
    return found
