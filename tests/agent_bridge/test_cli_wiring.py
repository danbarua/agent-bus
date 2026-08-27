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
