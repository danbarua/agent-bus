"""Every exception the bridge turns into a fallback either leaves a record or is
an ordinary branch. These are the ones that leave a record, and the ones that
must stay quiet."""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_bridge import bridge as b
from agent_bridge.bridge import SpoolClient, read_cloud_token
from agent_bus.protocol import BridgeAddress

ADDRESS = BridgeAddress("desktop:claude")

# Captured at import, before the suite-wide net replaces it per test.
_REAL_KEYCHAIN_TOKEN = b._keychain_token


@pytest.fixture(autouse=True)
def _real_keychain_reader(monkeypatch):
    monkeypatch.setattr(b, "_keychain_token", _REAL_KEYCHAIN_TOKEN)


def _records(dest):
    return [json.loads(line) for line in dest.read_text().splitlines()]


def _nothing_written(dest):
    return not dest.exists() or _records(dest) == []


def _security(monkeypatch, *, returncode=0, stdout="", stderr="", raises=None):
    def run(*_a, **_k):
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)


def test_no_security_binary_is_an_ordinary_branch(bridge_log, monkeypatch):
    _security(monkeypatch, raises=FileNotFoundError("security"))
    assert b._keychain_token() is None
    assert _nothing_written(bridge_log)


def test_no_such_keychain_item_is_an_ordinary_branch(bridge_log, monkeypatch):
    _security(monkeypatch, returncode=44)
    assert b._keychain_token() is None
    assert _nothing_written(bridge_log)


def test_a_hung_keychain_says_so_because_the_file_fallback_may_be_stale(bridge_log, monkeypatch):
    _security(monkeypatch, raises=subprocess.TimeoutExpired("security", 10))
    assert b._keychain_token() is None
    (rec,) = _records(bridge_log)
    assert rec["message"] == "keychain_unreadable"
    assert rec["severity"] == "WARNING"
    assert rec["error"] == "TimeoutExpired"


def test_a_locked_keychain_says_so(bridge_log, monkeypatch):
    _security(monkeypatch, returncode=36, stderr="User interaction is not allowed.")
    assert b._keychain_token() is None
    (rec,) = _records(bridge_log)
    assert rec["message"] == "keychain_unreadable"
    assert "36" in rec["error_message"]
    assert "User interaction is not allowed" in rec["error_message"]
    assert "error" not in rec


def test_a_keychain_secret_never_reaches_the_record(bridge_log, monkeypatch):
    _security(monkeypatch, returncode=36, stdout="s3cr3t-token", stderr="locked")
    b._keychain_token()
    assert "s3cr3t-token" not in bridge_log.read_text()


def test_a_missing_token_file_is_an_ordinary_branch(bridge_log, tmp_path, monkeypatch):
    monkeypatch.delenv(b.TOKEN_ENV, raising=False)
    _security(monkeypatch, returncode=44)
    assert read_cloud_token(str(tmp_path / "empty")) is None
    assert _nothing_written(bridge_log)


def test_a_token_file_that_cannot_be_read_says_so(bridge_log, tmp_path, monkeypatch):
    monkeypatch.delenv(b.TOKEN_ENV, raising=False)
    _security(monkeypatch, returncode=44)
    (tmp_path / "cloud-token").mkdir()
    assert read_cloud_token(str(tmp_path)) is None
    (rec,) = _records(bridge_log)
    assert rec["message"] == "token_file_unreadable"
    assert rec["error"] == "IsADirectoryError"


def test_a_corrupt_spool_file_is_skipped_once_and_the_rest_still_arrive(bridge_log, tmp_path):
    spool = SpoolClient(str(tmp_path))
    inbound = tmp_path / ADDRESS / "inbound"
    inbound.mkdir(parents=True)
    (inbound / "bad.json").write_text("{nope")
    (inbound / "good.json").write_text(json.dumps({"to": "x", "text": "hi"}))

    first = spool.pull(ADDRESS)
    second = spool.pull(ADDRESS)

    assert [m["id"] for m in first] == [m["id"] for m in second] == ["good"]
    (rec,) = _records(bridge_log)
    assert rec["message"] == "spool_file_skipped"
    assert rec["trace_id"] == "bad"
    assert rec["error"] == "JSONDecodeError"


def test_acking_a_message_that_is_already_gone_is_an_ordinary_branch(tmp_path):
    SpoolClient(str(tmp_path)).ack(ADDRESS, ["never-existed"])


def test_an_ack_that_cannot_remove_the_file_raises_so_it_is_not_silent(tmp_path):
    inbound = tmp_path / ADDRESS / "inbound"
    (inbound / "m1.json").mkdir(parents=True)
    with pytest.raises(OSError):
        SpoolClient(str(tmp_path)).ack(ADDRESS, ["m1"])


def test_a_corrupt_subscriptions_file_is_reported_not_read_as_empty(tmp_path):
    spool = SpoolClient(str(tmp_path))
    d = tmp_path / ADDRESS
    d.mkdir(parents=True)
    (d / "subscriptions.json").write_text("{nope")
    with pytest.raises(json.JSONDecodeError):
        spool.subscriptions(ADDRESS, None)
    assert spool.subscriptions(BridgeAddress("desktop:other"), None) == {}
