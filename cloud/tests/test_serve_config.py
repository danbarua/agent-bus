"""What a container actually starts with.

`serve()` was the one seam nothing drove: every other test hands `make_handler`
a key, an allowlist and a passphrase directly, so the wiring between the
environment and those three was never exercised -- and it turned out not to
exist. A deployed container authenticated nobody, and looked healthy doing it.
"""

import json
import os

import app
import oauth
import pytest

KEY_HEX = "05" * 32
ISSUER = "https://bus.example"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("AGENT_BUS_CLOUD_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AGENT_BUS_CLOUD_ISSUER", ISSUER)
    monkeypatch.setenv("AGENT_BUS_CLOUD_SIGNING_KEY", KEY_HEX)
    return monkeypatch


def test_the_signing_key_arrives_and_is_the_one_that_verifies(env):
    """The whole point. A token minted with this key must verify against the
    config the handler is built from -- a key that arrives mangled would fail
    every request in a way that looks like a client bug."""
    cfg = app.config_from_env()
    token = oauth.mint_bridge_token("desktop:claude", cfg.oauth.key, ISSUER)
    assert oauth.verify_token(token, cfg.oauth.key)


def test_no_signing_key_refuses_to_start(env):
    """The failure mode that looks healthy: /health answers, discovery answers,
    and only a connector attempting a tool call finds out nobody can be
    authenticated. It must not be possible to deploy into it."""
    env.delenv("AGENT_BUS_CLOUD_SIGNING_KEY")
    with pytest.raises(RuntimeError, match="SIGNING_KEY"):
        app.config_from_env()


@pytest.mark.parametrize("bad, why", [
    ("nothex!!", "not hex"),
    ("05" * 8, "too short"),
    ("", "empty"),
])
def test_a_key_that_is_not_a_key_refuses_to_start(env, bad, why):
    """A truncated or mistyped secret must not become a weak one. `printf %s`
    exists in the runbook because a trailing newline once made a key look
    valid and authenticate nothing."""
    env.setenv("AGENT_BUS_CLOUD_SIGNING_KEY", bad)
    with pytest.raises(RuntimeError, match="SIGNING_KEY"):
        app.config_from_env()


def test_the_issuer_is_required_and_says_so(env):
    """It is the OAuth `issuer` and the base of every URL a connector caches,
    so a container that guessed one would be worse than one that would not
    start."""
    env.delenv("AGENT_BUS_CLOUD_ISSUER")
    with pytest.raises(RuntimeError, match="ISSUER"):
        app.config_from_env()


def test_the_allowlist_arrives_as_uri_to_address(env):
    """The redirect URI is the only thing in the flow that names the vendor,
    and it is one we control rather than one the client asserts."""
    env.setenv("AGENT_BUS_CLOUD_ALLOWLIST", json.dumps({CALLBACK: "desktop:claude"}))
    env.setenv("AGENT_BUS_CLOUD_PASSPHRASE", "hunter2")
    assert app.config_from_env().oauth.allowlist == {CALLBACK: "desktop:claude"}


def test_an_unparseable_allowlist_refuses_to_start(env):
    """Failing closed would be silent: an allowlist that did not parse would
    become an empty one, and every connector would be refused with no clue
    why. Refusing to start says which variable is wrong."""
    env.setenv("AGENT_BUS_CLOUD_ALLOWLIST", "claude.ai=desktop:claude")
    with pytest.raises(RuntimeError, match="ALLOWLIST"):
        app.config_from_env()


def test_a_bridge_only_deployment_needs_no_passphrase(env):
    """A bridge token is minted out of band and never sees the consent page.
    With no connector allowlisted the flow the passphrase gates is unreachable,
    so requiring one would be a prerequisite that buys nothing."""
    cfg = app.config_from_env()
    assert cfg.oauth.allowlist == {}
    assert cfg.oauth.passphrase == ""


def test_allowlisting_a_connector_makes_the_passphrase_required(env):
    """Required exactly when the flow it gates becomes reachable. An empty one
    fails closed rather than open -- but silently, and a connector that cannot
    be consented to looks identical to one that is broken."""
    env.setenv("AGENT_BUS_CLOUD_ALLOWLIST", json.dumps({CALLBACK: "desktop:claude"}))
    with pytest.raises(RuntimeError, match="PASSPHRASE"):
        app.config_from_env()


def test_the_handler_it_builds_authenticates_for_real(env):
    """End of the seam: config -> a verifier that really checks a signature."""
    cfg = app.config_from_env()
    verified = cfg.verify(oauth.mint_access("desktop:claude", cfg.oauth.key, "chatgpt"))
    assert verified == ("desktop", "claude")
    assert cfg.verify("not-a-token") is None


def test_the_server_it_starts_can_actually_serve_a_bridge(env):
    """The bug this file exists for, end to end.

    `serve()` dropped `oauth_config`, so /register, /authorize, /token and
    /bridge refused everyone while discovery kept answering -- a deployment
    that passed every health check and could not carry a single message.
    Testing the config alone does not catch it: the config was never the part
    that was wrong.
    """
    import json as _json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    class Store:
        def __init__(self):
            self.queues = {}

        def write(self, q, message):
            self.queues.setdefault(q, []).append(message)
            return "m1"

    store = Store()
    cfg = app.config_from_env()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.handler_for(store, cfg))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        token = oauth.mint_bridge_token("desktop:claude", cfg.oauth.key, ISSUER)
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/bridge",
            data=_json.dumps({"op": "push",
                              "message": {"id": "x", "from": "y", "text": "z"}}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        assert "desktop:claude:inbox" in store.queues
    finally:
        httpd.shutdown()
