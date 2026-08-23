"""One agent, one row.

Reproduces the duplicate that was live on the author's machine:

    claude-a4775baa   claude  58291  idle   8054898a-70b8-4f16-...
    exo-ledger        claude  58291  busy   claude:a4775baa-d875-...

One process. Registered under a bus uuid, discovered under the harness's own
session address, merged on id alone -- so the two never reconciled and the one
view whose job is to make harnesses look alike double-counted.
"""
import json
import subprocess

import pytest

from agent_bus import store
from agent_bus.address import SESSION, mint


@pytest.fixture
def bus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path / "bus"))
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sessions))
    store.ensure_dirs(str(tmp_path / "bus"))
    return str(tmp_path / "bus"), sessions


@pytest.fixture
def holder():
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


def _publish_session(sessions, pid, sid, name):
    (sessions / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "name": name, "cwd": "/tmp", "status": "busy",
    }))


def test_a_registered_and_discovered_agent_is_one_row(bus, holder):
    """The exact shape found on disk: a roster entry with no procStart, and a
    session file for the same pid under a different address."""
    home, sessions = bus
    sid = "a4775baa-d875-456c-ab27-1bb45511426d"
    store.register("claude-a4775baa", "claude", pid=holder.pid, home=home)
    _publish_session(sessions, holder.pid, sid, "exo-ledger")

    rows = [a for a in store.list_agents(home=home) if a.pid == holder.pid]
    assert len(rows) == 1, [(r.name, str(r.id)) for r in rows]


def test_the_merged_row_keeps_the_claimed_name_and_takes_live_status(bus, holder):
    """Roster is authoritative for identity, discovery for what changes."""
    home, sessions = bus
    store.register("claimed-name", "claude", pid=holder.pid, home=home)
    _publish_session(sessions, holder.pid, "sid-1", "harness-name")

    row = next(a for a in store.list_agents(home=home) if a.pid == holder.pid)
    assert row.name == "claimed-name"
    assert row.status == "busy"


def test_an_alias_makes_the_link_explicit(bus, holder):
    home, sessions = bus
    sid = "sid-explicit"
    alias = str(mint("claude", SESSION, sid))
    store.register("mine", "claude", pid=holder.pid, home=home, aliases=[alias])
    _publish_session(sessions, holder.pid, sid, "other-name")

    rows = [a for a in store.list_agents(home=home) if a.pid == holder.pid]
    assert len(rows) == 1
    assert store.find_entry(alias, home) is not None


def test_session_start_records_the_harness_address(tmp_path, monkeypatch):
    """describe() always resolved the session id and then discarded it."""
    from agent_bus.lifecycle import SessionDescriptor, session_start

    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        desc = SessionDescriptor(
            kind="grok", session_id="sid-42", pid=holder.pid, cwd="/tmp", name="g"
        )
        entry = session_start(descriptor=desc, home=home)
        assert "grok:session:sid-42" in entry.aliases
        assert entry.native.get("sessionId") == "sid-42"
        assert store.find_entry("grok:session:sid-42", home) is not None
    finally:
        holder.kill(); holder.wait()


def test_two_different_agents_are_still_two_rows(bus, holder):
    """The merge must not collapse genuinely distinct agents."""
    home, sessions = bus
    other = subprocess.Popen(["sleep", "60"])
    try:
        store.register("one", "grok", pid=holder.pid, home=home)
        store.register("two", "grok", pid=other.pid, home=home)
        names = {a.name for a in store.list_agents(home=home)}
        assert {"one", "two"} <= names
    finally:
        other.kill(); other.wait()


def test_a_different_kind_on_the_same_pid_is_not_merged(bus, holder):
    """(kind, pid) not pid alone -- a listener and its host share a pid."""
    home, sessions = bus
    store.register("grok-one", "grok", pid=holder.pid, home=home)
    _publish_session(sessions, holder.pid, "sid-x", "claude-one")
    kinds = {a.kind for a in store.list_agents(home=home) if a.pid == holder.pid}
    assert kinds == {"grok", "claude"}


def test_aliases_survive_a_disk_round_trip(bus, holder):
    home, _ = bus
    store.register("x", "grok", pid=holder.pid, home=home, aliases=["grok:session:s1"])
    assert store.find_entry("x", home).aliases == ["grok:session:s1"]


def test_a_listener_registers_in_the_bus_it_was_given(tmp_path, monkeypatch):
    """start_uds_listen took a `home` and did not pass it to the child.

    The listener is a separate process that registers itself, so a caller that
    set the home by argument rather than by env got a listener registering in
    the *default* bus. Under test that wrote real entries into the developer's
    own ~/.agent-bus on every run.
    """
    import agent_bus.listener as listener

    monkeypatch.delenv("AGENT_BUS_HOME", raising=False)
    captured = {}

    class _Proc:
        pid = 4242

    def _fake_popen(argv, **kw):
        captured["env"] = kw.get("env") or {}
        return _Proc()

    monkeypatch.setattr(listener.subprocess, "Popen", _fake_popen)
    listener.start_uds_listen("some-peer", 999, home=str(tmp_path))
    assert captured["env"].get("AGENT_BUS_HOME") == str(tmp_path)
