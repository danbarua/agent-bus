"""`/bridge`: the mirror of the connector's tools, and why it is not those tools.

A connector's `read` drains `<address>:inbox` -- the queue the **bridge** fills.
Its `write` fills `<address>:outbox` -- the queue the bridge drains. The two
sides want opposite ends of the same pipes, so they cannot share verbs.

They could have shared an endpoint with the meaning flipped by role, and that
was rejected: the MCP contract is frozen precisely so that our own needs can
never force a change to the surface a connector has cached. A separate endpoint
keeps that promise cheap to keep.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import app
import logs
import oauth
import pytest

KEY = b"\x04" * 32
ISSUER = "https://test.invalid"
ADDRESS = "desktop:claude"


class StubStore:
    def __init__(self):
        self.queues: dict[str, list[dict]] = {}
        self.rosters: dict[str, list[dict]] = {}

    def write(self, q, message):
        message = {**message, "id": message.get("id") or f"m{len(self.queues.get(q, []))}"}
        self.queues.setdefault(q, []).append(message)
        return message["id"]

    def read(self, q, unread_only=True):
        return [m for m in self.queues.get(q, []) if not (unread_only and m.get("read"))]

    def read_one(self, q, message_id):
        # Scoped to `q`, like the real one: an id in another peer's queue is
        # not found rather than fetched, and a stub that ignored the queue
        # would make the two-queue search below untestable.
        return next((m for m in self.queues.get(q, []) if m["id"] == message_id), None)

    def ack(self, q, ids):
        n = 0
        for m in self.queues.get(q, []):
            if m["id"] in ids:
                m["read"] = True
                n += 1
        return n

    def publish_roster(self, address, agents):
        self.rosters[address] = agents

    def roster(self, address):
        return self.rosters.get(address, [])


@pytest.fixture
def server():
    store = StubStore()
    handler = app.make_handler(
        store, ISSUER, verify=app.bearer_verifier(KEY),
        oauth_config=app.OAuthConfig(key=KEY, allowlist={}, passphrase="x"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", store
    httpd.shutdown()


def _bridge(base, op, token, **body):
    payload = json.dumps({"op": op, **body}).encode()
    req = urllib.request.Request(f"{base}/bridge", data=payload,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture
def token():
    return oauth.mint_bridge_token(ADDRESS, KEY, ISSUER)


# ---------------------------------------------------------------- the mirror

def test_push_fills_the_inbox_the_connector_reads(server, token):
    base, store = server
    status, body = _bridge(base, "push", token,
                           message={"id": "local-1", "from": "labkit-dev",
                                    "text": "review this", "summary": "branch"})
    assert status == 200, body
    assert [m["text"] for m in store.queues["desktop:claude:inbox"]] == ["review this"]


def test_the_recipient_is_the_token_never_the_body(server, token):
    """`to` is the address on the token. The queue already *is* the recipient,
    so a bridge naming one would only ever be naming someone else's."""
    base, store = server
    status, _ = _bridge(base, "push", token,
                        message={"id": "local-1", "from": "labkit-dev",
                                 "text": "hi", "to": "somebody:else"})
    assert status == 200
    assert store.queues["desktop:claude:inbox"][0]["to"] == ADDRESS


def test_the_local_id_travels_as_the_dedupe_key(server, token):
    """A forward retried after a crash must be absorbed rather than surface
    twice in someone's chat."""
    base, store = server
    _bridge(base, "push", token, message={"id": "local-1", "from": "x", "text": "y"})
    assert store.queues["desktop:claude:inbox"][0]["id"] == "local-1"


def test_pull_drains_the_outbox_the_connector_writes(server, token):
    base, store = server
    store.write("desktop:claude:outbox", {"id": "r1", "to": "labkit-dev",
                                          "text": "reviewed", "from": "desktop:claude"})
    status, body = _bridge(base, "pull", token)
    assert status == 200
    assert [m["id"] for m in body["messages"]] == ["r1"]


def test_ack_consumes_from_the_outbox(server, token):
    base, store = server
    store.write("desktop:claude:outbox", {"id": "r1", "text": "x"})
    _bridge(base, "ack", token, ids=["r1"])
    assert _bridge(base, "pull", token)[1]["messages"] == []


def test_the_roster_is_published_not_queried(server, token):
    """Nothing can reach into the laptop, so the bridge pushes a snapshot and
    list_agents reads it."""
    base, store = server
    _bridge(base, "roster", token, agents=[{"name": "labkit-dev", "kind": "other"}])
    assert store.rosters[ADDRESS] == [{"name": "labkit-dev", "kind": "other"}]


# ------------------------------------------------------------ the boundary

def test_a_connector_token_cannot_use_the_bridge_endpoint(server):
    """The privilege that makes a separate endpoint worth having.

    A connector's own access token is valid and names the same address. Without
    this check it could `push` into its own inbox -- forging mail that appears
    to have come from the team.
    """
    base, store = server
    connector = oauth.mint_access(ADDRESS, KEY, client_id="some-connector")
    status, body = _bridge(base, "push", connector,
                           message={"id": "x", "from": "y", "text": "z"})
    assert status == 403, body
    assert store.queues == {}


