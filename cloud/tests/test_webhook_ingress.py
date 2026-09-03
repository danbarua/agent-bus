"""The door GitHub knocks on: verify, shape, write.

Two layers, tested separately because they fail differently. `webhooks.py` is
pure and gets the judgement cases; the handler gets driven over real HTTP,
because half of what it does is statuses and headers and a function call
checks none of that.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import app
import pytest
import webhooks
from store import Rejected

SECRET = "it-was-a-quiet-tuesday"
BODY = b'{"action":"closed","pull_request":{"number":181,"merged":true}}'


def signed(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ------------------------------------------------------------- verification

def test_a_correct_signature_verifies():
    assert webhooks.verify_github(BODY, signed(BODY), SECRET) == "ok"


@pytest.mark.parametrize("signature,secret,reason", [
    (signed(BODY), "", "no-secret"),
    ("nonsense", SECRET, "malformed-header"),
    ("sha256=tooshort", SECRET, "malformed-header"),
    (signed(BODY, "the-wrong-secret"), SECRET, "digest-mismatch"),
])
def test_a_rejection_says_which_kind_it_is(signature, secret, reason):
    """A reason, never a bare bool.

    "Rejected" alone cannot tell an unset secret from a wrong one, and those
    have completely different fixes -- an operator holding a boolean reruns the
    delivery to learn nothing again. Carried from the predecessor, which had
    learned it.
    """
    assert webhooks.verify_github(BODY, signature, secret) == reason


def test_the_signature_covers_the_bytes_as_sent():
    """Re-serialising a parsed body does not reproduce what was signed: key
    order, whitespace and unicode escaping all differ. This is the one
    component that still has the original bytes, which is why #59 puts
    verification here and not behind a route that has already parsed."""
    reserialised = json.dumps(json.loads(BODY)).encode()
    assert reserialised != BODY, "the test needs a body that changes when parsed"
    assert webhooks.verify_github(reserialised, signed(BODY), SECRET) == "digest-mismatch"


def test_a_malformed_secrets_variable_configures_nothing():
    """Fails closed. Every delivery is then `no-secret`, which is the reason
    that names what to fix -- as against silently accepting unsigned ones."""
    assert webhooks.secrets_from_env("not json") == {}
    assert webhooks.secrets_from_env('["github"]') == {}
    assert webhooks.secrets_from_env('{"github": ""}') == {}
    assert webhooks.secrets_from_env('{"github": "s"}') == {"github": "s"}


# ----------------------------------------------------------- mail-shaping

def test_the_delivery_id_becomes_the_message_id():
    """The store keys documents by id, so GitHub redelivering overwrites
    rather than duplicates -- idempotency from a choice rather than from new
    machinery. Redelivery is normal, and bursts are the common case: the
    predecessor measured three deliveries inside one second."""
    m = webhooks.as_message("webhook:github", "pull_request", "d-1", BODY)
    assert m["id"] == "d-1"


def test_the_event_type_is_carried_because_the_body_does_not_have_it():
    """`X-GitHub-Event` is a header. Nothing in the payload says which event
    this is, so a bridge left to infer it would be guessing at exactly the
    point where it filters."""
    m = webhooks.as_message("webhook:github", "pull_request", "d-1", BODY)
    assert m["summary"] == "pull_request"
    assert m["text"] == BODY.decode()
    assert m["from"] == "github"
    assert m["to"] == "webhook:github"


# ------------------------------------------------------------------- HTTP

class Store:
    def __init__(self, refuse=False):
        self.written = []
        self.refuse = refuse

    def write(self, q, message):
        if self.refuse:
            raise Rejected("10000 unread already")
        self.written.append((q, message))
        return message["id"]


@pytest.fixture
def server():
    store = Store()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(
        store, "https://test.invalid", verify=lambda _t: None,
        webhook_secrets={"github": SECRET}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd, store
    httpd.shutdown()


def post(httpd, path, body, headers):
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    conn.request("POST", path, body=body, headers=headers)
    r = conn.getresponse()
    out = (r.status, r.read())
    conn.close()
    return out


def _headers(body, event="pull_request", delivery="d-1", secret=SECRET):
    return {"Content-Type": "application/json",
            "Content-Length": str(len(body)),
            webhooks.SIGNATURE_HEADER: signed(body, secret),
            webhooks.EVENT_HEADER: event,
            webhooks.DELIVERY_HEADER: delivery}


def test_a_signed_delivery_reaches_the_outbox(server):
    httpd, store = server
    status, raw = post(httpd, "/webhook/github", BODY, _headers(BODY))
    assert status == 202, raw
    q, message = store.written[-1]
    assert q == "webhook:github:outbox", "an event is FROM the peer, so: outbox"
    assert message["id"] == "d-1"


def test_an_unsigned_delivery_is_refused_and_writes_nothing(server):
    httpd, store = server
    headers = _headers(BODY)
    headers[webhooks.SIGNATURE_HEADER] = signed(BODY, "not-the-secret")
    status, raw = post(httpd, "/webhook/github", BODY, headers)
    assert status == 401
    assert not store.written, "a rejected delivery must not reach the queue"
    assert b"digest" not in raw.lower(), "the reason is for the log, not the caller"


def test_an_unconfigured_name_is_not_an_auth_failure(server):
    """404, not 401. An operator who sees 401 goes looking at a secret they
    set; the name never existed here."""
    httpd, store = server
    status, _ = post(httpd, "/webhook/gitlab", BODY, _headers(BODY))
    assert status == 404
    assert not store.written


def test_ping_answers_200_without_queueing_anything(server):
    """GitHub proving the hook works when someone saves it. Answering anything
    else makes the hook look broken at the moment it is set up."""
    httpd, store = server
    status, raw = post(httpd, "/webhook/github", BODY, _headers(BODY, event="ping"))
    assert status == 200, raw
    assert not store.written, "a ping carries no event to deliver"


def test_a_delivery_too_large_for_the_store_is_refused_before_it_is_read(server):
    """The cap is what a Firestore document can hold, not what GitHub will
    send. 413 is the one status a sender can act on."""
    httpd, store = server
    body = b"x" * 16
    headers = _headers(body)
    headers["Content-Length"] = str(webhooks.MAX_EVENT_BYTES + 1)
    status, _ = post(httpd, "/webhook/github", body, headers)
    assert status == 413
    assert not store.written


def test_a_refused_write_is_a_503_so_github_redelivers():
    """Nothing about the delivery is wrong -- the queue is full, which is ours
    to fix. A 4xx would tell GitHub to stop trying."""
    store = Store(refuse=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(
        store, "https://test.invalid", verify=lambda _t: None,
        webhook_secrets={"github": SECRET}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, _ = post(httpd, "/webhook/github", BODY, _headers(BODY))
    finally:
        httpd.shutdown()
    assert status == 503


def test_no_webhook_peer_configured_means_the_route_is_simply_absent():
    store = Store()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(
        store, "https://test.invalid", verify=lambda _t: None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, _ = post(httpd, "/webhook/github", BODY, _headers(BODY))
    finally:
        httpd.shutdown()
    assert status == 404
    assert not store.written
