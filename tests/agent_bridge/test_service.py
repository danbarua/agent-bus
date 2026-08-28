"""What a bridge has to get right to run for a month unattended.

Three things, and each of them is a branch that fires rarely enough to have
never been executed: the poll schedule, which changes only when traffic does;
the credential lookup, which has two sources and prefers the invisible one; and
the expiry warning, which matters on one day out of thirty.

They are pure functions here on purpose. A poll interval you can only observe
by waiting is one nobody will ever change, and a warning first exercised on the
day it is needed is one that has never been run.
"""

from __future__ import annotations

import subprocess

import pytest

from agent_bridge import bridge as b

DAY = 86400.0


# ------------------------------------------------------------ the poll schedule


def test_it_polls_fast_inside_the_busy_window():
    """A conversation is bursty, and the reply is the leg anyone waits for."""
    assert b.inbound_interval(0.0, idle=120.0) == b.INBOUND_POLL_BUSY_SECONDS
    assert b.inbound_interval(59.0, idle=120.0) == b.INBOUND_POLL_BUSY_SECONDS


def test_it_falls_back_to_idle_once_nothing_has_moved():
    """The cost being paid is ~5,600 requests a day for a handful of messages."""
    assert b.inbound_interval(60.0, idle=120.0) == 120.0
    assert b.inbound_interval(6000.0, idle=120.0) == 120.0


def test_an_idle_interval_below_the_busy_one_turns_adaptation_off():
    """`busy` is clamped to `idle`, so `inbound_poll=0` still means 0.

    Without the clamp, asking for every-pass polling would silently get a
    five-second floor -- and the tests that drive the loop would hang rather
    than fail, which is the worse of the two.
    """
    assert b.inbound_interval(0.0, idle=0.0) == 0.0
    assert b.inbound_interval(0.0, idle=1.0) == 1.0


# ------------------------------------------------------------- the credential


def test_the_keychain_wins_over_a_file(tmp_path, monkeypatch):
    """A file left behind after moving the token to the Keychain must not keep
    being used -- silently, and for as long as it stays valid."""
    (tmp_path / "cloud-token").write_text(_token("https://from-the-file"))
    monkeypatch.setattr(b, "_keychain_token",
                        lambda: _token("https://from-the-keychain"))

    url, _ = b.read_cloud_token(str(tmp_path))
    assert url == "https://from-the-keychain"


def test_the_file_still_works_when_there_is_no_keychain(tmp_path, monkeypatch):
    """Not every machine that runs this is a Mac, and a service that starts
    before the Keychain unlocks still has to start."""
    (tmp_path / "cloud-token").write_text(_token("https://from-the-file"))
    monkeypatch.setattr(b, "_keychain_token", lambda: None)

    url, _ = b.read_cloud_token(str(tmp_path))
    assert url == "https://from-the-file"


@pytest.mark.parametrize("outcome", ["missing-binary", "no-such-item", "timeout"])
def test_every_keychain_failure_is_just_absence(monkeypatch, outcome):
    """None, never an exception. A bridge that could have run from its file
    must not refuse to because `security` was unhappy."""
    def _run(*a, **kw):
        if outcome == "missing-binary":
            raise FileNotFoundError("security")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("security", 10)
        return subprocess.CompletedProcess(a[0], 44, stdout="", stderr="not found")

    monkeypatch.setattr(subprocess, "run", _run)
    assert b._keychain_token() is None


def test_the_startup_line_can_say_where_the_token_came_from(tmp_path, monkeypatch):
    """Two places can hold one and only one is visible in a directory listing,
    so "which is live" is the first question a 401 raises."""
    monkeypatch.setattr(b, "_keychain_token", lambda: None)
    assert b.token_source(str(tmp_path)) == "none"

    (tmp_path / "cloud-token").write_text(_token("https://x"))
    assert b.token_source(str(tmp_path)) == "file"

    monkeypatch.setattr(b, "_keychain_token", lambda: _token("https://x"))
    assert b.token_source(str(tmp_path)) == "keychain"


# ---------------------------------------------------------------- the expiry


def test_it_says_nothing_while_there_is_plenty_of_time():
    """A warning printed every day for a month is one nobody reads on day 29."""
    assert b.expiry_warning(1000 * DAY, now=990 * DAY) is None


def test_it_warns_before_the_token_runs_out_and_says_when():
    now = 1000 * DAY
    warning = b.expiry_warning(now + 3 * DAY, now=now)
    assert warning is not None
    assert "3.0 days" in warning, warning


def test_an_expired_token_says_so_rather_than_counting_down():
    """The failure is already happening, and "expires in -2 days" is a sentence
    someone has to stop and parse while their bridge is down."""
    now = 1000 * DAY
    warning = b.expiry_warning(now - 2 * DAY, now=now)
    assert warning is not None
    assert "EXPIRED" in warning and "2.0 days ago" in warning, warning


def test_a_token_with_no_expiry_is_not_invented():
    assert b.expiry_warning(None, now=1000 * DAY) is None
    assert b.token_expiry("not-a-token") is None


def _token(issuer: str) -> str:
    import base64
    import json

    claims = base64.urlsafe_b64encode(
        json.dumps({"iss": issuer, "exp": 4 * 10**9}).encode()
    ).decode().rstrip("=")
    return f"{claims}.signature"


# ------------------------------------------------------------- the plist itself


def test_the_plist_template_substitutes_to_something_launchd_can_read():
    """`plutil -lint` says OK on a plist whose comments contain `--`, and then
    the parser drops every key after the first one.

    Ten keys become four, and the service launches with no KeepAlive, no log
    path and no arguments. Nothing about the file looks wrong, which is the
    argument for parsing the result rather than reading it.
    """
    import os
    import plistlib
    import re

    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(
        repo, "packaging", "launchd", "ai.framesift.agent-bridge.plist.template"
    )
    with open(path, encoding="utf-8") as f:
        template = f.read()

    for comment in re.findall(r"<!--.*?-->", template, re.S):
        assert "--" not in comment[4:-3], (
            f"`--` inside an XML comment is illegal and parses as truncation, "
            f"not as an error: {comment[:80]!r}"
        )

    filled = template
    for key, value in (("__LABEL__", "desktop-claude"), ("__KIND__", "desktop"),
                       ("__NAME__", "claude"), ("__BIN__", "/opt/bin"),
                       ("__LOGS__", "/tmp/logs")):
        filled = filled.replace(key, value)
    assert "__" not in filled, "a placeholder the documented sed does not fill"

    plist = plistlib.loads(filled.encode())
    assert plist["Label"] == "ai.framesift.agent-bridge.desktop-claude"
    assert plist["ProgramArguments"] == [
        "/opt/bin/agent-bridge", "--kind", "desktop", "--name", "claude",
    ]
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] >= 30, (
        "this polls a billed endpoint; launchd's 10s default turns a bad "
        "token into a bill"
    )
    assert plist["StandardOutPath"] == "/tmp/logs/desktop-claude.log"
