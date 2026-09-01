"""Firestore, and the rules the store enforces rather than trusts.

Split deliberately in two. Everything above `Firestore` is pure and importable
without the client library, so the dispatch tests can exercise the rules with a
stub; only `Firestore` itself needs the emulator. A green suite that silently
skipped the store would be the "tested nothing" failure the compose file already
warns about, so `tests/test_store.py` names the emulator command in its skip.

**Queues are `<kind>:<name>:<direction>`, named from the *external* peer's point
of view** -- `get_inbox` drains that peer's inbox, `send_message` fills its
outbox. Named from the bus's side instead, the contract's verbs and the
collections would disagree, which reads fine in a schema and confuses everyone
at 2am. A source that only ever sends has one queue and no special case:

    desktop:claude:inbox     FOR Claude Desktop, FROM the team
    desktop:claude:outbox    FROM Claude Desktop, TO the team
    webhook:github:outbox    FROM GitHub, TO the team   (there is no inbox)
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
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

    A TTL policy is wanted on `expireAt` in three of those -- collection groups
    `items`, `roster` and `oauth_codes`. Not `oauth_clients`: ChatGPT caches its
    `client_id` and reuses it indefinitely, so expiring a registration would
    orphan a live connector. That list is what the terraform declares.
    """

    def __init__(self, client: Any = None, project: str | None = None,
                 database: str | None = None,
                 _client_factory: Any = None) -> None:
        """`database` names a Firestore database other than `(default)`.

        This is how a staging service shares a project with production without
        sharing its data. The billing account is at its five-project quota, so
        a second project is not available -- and a second service pointed at
        the same database would not be staging, it would be a second front end
        onto production's records.

        None means `(default)`, which is what production runs and what every
        existing deployment expects. `_client_factory` exists so that choice
        is testable without a Firestore.
        """
        if client is None:
            factory = _client_factory
            if factory is None:
                # Imported here, not at module scope: the rules above must stay
                # importable in a test process that has no client library and no
                # emulator.
                from google.cloud import firestore

                factory = firestore.Client
            client = factory(project=project, database=database)
        self._db = client

    # ------------------------------------------------------ the TTL boundary
    #
    # Firestore's TTL matches a `Date and time` field and nothing else, so a
    # float `expireAt` means the policy collects **nothing** -- invisibly, since
    # `live()` filters expired documents out of every read. The service looks
    # correct while the collection grows without bound.
    #
    # The conversion lives here and only here. Above this class everything stays
    # a number: `live()` compares against `time.time()`, and `read()` is fed
    # straight to `json.dumps` on the /bridge pull path, which a datetime
    # breaks.

    @staticmethod
    def _for_firestore(doc: dict[str, Any]) -> dict[str, Any]:
        at = doc.get("expireAt")
        if isinstance(at, (int, float)):
            return {**doc, "expireAt": datetime.fromtimestamp(at, tz=UTC)}
        return doc

    @staticmethod
    def _from_firestore(doc: dict[str, Any]) -> dict[str, Any]:
        at = doc.get("expireAt")
        if isinstance(at, datetime):
            return {**doc, "expireAt": at.timestamp()}
        return doc

    def _items(self, q: str) -> Any:
        return self._db.collection("messages").document(q).collection("items")

    def unread_count(self, q: str) -> int:
        return sum(1 for d in self._items(q).stream() if not (d.to_dict() or {}).get("read"))

    def write(self, q: str, message: dict[str, Any]) -> str:
        """Stamp, check, store. Raises `Rejected` before writing anything."""
        check(message, self.unread_count(q))
        stamped = stamp(message)
        self._items(q).document(stamped["id"]).set(self._for_firestore(stamped))
        return stamped["id"]

    def read(self, q: str, unread_only: bool = True) -> list[dict[str, Any]]:
        msgs = live([self._from_firestore(d.to_dict() or {})
                     for d in self._items(q).stream()])
        if unread_only:
            msgs = [m for m in msgs if not m.get("read")]
        return sorted(msgs, key=lambda m: m.get("ts") or 0)

    def read_one(self, q: str, message_id: str) -> dict[str, Any] | None:
        """One message, whole, by id -- or None if `q` does not hold it.

        Scoped to the caller's own queue on purpose. An id belonging to another
        peer is *not found* rather than fetched, so a guessed or overheard id
        reaches nothing. Expired messages are None too, by the same `live()`
        the listing uses: a body outliving its own summary would be the more
        surprising of the two.
        """
        doc = self._items(q).document(message_id).get()
        if not doc.exists:
            return None
        alive = live([self._from_firestore(doc.to_dict() or {})])
        return alive[0] if alive else None

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
        refreshing it and `list_agents` empties by itself. Bridge liveness needs
        no separate heartbeat, and a stale roster self-heals rather than needing
        invalidation.
        """
        self._db.collection("roster").document(address).set(self._for_firestore({
            "agents": agents,
            "expireAt": time.time() + TTL_SECONDS,
        }))

    def roster(self, address: str) -> list[dict[str, Any]]:
        snap = self._db.collection("roster").document(address).get()
        doc = self._from_firestore((snap.to_dict() or {}) if snap.exists else {})
        if not live([doc]):
            return []
        return doc.get("agents") or []

    # -------------------------------------------------------------- OAuth

    def put_client(self, record: dict[str, Any]) -> None:
        """Registered clients are persisted, and that was found rather than
        anticipated: ChatGPT caches the `client_id` and reuses it against
        /authorize instead of re-registering, so an in-memory registry orphaned
        a live client on restart."""
        self._db.collection("oauth_clients").document(record["client_id"]).set(record)

    def client(self, client_id: str) -> dict[str, Any] | None:
        snap = self._db.collection("oauth_clients").document(client_id).get()
        return (snap.to_dict() or {}) if snap.exists else None

    def put_code(self, code: str, record: dict[str, Any]) -> None:
        # `expireAt` as well as `expiresAt`: the first is Firestore's TTL field
        # so a code nobody redeems is collected, the second is what redeem_code
        # checks. The collector is not a filter -- see `live`.
        self._db.collection("oauth_codes").document(code).set(
            self._for_firestore({**record, "expireAt": record["expiresAt"]}))

    def take_code(self, code: str) -> dict[str, Any] | None:
        """Read and consume, in one transaction.

        Single use is the replay defence, so the read and the delete must not
        be separable: two redemptions racing a plain read-then-delete would
        both see the code. Firestore gives us the transaction; using it is
        cheaper than reasoning about whether single-user means single-threaded.
        """
        from google.cloud import firestore

        ref = self._db.collection("oauth_codes").document(code)

        @firestore.transactional
        def _take(txn: Any) -> dict[str, Any] | None:
            snap = ref.get(transaction=txn)
            if not snap.exists:
                return None
            txn.delete(ref)
            return self._from_firestore(snap.to_dict() or {})

        return _take(self._db.transaction())
