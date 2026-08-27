"""Firestore, and the rules the store enforces rather than trusts.

Split deliberately in two. Everything above `Firestore` is pure and importable
without the client library, so the dispatch tests can exercise the rules with a
stub; only `Firestore` itself needs the emulator. A green suite that silently
skipped the store would be the "tested nothing" failure the compose file already
warns about, so `tests/test_store.py` names the emulator command in its skip.

**Queues are `<kind>:<name>:<direction>`, named from the *external* peer's point
of view** -- `read` drains that peer's inbox, `write` fills its outbox. Named
from the bus's side instead, the frozen contract's verbs and the collections
would disagree, which reads fine in a schema and confuses everyone at 2am. A
source that only ever sends has one queue and no special case:

    desktop:claude:inbox     FOR Claude Desktop, FROM the team
    desktop:claude:outbox    FROM Claude Desktop, TO the team
    webhook:github:outbox    FROM GitHub, TO the team   (there is no inbox)
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from contract import MAX_TEXT, MAX_UNREAD

# Mirrors MESSAGE_TTL_SECONDS in the bus's store.py, deliberately and by hand:
# the two must agree, and this package must not import that one. "Messages
# expire, uniformly and briefly" is one rule, not one per side.
TTL_SECONDS = 3600

INBOX, OUTBOX = "inbox", "outbox"


def queue(kind: str, name: str, direction: str) -> str:
    if direction not in (INBOX, OUTBOX):
        raise ValueError(f"direction must be {INBOX} or {OUTBOX}, got {direction!r}")
    return f"{kind}:{name}:{direction}"


class Rejected(Exception):
    """The store refused to write. Distinct from a Firestore failure: this is
    the message being wrong, not the infrastructure."""


def stamp(message: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Add the fields the server owns.

    **The server stamps `ts` and `expireAt`; a sender's clock is never trusted.**
    A client with a skewed clock would otherwise write a message that outlives
    the TTL or expires on arrival, and neither is visible from here.
    """
    now = time.time() if now is None else now
    out = dict(message)
    out["ts"] = now
    out["expireAt"] = now + TTL_SECONDS
    out.setdefault("id", uuid.uuid4().hex)
    out.setdefault("read", False)
    return out


def check(message: dict[str, Any], unread_count: int) -> None:
    """Refuse before writing. Raises `Rejected`."""
    text = message.get("text") or ""
    if len(text) > MAX_TEXT:
        raise Rejected(f"text is {len(text)} chars; the limit is {MAX_TEXT}")
    if not (message.get("from") or "").strip():
        raise Rejected("from is required and is never inferred")
    if not (message.get("to") or "").strip():
        raise Rejected("to is required")
    if unread_count >= MAX_UNREAD:
        raise Rejected(
            f"{unread_count} unread already; the limit is {MAX_UNREAD}. "
            "Nobody is draining this queue."
        )


def live(messages: list[dict[str, Any]], now: float | None = None) -> list[dict[str, Any]]:
    """Drop what has expired.

    **Firestore TTL is a collector, not a filter.** Deletion is documented as
    best-effort, typically within 24h of `expireAt`, so a document past its time
    is still readable and would be handed to a peer as though it were current.
    Filtering here mirrors the split the bus already has locally -- `get_inbox`
    filters at 1x TTL, `reap` collects at 2x -- and makes the design correct
    even if the TTL policy never ran at all.
    """
    now = time.time() if now is None else now
    return [m for m in messages if (m.get("expireAt") or 0) > now]


class Firestore:
    """The store, for real. Everything above this is pure and testable without it.

    Collections:

        messages/<queue>/items/<id>    one subcollection per queue, so "unread
                                       in this queue" needs no composite index
        roster/<address>               the snapshot a bridge publishes
        oauth_clients/<id>             #63
        oauth_codes/<code>             #63
    """

    def __init__(self, client: Any = None, project: str | None = None) -> None:
        if client is None:
            # Imported here, not at module scope: the rules above must stay
            # importable in a test process that has no client library and no
            # emulator.
            from google.cloud import firestore

            client = firestore.Client(project=project)
        self._db = client

    def _items(self, q: str) -> Any:
        return self._db.collection("messages").document(q).collection("items")

    def unread_count(self, q: str) -> int:
        return sum(1 for d in self._items(q).stream() if not (d.to_dict() or {}).get("read"))

    def write(self, q: str, message: dict[str, Any]) -> str:
        """Stamp, check, store. Raises `Rejected` before writing anything."""
        check(message, self.unread_count(q))
        stamped = stamp(message)
        self._items(q).document(stamped["id"]).set(stamped)
        return stamped["id"]

    def read(self, q: str, unread_only: bool = True) -> list[dict[str, Any]]:
        msgs = live([d.to_dict() or {} for d in self._items(q).stream()])
        if unread_only:
            msgs = [m for m in msgs if not m.get("read")]
        return sorted(msgs, key=lambda m: m.get("ts") or 0)

    def ack(self, q: str, ids: list[str]) -> int:
        """Mark exactly these read. No 'all' mode, by contract."""
        acked = 0
        for mid in ids:
            ref = self._items(q).document(mid)
            if ref.get().exists:
                ref.update({"read": True})
                acked += 1
        return acked

    def publish_roster(self, address: str, agents: list[dict[str, Any]]) -> None:
        """Published, never queried -- nothing can reach into the laptop.

        Carries the ordinary TTL, so a bridge that stops running stops
        refreshing it and `list-agents` empties by itself. Bridge liveness needs
        no separate heartbeat, and a stale roster self-heals rather than needing
        invalidation.
        """
        self._db.collection("roster").document(address).set({
            "agents": agents,
            "expireAt": time.time() + TTL_SECONDS,
        })

    def roster(self, address: str) -> list[dict[str, Any]]:
        snap = self._db.collection("roster").document(address).get()
        doc = (snap.to_dict() or {}) if snap.exists else {}
        if not live([doc]):
            return []
        return doc.get("agents") or []
