"""Tests for store (file bus)."""
import os

import pytest

from agent_bus import store
from agent_bus.store import (
    MAX_TEXT,
    MAX_UNREAD,
    ack_message,
    get_inbox,
    get_self,
    is_pid_alive,
    list_agents,
    prune_dead_roster,
    register,
    send_message,
)


def test_home_and_dirs(tmp_path):
    home = str(tmp_path / "bus")
    os.environ["AGENT_BUS_HOME"] = home
    store.ensure_dirs()
    assert os.path.isdir(os.path.join(home, "roster"))
    assert os.path.isdir(os.path.join(home, "inboxes"))
    assert os.path.isdir(os.path.join(home, "captures"))


def test_register_and_list(tmp_path):
    home = str(tmp_path / "bus")
    os.environ["AGENT_BUS_HOME"] = home
    e = register("test-agent", "other", pid=os.getpid(), cwd=str(tmp_path))
    assert e.name == "test-agent"
    assert e.kind == "other"
    assert e.pid == os.getpid()

    live = list_agents(home=home)
    names = [a.name for a in live]
    assert "test-agent" in names

    # self
    s = get_self(home=home)
    assert s is not None
    assert s.id == e.id


def test_name_collision_suffix(tmp_path):
    home = str(tmp_path / "bus")
    os.environ["AGENT_BUS_HOME"] = home
    register("collide", "other", pid=os.getpid())  # live
    e2 = register("collide", "other", pid=os.getpid())
    assert e2.name == "collide-2"

def test_send_inbox_ack_and_limits(tmp_path):
    home = str(tmp_path / "bus")
    os.environ["AGENT_BUS_HOME"] = home
    sender = register("sender", "other", pid=os.getpid())
    target = register("target", "other", pid=os.getpid())  # live pid for find
    # send
    mid = send_message("target", "hello world", summary="greeting", from_name=sender.name, home=home)
    assert mid

    # inbox for target
    msgs = get_inbox("target", home=home)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "hello world"
    assert not msgs[0]["read"]
    assert msgs[0]["from_"].name == "sender"

    # unread only
    unread = get_inbox("target", unread_only=True, home=home)
    assert len(unread) == 1

    # ack
    ok = ack_message(mid, name_or_id="target", home=home)
    assert ok
    after = get_inbox("target", unread_only=True, home=home)
    assert len(after) == 0

    # cap 50
    for i in range(MAX_UNREAD):
        send_message("target", f"msg{i}", from_name="flood", home=home)
    with pytest.raises(ValueError, match="inbox full"):
        send_message("target", "one more", from_name="flood", home=home)

    # size limit
    big = "x" * (MAX_TEXT + 1)
    with pytest.raises(ValueError, match="too long"):
        send_message("target", big, from_name="big", home=home)


def test_prune_dead(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    os.environ["AGENT_BUS_HOME"] = home
    # register a dead one
    dead_pid = 999999  # unlikely alive
    register("deadone", "other", pid=dead_pid, home=home)

    # ensure in roster
    assert any(e.name == "deadone" for e in store.load_roster(home=home))

    removed = prune_dead_roster(home=home)
    assert removed >= 1
    assert not any(e.name == "deadone" for e in store.load_roster(home=home))

    # list should not show it
    assert all(a.name != "deadone" for a in list_agents(home=home))


def test_discover_merges(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    os.environ["AGENT_BUS_HOME"] = home

    # register self
    register("local", "omp", pid=os.getpid(), home=home)

    # fake discover by monkeypatching inside discover_agents would require import
    # instead, just call list and ensure no crash + local present
    agents = list_agents(home=home)
    assert any(a.name == "local" for a in agents)

def test_is_pid_alive(tmp_path):
    home = str(tmp_path / "bus")
    os.environ["AGENT_BUS_HOME"] = home
    # register a live one
    live_pid = os.getpid()
    register("liveone", "other", pid=live_pid, home=home)
    assert is_pid_alive(live_pid) is True

    # register a dead one
    dead_pid = 999999  # unlikely alive
    register("deadone", "other", pid=dead_pid, home=home)
    assert is_pid_alive(dead_pid) is False

def test_ack_nonexistent(tmp_path):
    home = str(tmp_path / "bus")
    os.environ["AGENT_BUS_HOME"] = home
    register("target", "other", pid=os.getpid(), home=home)
    # ack a non-existent message
    ok = ack_message("nonexistent-id", name_or_id="target", home=home)
    assert ok is False

def test_send_to_nonexistent(tmp_path):
    home = str(tmp_path / "bus")
    os.environ["AGENT_BUS_HOME"] = home
    register("sender", "other", pid=os.getpid(), home=home)
    # send to a non-existent target
    with pytest.raises(ValueError, match="no such agent: nonexistent-target"):
        send_message("nonexistent-target", "hello", from_name="sender", home=home)
