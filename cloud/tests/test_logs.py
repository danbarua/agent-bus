"""The server's own logs, which until now were never emitted at all.

`log = logging.getLogger("agent-bus-cloud")` with no handler anywhere means the
root logger's default level applies, so every `log.info` was discarded inside
the process. Not a collection problem -- the records never left. Production ran
for a day with a redaction allowlist, four refusal messages and a request log,
none of which had ever produced a byte.
"""

import io
import json
import logging
import time

import logs
import pytest


@pytest.fixture
def stream():
    buf = io.StringIO()
    logs.configure(stream=buf, force=True)
    yield buf
    for h in list(logging.getLogger("agent-bus-cloud").handlers):
        logging.getLogger("agent-bus-cloud").removeHandler(h)
    logs.TRACE.set("")


def _lines(buf):
    return [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]


def test_an_info_record_actually_reaches_the_stream(stream):
    """The bug, stated plainly. Everything else here is shape."""
    logging.getLogger("agent-bus-cloud").info("hello")
    assert [r["message"] for r in _lines(stream)] == ["hello"]


def test_it_is_one_json_object_per_line_with_a_severity(stream):
    """Cloud Logging parses stdout as structured only if each line is one
    object, and it reads the level from `severity` -- not `level`, not
    `levelname`. Get that wrong and every line is INFO forever."""
    logging.getLogger("agent-bus-cloud").warning("careful")
    rec = _lines(stream)[0]
    assert rec["severity"] == "WARNING"
    assert rec["message"] == "careful"


def test_extra_fields_survive(stream):
    """The request log passes `verb` and redacted `headers` through `extra=`.
    A formatter that only rendered the message would drop exactly the part
    worth having."""
    logging.getLogger("agent-bus-cloud").info(
        "tools/list", extra={"verb": "tools/list", "headers": {"accept": "*/*"}})
    rec = _lines(stream)[0]
    assert rec["verb"] == "tools/list"
    assert rec["headers"] == {"accept": "*/*"}


def test_a_record_carries_the_request_trace_so_it_nests_under_it(stream):
    """Cloud Run stamps every request with X-Cloud-Trace-Context and logs it on
    the request entry. An app log naming the same trace is shown nested beneath
    it in the console -- which is the whole difference between reading a
    connector's flow and inferring it from status codes."""
    logs.TRACE.set(logs.trace_field("105445aa7843bc8bf206b120001000/1;o=1", "my-project"))
    logging.getLogger("agent-bus-cloud").info("during a request")
    rec = _lines(stream)[0]
    assert rec["logging.googleapis.com/trace"] == (
        "projects/my-project/traces/105445aa7843bc8bf206b120001000")


def test_no_trace_header_is_not_an_error(stream):
    """Locally and in the tests there is no Cloud Run in front. The field is
    absent rather than empty or wrong: an empty trace id would group every
    local record under one meaningless trace."""
    logging.getLogger("agent-bus-cloud").info("no request in sight")
    assert "logging.googleapis.com/trace" not in _lines(stream)[0]


@pytest.mark.parametrize("header, project, why", [
    ("", "p", "no header"),
    ("abc123/1;o=1", "", "no project id to qualify it with"),
    ("nonsense", "p", "no slash, so no trace id"),
])
def test_a_trace_that_cannot_be_built_is_omitted_not_guessed(header, project, why):
    assert logs.trace_field(header, project) == "", why


def test_a_bearer_never_reaches_the_stream(stream):
    """The redaction allowlist has existed since #62 and has never once been
    exercised against a real handler, because there was no handler. These logs
    are read during a connector mystery, which is exactly when someone pastes
    them somewhere."""
    from app import redact

    logging.getLogger("agent-bus-cloud").info(
        "POST /mcp -> 200",
        extra={"headers": redact({"Authorization": "Bearer super-secret",
                                  "Accept": "application/json"})})
    dumped = stream.getvalue()
    assert "super-secret" not in dumped
    assert "<redacted>" in dumped
    assert "application/json" in dumped, "the allowlisted header is kept"


