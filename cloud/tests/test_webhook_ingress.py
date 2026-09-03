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
import logging
import threading
from http.server import ThreadingHTTPServer

import app
import pytest
import webhooks
from store import Rejected

SECRET = "it-was-a-quiet-tuesday"
BODY = (b'{"action":"closed","repository":{"full_name":"danbarua/agent-bus"},'
        b'"pull_request":{"number":181,"title":"Name the strings",'
        b'"merged":true,"base":{"ref":"main"}}}')


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


def test_each_peer_is_one_variable_holding_one_string():
    """One secret per peer, which is what every other secret here is.

    The first version made this a single JSON document keyed by peer. It bought
    "a second source needs no code change" -- and so does this, for the price
    of a variable rather than a parser and a failure mode for malformed JSON.
    """
    assert webhooks.secrets_from_env(
        {"AGENT_BUS_CLOUD_WEBHOOK_GITHUB_SECRET": "s"}) == {"github": "s"}
    assert webhooks.secrets_from_env(
        {"AGENT_BUS_CLOUD_WEBHOOK_GITHUB_SECRET": "s",
         "AGENT_BUS_CLOUD_WEBHOOK_GITLAB_SECRET": "t"}) == {"github": "s", "gitlab": "t"}


def test_a_mounted_but_empty_secret_is_not_a_peer():
    """It would otherwise register a name whose every delivery fails its HMAC,
    which reads as a wrong secret rather than an absent one -- and those have
    different fixes. That distinction is why `verify_github` returns a reason."""
    assert webhooks.secrets_from_env({"AGENT_BUS_CLOUD_WEBHOOK_GITHUB_SECRET": ""}) == {}
    assert webhooks.secrets_from_env({"AGENT_BUS_CLOUD_WEBHOOK_GITHUB_SECRET": "  "}) == {}


def test_unrelated_variables_are_not_read_as_peers():
    assert webhooks.secrets_from_env({"AGENT_BUS_CLOUD_SIGNING_KEY": "k",
                                      "PATH": "/usr/bin"}) == {}


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
            "User-Agent": "python-test",
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


def test_a_refused_delivery_does_not_corrupt_the_next_request(server):
    """The body has to be drained even when the answer needs none of it.

    HTTP/1.1 keeps the connection open, so an unread payload is read as the
    next request line -- and the garbage that produces is answered as if it
    were a request. It surfaces nowhere near its cause: the symptom was a log
    line naming a method of `?` and the path of the request *before* it.
    """
    httpd, store = server
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    try:
        # Refused: this name is not configured, and the body is never needed.
        conn.request("POST", "/webhook/gitlab", body=BODY, headers=_headers(BODY))
        assert conn.getresponse().read() is not None

        # Same connection. If the first body were still on the socket this
        # would be answered out of the middle of it.
        conn.request("POST", "/webhook/github", body=BODY, headers=_headers(BODY))
        r = conn.getresponse()
        assert r.status == 202, r.read()
        r.read()
    finally:
        conn.close()
    assert [m["id"] for _q, m in store.written] == ["d-1"]


def test_a_delivery_too_large_ends_the_connection():
    """The one path that deliberately does not read the body, so the socket
    cannot be reused -- closing is the only correct end to a message we
    declined to consume."""
    store = Store()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(
        store, "https://test.invalid", verify=lambda _t: None,
        webhook_secrets={"github": SECRET}))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    try:
        headers = _headers(b"x" * 16)
        headers["Content-Length"] = str(webhooks.MAX_EVENT_BYTES + 1)
        conn.request("POST", "/webhook/github", body=b"x" * 16, headers=headers)
        r = conn.getresponse()
        assert r.status == 413
        r.read()
        # The guarantee is that the socket is not reused, which is a property
        # of the server rather than of a header the client happened to see.
        with pytest.raises((http.client.HTTPException, OSError)):
            conn.request("POST", "/webhook/github", body=BODY, headers=_headers(BODY))
            conn.getresponse().read()
    finally:
        conn.close()
        httpd.shutdown()


def test_a_form_encoded_hook_is_refused_at_the_door(server):
    """GitHub offers two content types and only one of them is a payload.

    `application/x-www-form-urlencoded` sends `payload=<urlencoded json>`. It
    verifies its HMAC perfectly -- the signature covers whatever bytes were
    sent -- and is then undecodable by the bridge minutes later, in another
    process, as "event was not JSON". The hook looks healthy here and broken
    there, which is the far end from where anyone can fix it.
    """
    import urllib.parse
    httpd, store = server
    form = urllib.parse.urlencode({"payload": BODY.decode()}).encode()
    headers = _headers(form)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    status, raw = post(httpd, "/webhook/github", form, headers)
    assert status == 415, raw
    assert b"application/json" in raw, "the error has to name the setting to change"
    assert not store.written, "it must not be queued for the bridge to fail on later"


