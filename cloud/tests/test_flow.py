"""The whole flow over real HTTP: register, consent, redeem, call.

At the function level #63 already proves each guard. This is the layer where the
predecessor's bugs actually lived -- between "the function returns the right
dict" and "a connector could use it" -- so every negative is repeated here
through the socket rather than trusted from below.
"""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import app
import config
import oauth
import pytest

KEY = b"\x03" * 32
ISSUER = "https://test.invalid"
PASSPHRASE = "open sesame"
CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"

# The allowlist does double duty, and that is the design: it refuses a stranger
# a place to have the code delivered, *and* it says which peer a client is. The
# redirect URI is the only thing in the flow that identifies the vendor, and it
# is one we control rather than one the client asserts.
ALLOWLIST = {CLAUDE_CB: "desktop:claude"}


class StubStore:
    def __init__(self):
        self.clients, self.codes, self.written = {}, {}, []
        self.rostered = [{"name": "labkit-dev", "kind": "other"}]

    def put_client(self, r): self.clients[r["client_id"]] = r
    def client(self, cid): return self.clients.get(cid)
    def put_code(self, c, r):
        self.codes[c] = r
    def take_code(self, c): return self.codes.pop(c, None)
    def roster(self, address): return self.rostered
    def read(self, q, unread_only=True): return []
    def ack(self, q, ids): return len(ids)
    def write(self, q, m):
        self.written.append((q, m))
        return "m1"


@pytest.fixture
def server():
    store = StubStore()
    handler = app.make_handler(
        store, ISSUER,
        verify=config.bearer_verifier(KEY),
        oauth_config=config.OAuthConfig(key=KEY, allowlist=ALLOWLIST,
                                     passphrase=PASSPHRASE))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", store
    httpd.shutdown()


def _req(url, data=None, headers=None, method=None, redirect=True):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    opener = urllib.request.build_opener()
    if not redirect:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _register(base, redirect_uri=CLAUDE_CB):
    body = json.dumps({"redirect_uris": [redirect_uri]}).encode()
    status, _, raw = _req(f"{base}/register", data=body,
                          headers={"Content-Type": "application/json"})
    return status, json.loads(raw or b"{}")


def _consent(base, client_id, challenge, passphrase=PASSPHRASE):
    form = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": CLAUDE_CB,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "state": "xyz", "passphrase": passphrase,
    }).encode()
    return _req(f"{base}/authorize", data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                redirect=False)


def _consent_page(base, client_id, redirect_uri=CLAUDE_CB):
    """The GET a person's browser makes. Distinct from `_consent`, which POSTs
    -- the two paths disagreed about what a legitimate request was, which is
    the whole of #87."""
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect_uri, "code_challenge": "abc",
        "code_challenge_method": "S256",
    })
    return _req(f"{base}/authorize?{query}", redirect=False)


def _token(base, **form):
    return _req(f"{base}/token", data=urllib.parse.urlencode(form).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"})


def _call(base, token=None, name="list_agents"):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": {}}}).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _req(f"{base}/mcp", data=body, headers=headers)


# ------------------------------------------------------------ the happy path

def test_a_connector_can_register_consent_redeem_and_call(server):
    """The bring-up, end to end. Each step is a separate negative below; this
    is the one that proves they compose."""
    base, _ = server
    verifier = "v" * 64

    status, reg = _register(base)
    assert status == 200, reg
    assert reg["client_id"]

    status, headers, _ = _consent(base, reg["client_id"], oauth.pkce_challenge(verifier))
    assert status in (302, 303), status
    location = urllib.parse.urlparse(headers["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == CLAUDE_CB
    query = urllib.parse.parse_qs(location.query)
    assert query["state"] == ["xyz"], "state must round-trip or the client aborts"
    code = query["code"][0]

    status, _, raw = _token(base, grant_type="authorization_code", code=code,
                            code_verifier=verifier, redirect_uri=CLAUDE_CB,
                            client_id=reg["client_id"])
    assert status == 200, raw
    tokens = json.loads(raw)
    assert tokens["token_type"] == "Bearer"

    status, _, raw = _call(base, tokens["access_token"])
    assert status == 200, raw
    assert "labkit-dev" in json.loads(raw)["result"]["content"][0]["text"]


def test_the_token_decides_which_queues_are_touched(server):
    """The caller never names its own address. It comes from the claims, so a
    client cannot read someone else's mailbox by asking nicely."""
    base, store = server
    verifier = "v" * 64
    _, reg = _register(base)
    _, headers, _ = _consent(base, reg["client_id"], oauth.pkce_challenge(verifier))
    code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
    _, _, raw = _token(base, grant_type="authorization_code", code=code,
                       code_verifier=verifier, redirect_uri=CLAUDE_CB,
                       client_id=reg["client_id"])
    token = json.loads(raw)["access_token"]

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "send_message", "arguments": {
                           "to": "labkit-dev", "text": "hi", "from": "desktop:claude"}}}).encode()
    _req(f"{base}/mcp", data=body,
         headers={"Content-Type": "application/json",
                  "Authorization": f"Bearer {token}"})
    assert store.written[0][0] == "desktop:claude:outbox"


