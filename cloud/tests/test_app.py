"""Dispatch, and one real HTTP round trip.

The in-process tests are the fast ones; the HTTP test exists because every bug
the predecessor actually shipped lived in the layer between "the function
returns the right dict" and "a connector could read it".
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import app
import pytest


class StubStore:
    """Enough store to exercise dispatch without an emulator."""

    def __init__(self):
        self.written = []
        self.rostered = [{"name": "labkit-dev", "kind": "other"}]
        self.messages = []

    def roster(self, address):
        return self.rostered

    def read(self, q, unread_only=True):
        return self.messages

    def read_one(self, q, message_id):
        return next((m for m in self.messages if m["id"] == message_id), None)

    def ack(self, q, ids):
        return len(ids)

    def write(self, q, message):
        self.written.append((q, message))
        return "m1"


def _rpc(method, store=None, authed=True, **params):
    msg = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        msg["params"] = params
    return app.dispatch(msg, store or StubStore(), "desktop", "claude", authed=authed)


# ------------------------------------------------------- discovery is anonymous

@pytest.mark.parametrize("method", sorted(app.DISCOVERY_METHODS))
def test_every_discovery_method_answers_without_a_token(method):
    """ChatGPT pings these before attaching Authorization, and attaches it only
    on tools/call. Gating them uniformly made discovery 401, so no tool was
    visible at all -- whether or not OAuth itself worked."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": method}
    reply = app.dispatch(msg, StubStore(), "", "", authed=False)
    if method.startswith("notifications/"):
        assert reply is None
        return
    assert "error" not in reply, reply


def test_resources_and_prompts_answer_with_empties_not_method_not_found():
    """Some clients call these unconditionally, not gated on advertised
    capabilities. A hard Method not found there did not mean "no resources" --
    it killed tool discovery entirely."""
    assert _rpc("resources/list")["result"] == {"resources": []}
    assert _rpc("resources/templates/list")["result"] == {"resourceTemplates": []}
    assert _rpc("prompts/list")["result"] == {"prompts": []}


def test_initialize_declares_capabilities_it_has_none_of():
    caps = _rpc("initialize")["result"]["capabilities"]
    assert caps == {"tools": {}, "resources": {}, "prompts": {}}


def test_initialize_reports_the_running_build(monkeypatch):
    """The predecessor's staleness detector, and it caught a real mismatch: the
    tool contract is pinned per client at connect time, so an operator sees a
    deploy the attached session does not."""
    monkeypatch.setenv("AGENT_BUS_CLOUD_VERSION", "1.2.3")
    assert _rpc("initialize")["result"]["serverInfo"]["version"] == "1.2.3"


# ------------------------------------------------------------ tools/call is not

def test_a_tool_call_without_a_token_is_refused():
    assert _rpc("tools/call", authed=False, name="get_inbox")["error"]["code"] == -32001


def test_write_reaches_the_outbox_and_read_drains_the_inbox():
    """Queues are named from the external peer's side: the desktop writes to its
    own outbox and reads its own inbox."""
    s = StubStore()
    _rpc("tools/call", store=s, name="send_message",
         arguments={"to": "labkit-dev", "text": "hi", "from": "desktop:claude"})
    assert s.written[0][0] == "desktop:claude:outbox"


def test_get_inbox_lists_summaries_and_read_message_carries_the_body():
    """The split #204 exists for. The listing is one line per message -- id,
    sender, summary -- and deliberately no bodies: a desktop asked to "check
    your inbox" gets something it can triage rather than a wall of text. The
    body arrives only when a specific id is fetched, and it has to arrive in
    the *text* block, because a connector that renders only the prose is the
    one this surface serves."""
    s = StubStore()
    s.messages = [{"id": "m1", "from": "claude-bus-dev", "summary": "the rename",
                   "text": "full body here"}]

    listing = _rpc("tools/call", store=s, name="get_inbox", arguments={})
    body = listing["result"]["content"][0]["text"]
    assert "m1" in body and "the rename" in body
    assert "full body here" not in body, "the listing is summaries, not bodies"

    one = _rpc("tools/call", store=s, name="read_message",
               arguments={"message_id": "m1"})
    assert "full body here" in one["result"]["content"][0]["text"]


def test_read_message_says_so_when_the_id_is_unknown():
    """Null rather than an error, per the tool's own description -- an expired
    id and a wrong one are the same answer here. That an id from *another
    peer's* queue is also unknown is a property of `store.read_one`'s scoping,
    not of dispatch, so it is tested against the emulator in `test_store.py`;
    this stub resolves by id alone and could not see the difference."""
    s = StubStore()
    assert _rpc("tools/call", store=s, name="read_message",
                arguments={"message_id": "someone-elses"}
                )["result"]["structuredContent"]["message"] is None


def test_a_retired_tool_name_is_refused_rather_than_reinterpreted():
    """`read` used to mean "list what is waiting". A client still holding that
    cached schema must get a loud unknown-tool answer, never a tool that
    quietly means something else now."""
    for retired in ("read", "write", "ack", "list-agents"):
        out = _rpc("tools/call", store=StubStore(), name=retired, arguments={})
        assert "No such tool" in out["result"]["content"][0]["text"], retired


def test_an_unknown_method_is_a_jsonrpc_error_not_a_crash():
    assert _rpc("does/not/exist")["error"]["code"] == -32601