def test_a_charset_on_the_content_type_is_still_json(server):
    """`application/json; charset=utf-8` is the same content type. Matching the
    whole header would refuse a delivery that is entirely correct."""
    httpd, _store = server
    headers = _headers(BODY)
    headers["Content-Type"] = "application/json; charset=utf-8"
    status, raw = post(httpd, "/webhook/github", BODY, headers)
    assert status == 202, raw


def test_the_content_type_is_checked_after_the_signature(server):
    """An unsigned caller learns nothing about how this endpoint is
    configured -- including which content types it takes."""
    httpd, _store = server
    headers = _headers(BODY)
    headers["Content-Type"] = "text/plain"
    headers[webhooks.SIGNATURE_HEADER] = signed(BODY, "not-the-secret")
    status, raw = post(httpd, "/webhook/github", BODY, headers)
    assert status == 401, "the signature is the first thing that must hold"
    assert b"application/json" not in raw


def _records(stream):
    return [json.loads(ln) for ln in stream.getvalue().splitlines() if ln.strip()]


def _capture(httpd, path, body=BODY, headers=None):
    """Run one request against `httpd`, and return whatever it logged.

    The request thread logs *after* it writes the response, so a client's
    `getresponse()` returning is no guarantee that call has happened yet --
    it is a different thread, doing one more thing after the bytes are on
    the wire. Removing the stream's handler right away, before that catches
    up, does not just risk reading the record too early: it can discard the
    record outright, because a log call reaching a logger with no handler
    attached goes nowhere. Found as a CI-only flake this passed locally every
    time on a machine fast enough that the race never lost.
    """
    import io
    import time

    import logs
    stream = io.StringIO()
    logs.configure(stream=stream, force=True)
    try:
        post(httpd, path, body, headers or _headers(body))
        deadline = time.time() + 2
        while not _records(stream) and time.time() < deadline:
            time.sleep(0.01)
    finally:
        for h in list(logging.getLogger(logs.LOGGER_NAME).handlers):
            logging.getLogger(logs.LOGGER_NAME).removeHandler(h)
    return _records(stream)


def test_one_record_per_delivery_and_it_names_the_event(server):
    """`message` is the verb, and for a delivery the verb is the event.

    It used to be the literal word `webhook` on one line and
    `POST /webhook/github` on another -- two records per delivery, neither
    saying which event had arrived, on the endpoint that exists to say exactly
    that. Every one read the same in Cloud Logging's summary column.
    """
    httpd, _store = server
    got = _capture(httpd, "/webhook/github")
    assert len(got) == 1, f"one delivery produced {len(got)} records: {got}"
    r = got[0]
    assert r["message"] == "pull_request", "the summary column has to name the event"
    assert (r["verb"], r["peer"], r["status"]) == ("pull_request", "github", 202)
    assert r["trace_id"] == "d-1", "the delivery id is what joins this to the bridge"


def test_a_refused_delivery_still_says_which_hook_it_was(server):
    """The reason and the peer, on a path that refuses before the event is
    known. Without the peer a 401 says only that something failed somewhere."""
    httpd, _store = server
    headers = _headers(BODY)
    headers[webhooks.SIGNATURE_HEADER] = signed(BODY, "not-the-secret")
    got = _capture(httpd, "/webhook/github", headers=headers)
    assert any(r.get("reason") == "digest-mismatch" for r in got), got
    assert all(r.get("peer") == "github" for r in got), (
        f"a record that does not name the hook: {got}")


def test_the_github_headers_are_logged_and_the_signature_is_not(server):
    """Which event and which delivery are the two facts a record is about, and
    neither is a secret. The signature beside them is."""
    httpd, _store = server
    headers = _capture(httpd, "/webhook/github")[0]["headers"]
    assert headers["x-github-event"] == "pull_request"
    assert headers["x-github-delivery"] == "d-1"
    assert headers["x-hub-signature-256"] == "<redacted>"