# ------------------------------------------- the whole point, over a socket


def test_a_real_request_produces_a_readable_line_with_its_trace(stream, monkeypatch):
    """End to end, because every piece of this passed in isolation while the
    server emitted nothing at all.

    A request carrying Cloud Run's trace header must produce one JSON line
    naming the method, the path, the status and the trace -- which is exactly
    what was missing when a Claude Desktop bring-up had to be read off HTTP
    status codes.
    """
    import json as _json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    import app

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "agent-bus-test")

    class Store:
        def roster(self, address):
            return []

    handler = app.make_handler(Store(), "https://test.invalid",
                               verify=lambda t: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/mcp",
            data=_json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer never-log-me",
                     "X-Cloud-Trace-Context": "abc123def456/9;o=1"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        deadline = time.time() + 2
        while not _lines(stream) and time.time() < deadline:
            time.sleep(0.01)
    finally:
        httpd.shutdown()

    rec = next(r for r in _lines(stream) if r.get("verb") == "tools/list")
    assert rec["severity"] == "INFO"
    # The values are fields, not a rendered sentence: `message` is the verb,
    # per docs/structured-logging.md. "Everything that was not a 200" has to be
    # a comparison on `status`, not a text match on the summary line.
    assert rec["message"] == "tools/list"
    assert (rec["status"], rec["path"], rec["http_method"]) == (200, "/mcp", "POST")
    assert rec["logging.googleapis.com/trace"] == (
        "projects/agent-bus-test/traces/abc123def456")
    assert "never-log-me" not in stream.getvalue()
    assert rec["headers"]["authorization"] == "<redacted>"


def test_a_second_request_without_the_header_does_not_inherit_the_first(stream):
    """HTTP/1.1 keep-alive serves several requests on one thread. A request
    with no trace header must not file its logs under the previous request's
    flow -- which is why the stamp is assigned unconditionally rather than
    only when the header is present."""
    logs.TRACE.set(logs.trace_field("aaa/1;o=1", "p"))
    logging.getLogger("agent-bus-cloud").info("first")
    logs.TRACE.set(logs.trace_field("", "p"))
    logging.getLogger("agent-bus-cloud").info("second")

    first, second = _lines(stream)
    assert first["logging.googleapis.com/trace"].endswith("/aaa")
    assert "logging.googleapis.com/trace" not in second


def test_the_production_entry_point_configures_logging():
    """The seam the bug actually lived in.

    Every test above configures logging itself, so all of them passed while
    `serve()` configured nothing -- which is precisely how the original defect
    survived: the formatter was fine, the logger was fine, and no record was
    ever emitted. Removing the call from the entry point has to go red
    somewhere, and it can only be here.
    """
    import app

    log = logging.getLogger("agent-bus-cloud")
    for h in list(log.handlers):
        log.removeHandler(h)
    assert not log.handlers, "precondition: nothing configured"

    with pytest.raises(RuntimeError):
        app.main(lambda: pytest.fail("the store must not be built"))

    assert log.handlers, (
        "the entry point ran and left the logger unconfigured, so the server "
        "would run in production emitting nothing at all"
    )
    for h in list(log.handlers):
        log.removeHandler(h)


def test_two_requests_on_one_connection_do_not_share_a_trace(stream, monkeypatch):
    """Keep-alive, for real. The previous test set TRACE by hand, which meant
    the server could have stamped it only when the header was present and
    nothing would have noticed -- a second request on the same thread would
    then file its logs under the first request's flow."""
    import http.client
    import json as _json
    import threading
    from http.server import ThreadingHTTPServer

    import app

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "agent-bus-test")

    class Store:
        def roster(self, address):
            return []

    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        app.make_handler(Store(), "https://test.invalid", verify=lambda t: None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    body = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
        conn.request("POST", "/mcp", body,
                     {"Content-Type": "application/json",
                      "X-Cloud-Trace-Context": "traced111/1;o=1"})
        conn.getresponse().read()
        # Same connection, therefore the same server thread. No trace header.
        conn.request("POST", "/mcp", body, {"Content-Type": "application/json"})
        conn.getresponse().read()
        conn.close()
        deadline = time.time() + 2
        while len([r for r in _lines(stream) if r.get("verb")]) < 2 and time.time() < deadline:
            time.sleep(0.01)
    finally:
        httpd.shutdown()

    served = [r for r in _lines(stream) if r.get("verb") == "tools/list"]
    assert len(served) == 2, served
    assert served[0]["logging.googleapis.com/trace"].endswith("/traced111")
    assert "logging.googleapis.com/trace" not in served[1], (
        "the second request inherited the first request's trace"
    )


# ------------------------------ the message id, alongside the request trace (#108)


def test_a_bridge_push_logs_the_message_id_as_trace_id(stream, monkeypatch):
    """One identifier, one query expression, two places.

    The cloud had the request trace and not the message id; the bus had
    neither. A message crossing the bridge outlives the HTTP request that
    carried one leg of it, so the message id is the outer identifier and the
    request trace is a span within it -- which is why both are emitted rather
    than one replacing the other.
    """
    import json as _json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    import app
    import oauth

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "agent-bus-test")
    key = b"\x05" * 32

    class Store:
        def write(self, q, message):
            return message.get("id") or "minted-here"

    cfg = app.OAuthConfig(key=key, allowlist={}, passphrase="x")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(
        Store(), "https://test.invalid", verify=app.bearer_verifier(key),
        oauth_config=cfg))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        token = oauth.mint_bridge_token("desktop:claude", key, "https://test.invalid")
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/bridge",
            data=_json.dumps({"op": "push", "message": {
                "id": "local-abc123", "from": "labkit-dev", "text": "hi"}}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}",
                     "X-Cloud-Trace-Context": "reqtrace77/1;o=1"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        deadline = time.time() + 2
        while not any(r.get("trace_id") for r in _lines(stream)) and time.time() < deadline:
            time.sleep(0.01)
    finally:
        httpd.shutdown()

    rec = next(r for r in _lines(stream) if r.get("trace_id"))
    assert rec["trace_id"] == "local-abc123", "the message id is the journey"
    assert rec["logging.googleapis.com/trace"].endswith("/reqtrace77"), (
        "and the request trace is still there -- one is a span within the other")


