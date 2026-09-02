"""Which cloud the bridge picks, and how loudly it says so.

The failure this pins is silence: a bridge that spools when the operator
installed a token has not failed, it has *looked* like it worked, and the mail
sits in a directory nobody opens.
"""

from __future__ import annotations

import pytest

from agent_bridge.bridge import HttpCloudClient, SpoolClient
from agent_bridge.cli import _client


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path))
    return tmp_path


def _token(address="desktop:claude", issuer="https://bus.example"):
    import os
    import sys
    cloud = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "cloud")
    if cloud not in sys.path:
        sys.path.insert(0, cloud)
    oauth = pytest.importorskip("oauth")
    return oauth.mint_bridge_token(address, b"\x05" * 32, issuer)


def test_a_token_is_the_whole_of_connecting_to_the_cloud(home, capsys):
    """No flag, no env var. Drop the file in and the bridge is connected."""
    (home / "cloud-token").write_text(_token())
    client = _client(None)
    assert isinstance(client, HttpCloudClient)
    assert client.base_url == "https://bus.example"
    assert "https://bus.example" in capsys.readouterr().err


def test_no_token_spools_and_says_where(home, capsys):
    client = _client(None)
    assert isinstance(client, SpoolClient)
    assert "spooling to" in capsys.readouterr().err


def test_spool_dir_wins_over_an_installed_token(home, capsys, tmp_path):
    """The way to work offline on a machine that has a token, without moving
    the token somewhere it will be forgotten."""
    (home / "cloud-token").write_text(_token())
    client = _client(str(tmp_path / "elsewhere"))
    assert isinstance(client, SpoolClient)
    # Asked for explicitly, so no notice: the operator knows.
    assert capsys.readouterr().err == ""


# ------------------------------------------------- where the token comes from


def test_the_environment_beats_the_keychain(monkeypatch, tmp_path):
    """The Keychain holds exactly one item, so without this every bridge on a
    machine resolves the same credential and therefore the same deployment --
    and a second bridge pointed at staging is not expressible at all."""
    from agent_bridge import bridge as b

    monkeypatch.setattr(b, "_keychain_token", lambda: "keychain-token")
    monkeypatch.setenv(b.TOKEN_ENV, "env-token")
    assert b.token_source(str(tmp_path)) == "environment"


def test_blank_in_the_environment_is_not_a_token(monkeypatch, tmp_path):
    """`export AGENT_BUS_CLOUD_TOKEN=` in a shell profile must not shadow a
    working Keychain item with an empty string."""
    from agent_bridge import bridge as b

    monkeypatch.setattr(b, "_keychain_token", lambda: "keychain-token")
    monkeypatch.setenv(b.TOKEN_ENV, "   ")
    assert b.token_source(str(tmp_path)) == "keychain"


def test_the_env_token_is_the_one_actually_used(monkeypatch, tmp_path):
    """`token_source` reporting "environment" while `read_cloud_token` returned
    the Keychain's would be worse than not having the feature: the startup line
    would name a deployment the bridge was not talking to."""
    import base64
    import json

    from agent_bridge import bridge as b

    def _tok(iss):
        # `payload.signature`, two segments -- the shape `cloud/oauth.py` mints,
        # not a three-segment JWT. Claims are the *first* segment.
        claims = base64.urlsafe_b64encode(
            json.dumps({"iss": iss}, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        return f"{claims}.signature"

    monkeypatch.setattr(b, "_keychain_token", lambda: _tok("https://prod.invalid"))
    monkeypatch.setenv(b.TOKEN_ENV, _tok("https://staging.invalid"))
    url, _ = b.read_cloud_token(str(tmp_path))
    assert url == "https://staging.invalid", "the reported source and the used token disagree"


# ----------------------------------------------------------------- the verbs


def test_start_is_a_verb_and_carries_the_address():
    """#218. `agent-bridge` was flags only, so there was nowhere to put a
    query -- which is what #219 needs. `agent-bus mcp` is the shape: a verb
    runs the long-running process, the others are commands."""
    from agent_bridge.cli import build_parser

    ns = build_parser().parse_args(["start", "--kind", "desktop", "--name", "claude"])
    assert ns.cmd == "start"
    assert (ns.kind, ns.name) == ("desktop", "claude")
    assert ns.func.__name__ == "cmd_start"


def test_the_bare_flag_form_is_gone_rather_than_shimmed():
    """Dropped, not kept working. Nothing outside this machine runs it, and a
    shim outlives the thing it shims -- the migration is to stop the service,
    uninstall it, and install the new one.

    It has to fail *loudly*: a plist still using the old form would otherwise
    install a job that exits every 60 seconds under `KeepAlive`.
    """
    import pytest

    from agent_bridge.cli import build_parser

    with pytest.raises(SystemExit) as e:
        build_parser().parse_args(["--kind", "desktop", "--name", "claude"])
    assert e.value.code != 0


def test_a_verb_is_required():
    """Bare `agent-bridge` used to start a daemon. It must not now do something
    surprising instead."""
    import pytest

    from agent_bridge.cli import build_parser

    with pytest.raises(SystemExit) as e:
        build_parser().parse_args([])
    assert e.value.code != 0