def test_a_post_to_an_unknown_path_does_not_corrupt_the_next_request(server):
    """Observed in production, not deduced.

    GitHub was posting to `/webhooks/github` -- plural, and not a route here --
    and every delivery produced *two* records: the 404 it deserved, then a 400
    for the payload being read as the next request line. `?` for the method and
    the previous request's path, on a connection GitHub reused.

    The webhook handler drains its body already. Nothing else answering a POST
    did, and a wrong URL is the case where that matters most: it is exactly
    when somebody is reading the logs.
    """
    httpd, store = server
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    try:
        conn.request("POST", "/webhooks/github", body=BODY, headers=_headers(BODY))
        assert conn.getresponse().read() is not None

        conn.request("POST", "/webhook/github", body=BODY, headers=_headers(BODY))
        r = conn.getresponse()
        assert r.status == 202, r.read()
        r.read()
    finally:
        conn.close()
    assert [m["id"] for _q, m in store.written] == ["d-1"]


def test_a_post_to_the_mcp_path_that_is_not_mcp_drains_too(server):
    """The same refusal, reached by the other branch."""
    httpd, store = server
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    try:
        conn.request("POST", "/nothing-here", body=BODY, headers=_headers(BODY))
        assert conn.getresponse().read() is not None
        conn.request("POST", "/webhook/github", body=BODY, headers=_headers(BODY))
        r = conn.getresponse()
        assert r.status == 202, r.read()
        r.read()
    finally:
        conn.close()
    assert store.written


# ------------------------------------------------- what a record is allowed to say

def _one_record(server, path, body=BODY, headers=None):
    httpd, _store = server
    return _capture(httpd, path, body, headers)


def test_only_credentials_are_redacted(server):
    """This was an allowlist: four headers logged, everything else replaced.

    It was argued for on security grounds and cost more than it bought on both
    counts. Debugging, because finding out what a caller actually sent took a
    code change and a deploy. And security, which was the stated reason:
    "redact everything in case we are compromised" leaves nobody able to answer
    *what did they send*, which is the only question that matters afterwards.

    So: the short list that is genuinely a credential, and everything else is
    data we are allowed to see about our own service.
    """
    headers = _headers(BODY)
    headers["Authorization"] = "Bearer super-secret"
    headers["X-Custom-Thing"] = "not a secret"
    got = _one_record(server, "/webhook/github", headers=headers)[0]["headers"]

    assert got["authorization"] == "<redacted>"
    assert got[webhooks.SIGNATURE_HEADER.lower()] == "<redacted>"
    assert got["user-agent"] == "python-test"
    assert got["x-custom-thing"] == "not a secret", (
        "a header nobody listed is data, not a secret")


def test_a_refused_request_records_what_it_asked_for(server):
    """A run of 404s that says nothing is how a wrong Payload URL went
    unnoticed. `path` and the body answer "what was it asking for" -- for
    GitHub pointed at the wrong route, and for anyone probing."""
    got = _one_record(server, "/webhooks/github")
    r = next(x for x in got if x.get("status") == 404)
    assert r["path"] == "/webhooks/github"
    assert "danbarua/agent-bus" in r["body"], "the body is the request, not a secret"


def test_an_accepted_delivery_does_not_copy_its_payload_into_the_log(server):
    """The opposite trade. An accepted payload is tens of KB and already in the
    store, so a copy per delivery is cost without an answer."""
    r = _one_record(server, "/webhook/github")[0]
    assert r["status"] == 202
    assert "body" not in r


def test_the_record_says_what_the_delivery_was_about(server):
    """`pull_request` alone does not say which repository, which number, or
    what happened to it -- and the payload is in Firestore, where nobody
    reading a log is looking."""
    r = _one_record(server, "/webhook/github")[0]
    assert (r["repo"], r["action"], r["number"], r["base"]) == (
        "danbarua/agent-bus", "closed", 181, "main")


def test_the_recorded_path_keeps_its_query(server):
    """On a 404 the query is most of what the caller was trying to do."""
    got = _one_record(server, "/webhooks/github?attempt=2")
    assert any(x.get("path") == "/webhooks/github?attempt=2" for x in got), got


def test_about_reads_nothing_it_cannot_and_never_the_prose():
    """A title is a person's words; the contract puts message content at TRACE
    and nowhere else. And a payload that will not parse has already been
    accepted -- it verified -- so a log line is not the place to reject it."""
    assert webhooks.about(b"not json") == {}
    assert webhooks.about(b'["a list"]') == {}
    assert "title" not in webhooks.about(BODY)
    assert webhooks.about(b'{"repository": {"full_name": "a/b"}}') == {"repo": "a/b"}