def test_a_request_carrying_no_message_has_no_trace_id(stream, monkeypatch):
    """Same rule as the bus side: an empty trace_id groups unrelated records
    under one meaningless trace, so a request that carried no message omits
    it rather than emitting an empty one."""
    import json as _json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    import app

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "agent-bus-test")

    class Store:
        def roster(self, address):
            return []

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(
        Store(), "https://test.invalid", verify=lambda t: None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/mcp",
            data=_json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        deadline = time.time() + 2
        while not _lines(stream) and time.time() < deadline:
            time.sleep(0.01)
    finally:
        httpd.shutdown()

    assert not any("trace_id" in r for r in _lines(stream)), _lines(stream)


def test_every_record_names_the_build_that_wrote_it(stream, monkeypatch):
    """A regression found in the logs has to be attributable to a release.

    `/health` has reported the version since #211, but only to whoever thinks
    to ask it -- and asking a *live* server what it is running now is a
    different question from what was running when the line was written. Without
    this, "did this start at cloud-v0.0.4?" is unanswerable from the logs.
    """
    monkeypatch.setenv("AGENT_BUS_CLOUD_VERSION", "cloud-v9.9.9")
    logging.getLogger("agent-bus-cloud").info("anything")
    assert _lines(stream)[0]["version"] == "cloud-v9.9.9"


def test_an_unversioned_image_says_so_rather_than_omitting_the_field(stream, monkeypatch):
    """An absent field reads as "old log line"; `0+unknown` reads as "this
    build was not stamped", which is a different and fixable problem. The same
    string `/health` reports, so the two answers cannot disagree."""
    monkeypatch.delenv("AGENT_BUS_CLOUD_VERSION", raising=False)
    logging.getLogger("agent-bus-cloud").info("anything")
    assert _lines(stream)[0]["version"] == "0+unknown"


def test_a_verb_we_do_not_implement_is_logged_rather_than_answered_in_silence(
        stream, monkeypatch):
    """Whether a request reached the container at all.

    `BaseHTTPRequestHandler` answers any verb without a `do_*` with 501 through
    `send_error`, which never passes through `_send` -- so two HEADs from a
    scanner on 2026-08-27 were answered by this code and left no record. In
    Cloud Run's request log that is indistinguishable from a 501 the front end
    produced without the container ever being asked, and the question "did that
    reach us?" had no answer from the logs at all.

    The user-agent is the point: it is the only thing on the record that says
    what was calling.
    """
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    import app

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "agent-bus-test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(
        object(), "https://test.invalid", verify=lambda _t: None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1],
                                          timeout=5)
        conn.request("HEAD", "/health", headers={"User-Agent": "rust_sniffer/0.1"})
        assert conn.getresponse().status == 501
        conn.close()
        deadline = time.time() + 2
        while not _lines(stream) and time.time() < deadline:
            time.sleep(0.01)
    finally:
        httpd.shutdown()

    rec = next(r for r in _lines(stream) if r.get("status") == 501)
    assert rec["severity"] == "WARNING", "nothing we serve on purpose lands here"
    assert rec["http_method"] == "HEAD"
    assert rec["path"] == "/health"
    assert rec["headers"]["user-agent"] == "rust_sniffer/0.1"


