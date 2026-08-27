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
import oauth
import pytest

KEY = b"\x04" * 32
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
        store, "https://test.invalid", verify=app.bearer_verifier(KEY),
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
    return oauth.mint_bridge_token(ADDRESS, KEY)


# ---------------------------------------------------------------- the mirror

def test_push_fills_the_inbox_the_connector_reads(server, token):
    base, store = server
    status, body = _bridge(base, "push", token,
                           message={"id": "local-1", "from": "labkit-dev",
                                    "text": "review this", "summary": "branch"})
    assert status == 200, body
    assert [m["text"] for m in store.queues["desktop:claude:inbox"]] == ["review this"]


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
    list-agents reads it."""
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
    stale = oauth.mint_bridge_token(ADDRESS, KEY, ttl=-1)
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
