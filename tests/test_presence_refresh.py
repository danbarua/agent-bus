"""Presence must reflect what a peer is actually doing.

We wrote status "idle" once at startup and never again, so a peer read as idle
in Claude's ListAgents regardless of what it was doing. status/statusUpdatedAt/
cwd/updatedAt are ordinary fields in the session file we already publish.
"""

import json
import os

from agent_bus.plugin_host import (
    _listener_pid_path,
    publish_status,
    rename_uds_listen,
    touch_published_session,
)


def _fake_listener(tmp_path, monkeypatch, host_pid=4242, listener_pid=4243):
    """A published session file plus the pid file that points at it."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sessions))
    home = tmp_path / "bus"
    (home / "listeners").mkdir(parents=True)
    (home / "listeners" / f"{host_pid}.pid").write_text(f"{listener_pid}\n")
    sess = sessions / f"{listener_pid}.json"
    sess.write_text(json.dumps({
        "pid": listener_pid, "name": "peer", "status": "idle",
        "statusUpdatedAt": 1, "updatedAt": 1, "cwd": "/old",
    }))
    return home, sess


def test_status_is_published_into_the_session_file(tmp_path, monkeypatch):
    home, sess = _fake_listener(tmp_path, monkeypatch)
    assert publish_status(4242, "busy", home=str(home))
    data = json.loads(sess.read_text())
    assert data["status"] == "busy"
    assert data["statusUpdatedAt"] > 1, "a status change must be stamped"
    assert data["updatedAt"] > 1


def test_cwd_travels_with_status(tmp_path, monkeypatch):
    home, sess = _fake_listener(tmp_path, monkeypatch)
    publish_status(4242, "busy", cwd="/new/place", home=str(home))
    assert json.loads(sess.read_text())["cwd"] == "/new/place"


def test_cwd_is_left_alone_when_not_given(tmp_path, monkeypatch):
    home, sess = _fake_listener(tmp_path, monkeypatch)
    publish_status(4242, "busy", home=str(home))
    assert json.loads(sess.read_text())["cwd"] == "/old"


def test_touch_moves_only_updated_at(tmp_path, monkeypatch):
    """A tool call proves the peer is alive and working, but says nothing about
    idle-vs-busy, so it must not invent a status."""
    home, sess = _fake_listener(tmp_path, monkeypatch)
    assert touch_published_session(4242, home=str(home))
    data = json.loads(sess.read_text())
    assert data["updatedAt"] > 1
    assert data["status"] == "idle"
    assert data["statusUpdatedAt"] == 1


def test_rename_still_works_through_the_shared_patcher(tmp_path, monkeypatch):
    home, sess = _fake_listener(tmp_path, monkeypatch)
    assert rename_uds_listen(4242, "renamed", home=str(home))
    assert json.loads(sess.read_text())["name"] == "renamed"


def test_publishing_without_a_listener_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "nope"))
    home = tmp_path / "bus"
    (home / "listeners").mkdir(parents=True)
    assert publish_status(9999, "busy", home=str(home)) is False