# ---------------------------------------------------------- the negatives

def test_a_redirect_uri_outside_the_allowlist_is_refused_over_http(server):
    base, store = server
    status, body = _register(base, redirect_uri="https://evil.example/cb")
    assert status == 400, body
    assert store.clients == {}


def test_consent_without_the_passphrase_does_not_issue_a_code(server):
    base, store = server
    _, reg = _register(base)
    status, headers, _ = _consent(base, reg["client_id"],
                                  oauth.pkce_challenge("v" * 64), passphrase="wrong")
    assert status not in (302, 303), "a refused consent must not redirect"
    assert "Location" not in headers
    assert store.codes == {}, "and must not mint a code"


def test_a_replayed_code_is_refused_over_http(server):
    base, _ = server
    verifier = "v" * 64
    _, reg = _register(base)
    _, headers, _ = _consent(base, reg["client_id"], oauth.pkce_challenge(verifier))
    code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]

    first = _token(base, grant_type="authorization_code", code=code,
                   code_verifier=verifier, redirect_uri=CLAUDE_CB,
                   client_id=reg["client_id"])
    assert first[0] == 200
    again = _token(base, grant_type="authorization_code", code=code,
                   code_verifier=verifier, redirect_uri=CLAUDE_CB,
                   client_id=reg["client_id"])
    assert again[0] == 400, again


def test_a_wrong_verifier_is_refused_over_http(server):
    base, _ = server
    _, reg = _register(base)
    _, headers, _ = _consent(base, reg["client_id"], oauth.pkce_challenge("v" * 64))
    code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
    status, _, _ = _token(base, grant_type="authorization_code", code=code,
                          code_verifier="wrong" * 13, redirect_uri=CLAUDE_CB,
                          client_id=reg["client_id"])
    assert status == 400


def test_an_unauthenticated_tool_call_says_where_to_authenticate(server):
    """RFC 9728: the 401 has to point at the protected-resource metadata, or a
    connector has nowhere to start."""
    base, _ = server
    status, headers, _ = _call(base)
    assert status == 401
    assert "oauth-protected-resource" in headers.get("WWW-Authenticate", "")


def test_a_refresh_token_cannot_be_used_as_an_access_token(server):
    """Both are signed by the same key. `kind` is the only thing separating a
    30-day credential from a 1-hour one."""
    base, _ = server
    refresh = oauth.sign_token(
        {"sub": "dan", "kind": "refresh", "address": "desktop:claude",
         "exp": 9_999_999_999}, KEY)
    status, _, _ = _call(base, refresh)
    assert status == 401


# ------------------------------------------------------- the bridge's token

def test_a_bridge_token_can_be_minted_out_of_band(server):
    """The bridge is not a third-party client and does not do the dance. One
    long-lived bearer, signed with the same key, and one header."""
    base, _ = server
    token = oauth.mint_bridge_token("desktop:claude", KEY, ISSUER)
    status, _, raw = _call(base, token)
    assert status == 200, raw


