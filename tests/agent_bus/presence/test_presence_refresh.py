"""Presence must reflect what a peer is actually doing.

We wrote status "idle" once at startup and never again, so a peer read as idle
in Claude's ListAgents regardless of what it was doing. status/statusUpdatedAt/
cwd/updatedAt are ordinary fields in the session file we already publish.
"""

import json

from agent_bus.listener import (
    publish_status,
    rename_uds_listen,
    touch_published_session,
)
from agent_bus.protocol import AgentTarget


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


# ------------------------------------------------ regressions from PR #9 review


def test_status_reaches_the_roster_not_only_the_session_file(tmp_path):
    """agent-bus list and the MCP list_agents tool read RosterEntry.status,
    which stayed "idle" from registration forever."""
    import subprocess

    from agent_bus.store import find_entry, register, set_status

    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("reporter", "other", pid=holder.pid, home=home)
        assert set_status("busy", AgentTarget("reporter"), home=home)
        assert find_entry(AgentTarget("reporter"), home=home).status == "busy"
    finally:
        holder.kill()
        holder.wait()


def test_status_works_for_a_peer_with_no_listener(tmp_path):
    """A Claude peer publishes no listener, so there is no session file to
    patch. The roster is its status of record; this must not be an error."""
    import subprocess

    from agent_bus.store import find_entry, register, set_status

    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("listenerless", "claude", pid=holder.pid, home=home)
        assert set_status("waiting", AgentTarget("listenerless"), home=home) is True
        assert find_entry(AgentTarget("listenerless"), home=home).status == "waiting"
    finally:
        holder.kill()
        holder.wait()