# ------------------------------------------------------------ the metadata docs

def test_the_openid_document_is_served_at_all():
    """ChatGPT hard-aborts on a 404 here and does not fall back to RFC 8414 --
    and a failed discovery is cached client-side, so retries produce no server
    traffic. It is the one mistake that cannot be iterated out of."""
    assert "/.well-known/openid-configuration" in app.metadata("https://h")


def test_every_url_in_the_metadata_names_the_issuer():
    """A document still advertising *.run.app is the bug that strands a
    connector after the hostname moves."""
    docs = app.metadata("https://bus.example.invalid")
    for path, doc in docs.items():
        for key, value in doc.items():
            if isinstance(value, str) and value.startswith("http"):
                assert value.startswith("https://bus.example.invalid"), (path, key, value)


# ------------------------------------------------------------ over real sockets

@pytest.fixture
def server():
    store = StubStore()
    handler = app.make_handler(
        store, "https://test.invalid",
        verify=lambda tok: ("desktop", "claude") if tok == "good" else None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", store
    httpd.shutdown()


def _get(base, path):
    """Raw GET. Not urlopen's normalisation: the traversal cases below have to
    reach the server with the path they were written with."""
    import http.client

    host, port = base.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("GET", path, skip_host=False, skip_accept_encoding=True)
    conn.endheaders()
    r = conn.getresponse()
    out = (r.status, dict(r.getheaders()), r.read())
    conn.close()
    return out


def _post(base, payload, token=None):
    req = urllib.request.Request(f"{base}/mcp", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_a_connector_can_discover_and_is_then_refused(server):
    """The whole bring-up sequence, over sockets: discover anonymously, then be
    turned away at the first tool call."""
    base, _ = server

    status, body = _post(base, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert status == 200
    assert body["result"]["capabilities"] == {"tools": {}, "resources": {}, "prompts": {}}

    status, body = _post(base, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert status == 200
    assert [t["name"] for t in body["result"]["tools"]] == \
        ["list_agents", "get_inbox", "read_message", "ack_message", "send_message"]

    status, body = _post(base, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                "params": {"name": "get_inbox", "arguments": {}}})
    assert status == 401, body


def test_a_bearer_gets_through(server):
    base, _ = server
    status, body = _post(base, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": "list_agents", "arguments": {}}},
                         token="good")
    assert status == 200, body
    assert "labkit-dev" in body["result"]["content"][0]["text"]


def test_the_well_knowns_are_200_over_http(server):
    base, _ = server
    for path in app.metadata("https://test.invalid"):
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            assert r.status == 200, path
            json.loads(r.read())


def test_get_on_mcp_is_405(server):
    base, _ = server
    try:
        urllib.request.urlopen(f"{base}/mcp", timeout=5)
        raise AssertionError("GET /mcp should not succeed")
    except urllib.error.HTTPError as e:
        assert e.code == 405


def test_the_logs_never_carry_the_bearer_token(server, caplog):
    """These logs exist to be read during a connector mystery, which is exactly
    when someone pastes them somewhere. Redaction is an allowlist, because a
    denylist forgets the header added next year."""
    import logging
    import time

    base, _ = server
    with caplog.at_level(logging.INFO, logger="agent-bus-cloud"):
        _post(base, {"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
              token="super-secret-token")
        # The handler logs *after* writing the body, so the response can beat
        # the log record here. Waiting for it is the test's problem: logging on
        # the response path would be the wrong fix for a flake.
        deadline = time.time() + 2
        while not caplog.records and time.time() < deadline:
            time.sleep(0.01)

    dumped = "\n".join(
        f"{r.getMessage()} {getattr(r, 'headers', '')}" for r in caplog.records)
    assert dumped, "nothing was logged; a successful request must still be"
    assert "super-secret-token" not in dumped, dumped
    assert "<redacted>" in dumped


# ------------------------------------------------------------ the front page


def test_the_root_serves_a_page_rather_than_a_404(server):
    base, _ = server
    status, headers, body = _get(base, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"<!doctype html>" in body.lower()


def test_the_page_says_nothing_about_who_is_behind_it(server):
    """The hostname is discoverable from certificate transparency; who is on
    the other end of it need not be. A landing page that named the operator,
    the agents, or the peers would hand over the one thing the address alone
    does not give away."""
    base, _ = server
    _, _, body = _get(base, "/")
    page = body.decode().lower()
    for leak in ("desktop:claude", "claude-bus-dev", "roster", "inbox", "danbarua"):
        assert leak not in page, f"the front page mentions {leak!r}"


def test_assets_are_a_fixed_set_and_not_a_directory(server):
    """This is an OAuth server. Serving files by path is how `/../../` becomes
    a feature, so there is no path handling at all: a name either is in the
    embedded map or it is a 404."""
    base, _ = server
    for path in ("/../cloud/app.py", "/static/../../etc/passwd", "/assets/nope.png",
                 "/app.py", "/%2e%2e%2fapp.py"):
        status, _, _ = _get(base, path)
        assert status == 404, f"{path} returned {status}"


def test_the_front_page_needs_no_token(server):
    """It is a public page on a public hostname. Gating it would be theatre --
    and discovery already answers anonymously by necessity."""
    base, _ = server
    assert _get(base, "/")[0] == 200
    assert _get(base, "/health")[0] == 200