def test_the_consent_page_does_not_render_for_a_client_that_never_registered(server):
    """The GET and the POST have to agree about what a legitimate request is.

    They did not: `_authorize` checked registration, `_consent` checked only
    the redirect_uri, so anyone could produce a URL that rendered this page
    naming any client id they liked. Found against the live server.

    No code could be issued -- the POST checks, and the passphrase gates it
    besides. What was wrong is subtler and worse: the consent page is the only
    place a person is asked to make a trust decision, and it exists precisely
    because the predecessor's single Allow button relied on hostname obscurity
    that CT logs destroyed. A page that renders for a client the server has
    never heard of teaches the human to click through it.
    """
    base, _ = server
    status, _, raw = _consent_page(base, "never-registered")
    body = raw.decode()
    assert status == 400, status
    assert "Connect this client" not in body
    assert "never-registered" not in body


def test_the_consent_page_renders_for_a_client_that_did_register(server):
    """The check above is worth nothing if it refuses everyone."""
    base, _ = server
    _, reg = _register(base)
    status, _, raw = _consent_page(base, reg["client_id"])
    body = raw.decode()
    assert status == 200, status
    assert "Connect this client" in body
    assert "desktop:claude" in body


def test_a_wrong_passphrase_says_so(server):
    """Failing closed is right; failing silently is not.

    The form re-rendered unchanged, so a person could not tell "I mistyped"
    from "this server is broken" -- and the second reading ends with them
    giving up on a system that is working. There is nothing to leak by saying
    it: the client knows it submitted a passphrase, and the server already
    logs the refusal.
    """
    base, store = server
    _, reg = _register(base)
    status, headers, raw = _consent(base, reg["client_id"],
                                    oauth.pkce_challenge("v" * 64),
                                    passphrase="wrong")
    body = raw.decode()
    assert status not in (302, 303)
    assert "Location" not in headers
    assert store.codes == {}
    assert "not the right passphrase" in body.lower() or "passphrase was not" in body.lower(), (
        "the form came back with no indication anything was wrong")


def test_a_first_visit_is_not_told_it_got_the_passphrase_wrong(server):
    """The message must come from a refusal, not from rendering the page."""
    base, _ = server
    _, reg = _register(base)
    _, _, raw = _consent_page(base, reg["client_id"])
    assert "not the right passphrase" not in raw.decode().lower()


def test_consent_for_a_client_that_never_registered_is_refused(server):
    """Found by a surviving mutant: removing this check broke no test.

    The passphrase and the allowlist still gate the flow, so this is defence in
    depth rather than the only lock -- but without it registration is
    decorative, and a code would be issued to a client the server has never
    heard of. `/token` would then happily match it against itself.
    """
    base, store = server
    status, headers, _ = _consent(base, "never-registered",
                                  oauth.pkce_challenge("v" * 64))
    assert status not in (302, 303)
    assert "Location" not in headers
    assert store.codes == {}


# ------------------------------------------------- whose error format is whose


def test_the_oauth_endpoints_keep_rfc_6749s_error_shape(server):
    """Our own endpoints answer in RFC 7807 problem+json. **These must not.**

    RFC 6749 §5.2 mandates `{"error": ...}` on the token endpoint and RFC 7591
    §3.2.2 the same for registration, and a connector parses them by those
    specs. Rewriting them as problem+json would be a spec violation dressed up
    as consistency -- and it would break the thing quietly, at the one moment
    nobody is watching: someone re-adding a connector.
    """
    base, _ = server
    status, headers, body = _token(base, grant_type="password")
    assert status == 400
    assert headers["Content-Type"] == "application/json", headers
    assert json.loads(body) == {"error": "unsupported_grant_type"}, body


def test_a_json_rpc_error_keeps_its_own_envelope(server):
    """JSON-RPC owns this shape too. A client reads `error.code`, not a status
    body -- the transport is 200-with-an-error-object as often as not."""
    base, _ = server
    status, headers, body = _call(base)          # no token
    assert headers["Content-Type"] == "application/json", headers
    assert json.loads(body)["error"]["code"] == -32001, body
    assert status == 401
