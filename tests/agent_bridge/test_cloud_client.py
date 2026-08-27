"""The bridge against the real server, in one process.

Neither package imports the other, by design -- but *proving they agree* is
exactly what an integration test is for, so this one puts `cloud/` on the path
and drives the genuine `app` rather than a stub of it. A stub here would be a
second guess at the wire, and this session has been bitten by exactly that
often enough to stop writing them.

No emulator: the store is stubbed, because what is under test is the client and
the endpoint agreeing, not Firestore.
"""

from __future__ import annotations

import os
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

CLOUD = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "cloud")

if CLOUD not in sys.path:
    sys.path.insert(0, CLOUD)

cloud_app = pytest.importorskip("app", reason=f"no cloud server at {CLOUD}")
cloud_oauth = pytest.importorskip("oauth")

from agent_bridge.bridge import HttpCloudClient  # noqa: E402

KEY = b"\x05" * 32
ADDRESS = "desktop:claude"


class StubStore:
    def __init__(self):
        self.queues: dict[str, list[dict]] = {}
        self.rosters: dict[str, list[dict]] = {}

    def write(self, q, message):
        message = {**message, "id": message.get("id") or "m1"}
        self.queues.setdefault(q, []).append(message)
        return message["id"]

    def read(self, q, unread_only=True):
        return [m for m in self.queues.get(q, []) if not (unread_only and m.get("read"))]

    def ack(self, q, ids):
        for m in self.queues.get(q, []):
            if m["id"] in ids:
                m["read"] = True
        return len(ids)

    def publish_roster(self, address, agents):
        self.rosters[address] = agents

    def roster(self, address):
        return self.rosters.get(address, [])


@pytest.fixture
def cloud():
    store = StubStore()
    handler = cloud_app.make_handler(
        store, "https://test.invalid", verify=cloud_app.bearer_verifier(KEY),
        oauth_config=cloud_app.OAuthConfig(key=KEY, allowlist={}, passphrase="x"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = cloud_oauth.mint_bridge_token(ADDRESS, KEY)
    yield HttpCloudClient(f"http://127.0.0.1:{httpd.server_address[1]}", token), store
    httpd.shutdown()


def test_a_forwarded_message_lands_in_the_inbox(cloud):
    client, store = cloud
    client.push(ADDRESS, {"id": "local-1", "from": "labkit-dev",
                          "text": "review this", "summary": "branch"})
    assert [m["text"] for m in store.queues["desktop:claude:inbox"]] == ["review this"]


def test_a_reply_written_by_the_peer_comes_back(cloud):
    client, store = cloud
    store.write("desktop:claude:outbox", {"id": "r1", "to": "labkit-dev",
                                          "text": "reviewed", "from": "desktop:claude"})
    assert [m["id"] for m in client.pull(ADDRESS)] == ["r1"]


def test_acking_a_reply_stops_it_coming_back(cloud):
    client, store = cloud
    store.write("desktop:claude:outbox", {"id": "r1", "text": "x"})
    client.ack(ADDRESS, ["r1"])
    assert client.pull(ADDRESS) == []


def test_the_roster_reaches_the_server(cloud):
    client, store = cloud
    client.publish_roster(ADDRESS, [{"name": "labkit-dev", "kind": "other"}])
    assert store.rosters[ADDRESS] == [{"name": "labkit-dev", "kind": "other"}]


def test_a_rejected_push_raises_rather_than_reporting_success(cloud):
    """The bridge acks locally only when the forward succeeded. A client that
    swallowed a refusal would ack mail the cloud never took."""
    client, _ = cloud
    with pytest.raises(Exception, match="400|refused|Refused"):
        client.push(ADDRESS, {"id": "x", "from": "y"})  # no text: refused


def test_a_bad_token_raises(cloud):
    client, _ = cloud
    client.token = "not-a-token"
    with pytest.raises(Exception, match="401"):
        client.pull(ADDRESS)
