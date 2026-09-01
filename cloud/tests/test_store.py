"""The rules the store enforces, and then the store actually enforcing them."""

import time

import pytest
import store

# ------------------------------------------------- pure, no emulator required

def test_the_queue_name_is_the_address_plus_a_direction():
    assert store.queue("desktop", "claude", "inbox") == "desktop:claude:inbox"
    assert store.queue("webhook", "github", "outbox") == "webhook:github:outbox"


def test_a_direction_that_is_neither_is_refused():
    with pytest.raises(ValueError, match="inbox or outbox"):
        store.queue("desktop", "claude", "sideways")


def test_the_server_stamps_the_clock_it_trusts():
    """A sender's clock is never trusted. A skewed one would otherwise write a
    message that outlives the TTL or expires on arrival, invisibly from here."""
    m = store.stamp({"text": "hi", "ts": 0, "expireAt": 0}, now=1000.0)
    assert m["ts"] == 1000.0
    assert m["expireAt"] == 1000.0 + store.TTL_SECONDS


def test_expired_messages_are_filtered_not_merely_collected():
    """Firestore TTL is a garbage collector: deletion is best-effort, typically
    within 24h. A document past its time is still readable, and would be handed
    to a peer as though current."""
    now = 1000.0
    kept = store.live([{"expireAt": now + 1}, {"expireAt": now - 1}, {}], now=now)
    assert kept == [{"expireAt": now + 1}]


@pytest.mark.parametrize("message, why", [
    ({"to": "x", "text": "y"}, "from is required"),
    ({"to": "x", "text": "y", "from": "  "}, "from is required"),
    ({"text": "y", "from": "x"}, "to is required"),
    ({"to": "x", "from": "y", "text": "z" * 40_000}, "the limit is"),
])
def test_a_message_the_bus_would_not_accept_is_refused_here_too(message, why):
    """"The bus adopts the narrowest constraint" cuts both ways: a desktop
    mailbox is the one genuinely unread-accumulating kind."""
    with pytest.raises(store.Rejected, match=why):
        store.check(message, unread_count=0)


def test_a_queue_nobody_drains_stops_accepting():
    with pytest.raises(store.Rejected, match="Nobody is draining"):
        store.check({"to": "x", "text": "y", "from": "z"},
                    unread_count=store.MAX_UNREAD)


# ------------------------------------------------------ against the emulator

@pytest.mark.emulator
def test_a_message_survives_a_round_trip(firestore, address):
    q = store.queue(*address, "inbox")
    mid = firestore.write(q, {"to": "desktop:claude", "text": "review this",
                              "summary": "branch", "from": "labkit-dev"})
    got = firestore.read(q)
    assert [m["id"] for m in got] == [mid]
    assert got[0]["text"] == "review this"


@pytest.mark.emulator
def test_acking_is_by_id_and_only_those_ids(firestore, address):
    q = store.queue(*address, "inbox")
    a = firestore.write(q, {"to": "d", "text": "one", "from": "x"})
    b = firestore.write(q, {"to": "d", "text": "two", "from": "x"})

    assert firestore.ack(q, [a]) == 1
    left = [m["id"] for m in firestore.read(q, unread_only=True)]
    assert left == [b], "acking one must not consume the other"


@pytest.mark.emulator
def test_read_one_fetches_a_body_only_from_the_caller_s_own_queue(firestore, address):
    """`read_message` hands a connector one whole message by id, so the id is
    the only thing standing between a caller and a body. It is scoped to the
    queue the token resolved to: an id that exists, but in someone else's
    queue, is *not found* rather than fetched."""
    mine = store.queue(*address, "inbox")
    theirs = store.queue("desktop", "someone-else", "inbox")
    mid = firestore.write(theirs, {"to": "d", "text": "not for you", "from": "x"})

    assert firestore.read_one(theirs, mid)["text"] == "not for you"
    assert firestore.read_one(mine, mid) is None, "an id must not cross queues"


@pytest.mark.emulator
def test_a_roster_that_stops_being_republished_empties_itself(firestore, address):
    """Bridge liveness needs no heartbeat: the snapshot carries the ordinary
    TTL, so a bridge that stops running stops refreshing it and list_agents
    goes empty on its own."""
    who = ":".join(address)
    firestore.publish_roster(who, [{"name": "labkit-dev"}])
    assert firestore.roster(who) == [{"name": "labkit-dev"}]

    stale = {"agents": [{"name": "labkit-dev"}], "expireAt": time.time() - 1}
    firestore._db.collection("roster").document(who).set(stale)
    assert firestore.roster(who) == []