def test_no_token_is_refused(server):
    base, _ = server
    assert _bridge(base, "pull", None)[0] == 401


def test_an_expired_bridge_token_is_refused(server):
    base, _ = server
    stale = oauth.mint_bridge_token(ADDRESS, KEY, ISSUER, ttl=-1)
    assert _bridge(base, "pull", stale)[0] == 401


def test_the_address_comes_from_the_token_not_the_request(server, token):
    """A bridge cannot ask to be someone else. There is no address field."""
    base, store = server
    _bridge(base, "push", token, address="desktop:chatgpt",
            message={"id": "x", "from": "y", "text": "z"})
    assert "desktop:chatgpt:inbox" not in store.queues
    assert "desktop:claude:inbox" in store.queues


def test_an_unknown_op_is_refused(server, token):
    base, _ = server
    assert _bridge(base, "delete-everything", token)[0] == 400


# ------------------------------------------------------------------ the record


def _lines(caplog, message):
    return [r for r in caplog.records if r.getMessage() == message]


def test_an_empty_poll_still_leaves_a_record(server, token, caplog):
    """A bridge polls every two minutes forever, so "nothing waiting" is the
    common case -- and it logged *nothing at all*, because the only line sat
    inside the per-message loop. A bridge that had stopped polling was then
    indistinguishable from a healthy idle one.

    DEBUG because of that same frequency: the record has to exist, and it must
    not be the thing that fills the log."""
    base, _ = server
    with caplog.at_level("DEBUG", logger=logs.LOGGER_NAME):
        status, body = _bridge(base, "pull", token)
    assert status == 200 and body["messages"] == []
    pulls = _lines(caplog, "bridge pull")
    assert pulls, "an empty poll left no record in any severity"
    assert pulls[0].levelname == "DEBUG"
    assert pulls[0].count == 0


def test_a_poll_that_carries_mail_is_INFO(server, token, caplog):
    """The case an operator is looking for must be visible without turning
    DEBUG on, which is the whole point of splitting the two."""
    base, store = server
    store.write("desktop:claude:outbox", {"to": "labkit-dev", "text": "hi", "from": "d"})
    with caplog.at_level("DEBUG", logger=logs.LOGGER_NAME):
        status, body = _bridge(base, "pull", token)
    assert status == 200 and len(body["messages"]) == 1
    pulls = _lines(caplog, "bridge pull")
    assert pulls and pulls[0].levelname == "INFO"
    assert pulls[0].count == 1


def test_the_roster_publish_leaves_a_record(server, token, caplog):
    """`list_agents` rests on it, and it logged nothing at any level -- so an
    empty roster and a bridge that had stopped publishing looked identical from
    outside, which is the confusion `list_agents`'s own empty-case text exists
    to explain."""
    base, _ = server
    with caplog.at_level("DEBUG", logger=logs.LOGGER_NAME):
        _bridge(base, "roster", token, agents=[{"name": "a", "kind": "other"}])
    rosters = _lines(caplog, "bridge roster")
    assert rosters and rosters[0].count == 1


# ------------------------------------------------------------- where it got to


def test_read_finds_a_message_in_the_inbox_and_says_which_queue(server, token):
    """#219. *Which* queue holds it is the diagnostic: `inbox` means the bridge
    pushed it and the connector has not looked."""
    base, _ = server
    _bridge(base, "push", token, message={"id": "m-1", "from": "labkit-dev",
                                          "text": "t", "summary": "s"})
    status, body = _bridge(base, "read", token, message_id="m-1")
    assert status == 200, body
    assert body["queue"] == "inbox"
    assert body["message"]["text"] == "t"


def test_read_finds_a_message_in_the_outbox(server, token):
    """`outbox` means the peer wrote it and this bridge has not pulled it yet.
    Both queues are searched, so a send-only peer needs no special case -- its
    inbox is simply empty."""
    base, store = server
    store.write("desktop:claude:outbox", {"to": "labkit-dev", "text": "r", "from": "d"})
    mid = store.queues["desktop:claude:outbox"][0]["id"]

    status, body = _bridge(base, "read", token, message_id=mid)
    assert status == 200 and body["queue"] == "outbox", body


def test_read_does_not_consume(server, token):
    """A query. An operator asking where a message went must not be the reason
    it stops being redelivered -- a `read` that acked would deliver the answer
    and destroy the thing it answered about."""
    base, store = server
    store.write("desktop:claude:outbox", {"to": "x", "text": "r", "from": "d"})
    mid = store.queues["desktop:claude:outbox"][0]["id"]

    _bridge(base, "read", token, message_id=mid)
    _, pulled = _bridge(base, "pull", token)
    assert [m["id"] for m in pulled["messages"]] == [mid], (
        "the read consumed it: a later pull no longer sees it"
    )


def test_read_of_an_unknown_id_is_an_answer_not_an_error(server, token):
    """"Delivered and expired, or never arrived" is a real answer."""
    status, body = _bridge(server[0], "read", token, message_id="never-existed")
    assert status == 200, body
    assert body == {"queue": None, "message": None}


def test_read_needs_an_id(server, token):
    status, body = _bridge(server[0], "read", token)
    assert status == 400, body