def test_the_request_record_says_which_bridge_op_ran(stream, monkeypatch):
    """Every bridge call is `POST /bridge`, so the request line alone cannot
    tell a poll from a push.

    A bridge polls every two minutes forever, which makes `pull` the
    overwhelming majority of all traffic -- and until this field existed,
    filtering it out to find the calls that did something meant matching on
    text that was identical for both.
    """
    import json as _json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    import app
    import oauth

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "agent-bus-test")
    key = b"\x05" * 32

    class Store:
        def read(self, q, unread_only=True):
            return []

    cfg = app.OAuthConfig(key=key, allowlist={}, passphrase="x")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(
        Store(), "https://test.invalid", verify=app.bearer_verifier(key),
        oauth_config=cfg))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        token = oauth.mint_bridge_token("desktop:claude", key, "https://test.invalid")
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/bridge",
            data=_json.dumps({"op": "pull"}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        deadline = time.time() + 2
        while not any(r.get("verb") for r in _lines(stream)) and time.time() < deadline:
            time.sleep(0.01)
    finally:
        httpd.shutdown()

    rec = next(r for r in _lines(stream) if r.get("status") == 200)
    assert rec["verb"] == "pull"
    assert rec["message"] == "pull", "the message is the verb, not a template"
    assert rec["path"] == "/bridge"


def test_a_second_request_on_one_connection_does_not_inherit_the_first_verb(
        stream, monkeypatch):
    """`_intent` is per request, not per connection.

    HTTP/1.1 keep-alive serves several requests from one handler instance, so a
    verb left over from the previous one would file this request's record under
    the wrong operation -- and the record would look entirely normal. Exactly
    the hazard `parse_request` already resets the trace for, reached by the
    field added next to it.
    """
    import http.client
    import json as _json
    import threading
    from http.server import ThreadingHTTPServer

    import app

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "agent-bus-test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(
        object(), "https://test.invalid", verify=lambda _t: None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1],
                                          timeout=5)
        body = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        conn.request("POST", "/mcp", body=body,
                     headers={"Content-Type": "application/json"})
        conn.getresponse().read()
        # Same connection, and a path that sets no verb of its own.
        conn.request("GET", "/health")
        conn.getresponse().read()
        conn.close()
        deadline = time.time() + 2
        while len(_lines(stream)) < 2 and time.time() < deadline:
            time.sleep(0.01)
    finally:
        httpd.shutdown()

    health = next(r for r in _lines(stream) if r.get("path") == "/health")
    assert "verb" not in health, (
        f"the /health record inherited the previous request's verb: {health}")
    assert health["message"] == "GET /health"