@pytest.mark.emulator
def test_a_registered_client_survives(firestore, address):
    """The restart case, against the real store."""
    cid = f"c-{address[1]}"
    firestore.put_client({"client_id": cid, "redirect_uris": ["https://x/cb"]})
    assert firestore.client(cid)["redirect_uris"] == ["https://x/cb"]
    assert firestore.client("never-registered") is None


@pytest.mark.emulator
def test_a_code_can_only_be_taken_once(firestore, address):
    """The property the stub cannot prove: read-and-delete has to be one step,
    or two redemptions racing would both see the code."""
    code = f"code-{address[1]}"
    firestore.put_code(code, {"client_id": "c1", "expiresAt": time.time() + 60})

    assert firestore.take_code(code)["client_id"] == "c1"
    assert firestore.take_code(code) is None


# ------------------------------------------------ the TTL field, in the store


def _raw(fs, path):
    """The document as Firestore holds it, not as the store hands it back.

    The conversion under test is at the boundary, so reading through `read()`
    would convert it straight back and assert nothing.
    """
    return fs._db.document(path).get().to_dict() or {}


@pytest.mark.parametrize("collection", ["messages", "roster", "oauth_codes"])
def test_the_ttl_field_is_a_timestamp_not_a_number(firestore, address, collection):
    """Firestore's TTL requires a `Date and time` field. A float never matches,
    so the policy silently collects nothing while `live()` hides the evidence
    from every read -- a service that looks correct and grows without bound.
    """
    from datetime import datetime

    kind, name = address
    if collection == "messages":
        q = store.queue(kind, name, store.INBOX)
        mid = firestore.write(q, {"to": "x", "from": "y", "text": "z"})
        doc = _raw(firestore, f"messages/{q}/items/{mid}")
    elif collection == "roster":
        addr = f"{kind}:{name}"
        firestore.publish_roster(addr, [{"name": "a", "kind": "other"}])
        doc = _raw(firestore, f"roster/{addr}")
    else:
        code = f"c-{name}"
        firestore.put_code(code, {"client_id": "x", "expiresAt": time.time() + 60})
        doc = _raw(firestore, f"oauth_codes/{code}")

    assert isinstance(doc["expireAt"], datetime), (
        f"{collection} wrote {type(doc['expireAt']).__name__}; "
        "a TTL policy will never match it")


def test_the_wire_stays_a_number(firestore, address):
    """Only Firestore wants a datetime. `live()` compares against `time.time()`
    and the `/bridge` pull path calls `json.dumps` on what `read()` returns --
    a datetime breaks both, so the conversion must not leak past the boundary.
    """
    import json

    kind, name = address
    q = store.queue(kind, name, store.INBOX)
    firestore.write(q, {"to": "x", "from": "y", "text": "z"})
    msgs = firestore.read(q)
    assert isinstance(msgs[0]["expireAt"], float)
    json.dumps(msgs)  # raises TypeError on a datetime


def test_a_roster_read_back_still_expires(firestore, address):
    """`roster()` filters through `live()`, which needs the number back."""
    kind, name = address
    addr = f"{kind}:{name}"
    firestore.publish_roster(addr, [{"name": "a", "kind": "other"}])
    assert firestore.roster(addr) == [{"name": "a", "kind": "other"}]


# ------------------------------------------- which database (#staging)


def test_the_store_targets_the_default_database_unless_told_otherwise():
    """Production is `(default)` and must stay so without configuration.

    A named database is how a staging service shares a project with production
    without sharing its data. Getting the default wrong would point production
    at a database that does not exist.
    """
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

    store.Firestore(client=None, project="p", _client_factory=FakeClient)
    assert seen["project"] == "p"
    assert seen.get("database") is None, seen


def test_a_named_database_is_passed_through():
    """Staging in the same project writes to its own database or it is not
    staging at all -- it is a second front end onto production's data."""
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

    store.Firestore(client=None, project="p", database="staging",
                    _client_factory=FakeClient)
    assert seen["database"] == "staging"
