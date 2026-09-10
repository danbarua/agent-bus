"""Tests for store (file bus)."""
import json
import os
import subprocess

import pytest

from agent_bus import store
from agent_bus.protocol import AgentTarget, MailboxRef, MessageId
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


@pytest.fixture
def live_child_pid():
    proc = subprocess.Popen(["sleep", "60"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def test_home_and_dirs(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    store.ensure_dirs()
    assert os.path.isdir(os.path.join(home, "roster"))
    assert os.path.isdir(os.path.join(home, "inboxes"))
    assert not os.path.isdir(os.path.join(home, "captures")), (
        "captures/ was an always-on copy of every frame, content included, in "
        "a directory nobody asked for. log.trace replaced it."
    )


def test_register_and_list(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
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


def test_name_collision_suffix(tmp_path, live_child_pid, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    register("collide", "other", pid=os.getpid(), home=home)
    e2 = register("collide", "other", pid=live_child_pid, home=home)
    assert e2.name == "collide-2"

def test_send_inbox_ack_and_limits(tmp_path, live_child_pid, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    sender = register("sender", "other", pid=os.getpid(), home=home)
    register("target", "other", pid=live_child_pid, home=home)
    # send
    mid = send_message(
        AgentTarget("target"), "hello world", summary="greeting", from_name=AgentTarget(
            sender.name,
        ), home=home
    )
    assert mid

    # inbox for target
    msgs = get_inbox(AgentTarget("target"), home=home)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "hello world"
    assert not msgs[0]["read"]
    assert msgs[0]["from_"].name == "sender"

    # unread only
    unread = get_inbox(AgentTarget("target"), unread_only=True, home=home)
    assert len(unread) == 1

    # ack
    ok = ack_message(mid, target=AgentTarget("target"), home=home)
    assert ok
    after = get_inbox(AgentTarget("target"), unread_only=True, home=home)
    assert len(after) == 0

    # cap 50
    for i in range(MAX_UNREAD):
        send_message(AgentTarget("target"), f"msg{i}", from_name=AgentTarget("flood"), home=home)
    with pytest.raises(ValueError, match="inbox full"):
        send_message(AgentTarget("target"), "one more", from_name=AgentTarget("flood"), home=home)

    # size limit
    big = "x" * (MAX_TEXT + 1)
    with pytest.raises(ValueError, match="too long"):
        send_message(AgentTarget("target"), big, from_name=AgentTarget("big"), home=home)


def test_prune_dead(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
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
    monkeypatch.setenv("AGENT_BUS_HOME", home)

    # register self
    register("local", "omp", pid=os.getpid(), home=home)

    # fake discover by monkeypatching inside discover_agents would require import
    # instead, just call list and ensure no crash + local present
    agents = list_agents(home=home)
    assert any(a.name == "local" for a in agents)

def test_is_pid_alive(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    # register a live one
    live_pid = os.getpid()
    register("liveone", "other", pid=live_pid, home=home)
    assert is_pid_alive(live_pid) is True

    # register a dead one
    dead_pid = 999999  # unlikely alive
    register("deadone", "other", pid=dead_pid, home=home)
    assert is_pid_alive(dead_pid) is False

def test_ack_nonexistent(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    register("target", "other", pid=os.getpid(), home=home)
    # ack a non-existent message
    ok = ack_message(MessageId("nonexistent-id"), target=AgentTarget("target"), home=home)
    assert ok is False

def test_send_to_nonexistent(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    register("sender", "other", pid=os.getpid(), home=home)
    # send to a non-existent target
    with pytest.raises(ValueError, match="no such agent: nonexistent-target"):
        send_message(AgentTarget("nonexistent-target"), "hello", from_name=AgentTarget(
            "sender",
        ), home=home)


def test_register_same_pid_is_idempotent(tmp_path, monkeypatch):
    """SessionStart/resume must not mint collide-2 for the same live pid."""
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    first = register("host", "grok", pid=os.getpid(), cwd="/tmp/a", home=home)
    second = register("host", "grok", pid=os.getpid(), cwd="/tmp/b", home=home)
    assert second.id == first.id
    assert second.name == "host"
    assert second.cwd == "/tmp/b"


def test_get_self_and_inbox_follow_ancestor_pid(tmp_path, monkeypatch):
    """Tool shells are children of the host agent; inbox without --name must still resolve."""
    import subprocess
    import sys

    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    register("host-agent", "grok", pid=os.getpid(), home=home)
    mid = send_message(
        AgentTarget("host-agent"), "ping from peer", from_name=AgentTarget("peer"), home=home
    )

    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "../..", "src"))
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = home
    env["PYTHONPATH"] = src
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            ("from agent_bus.store import get_self, get_inbox, ack_message\n"
            "s = get_self()\n"
            "assert s is not None, 'get_self missed ancestor'\n"
            "print(s.name)\n"
            "msgs = get_inbox(unread_only=True)\n"
            "assert msgs and msgs[0]['text'] == 'ping from peer'\n"
            "assert ack_message(msgs[0]['id'])\n"),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "host-agent"
    assert mid


# --- which session a command is running inside ----------------------------

def _discovered(pid, name="a-session"):
    from agent_bus.store import RosterEntry
    return RosterEntry(
        id=MailboxRef(f"claude:{name}"), name=name, kind="claude", pid=pid, cwd=None,
        status="idle", inbox="", native={}, registeredAt="", updatedAt="",
    )


def test_session_lookup_walks_ancestors_not_just_the_parent(monkeypatch):
    """A shell two levels below its session must still find it.

    The CLI's own parent is whatever wrapper launched it -- `uv run` inserts
    one -- so a parent-only lookup registers a process that dies with the
    command.
    """
    monkeypatch.setattr(store, "ancestor_pids", lambda start=None: [11, 22, 33])
    monkeypatch.setattr(store, "discover_agents", lambda home=None: [_discovered(33)])
    found = store.session_entry_for_current_process()
    assert found is not None
    assert found.pid == 33


def test_session_lookup_takes_the_nearest_session(monkeypatch):
    """Sessions nest. The innermost one owns the command that ran."""
    monkeypatch.setattr(store, "ancestor_pids", lambda start=None: [11, 22, 33])
    monkeypatch.setattr(
        store, "discover_agents",
        lambda home=None: [_discovered(33, "outer"), _discovered(22, "inner")],
    )
    inner = store.session_entry_for_current_process()
    assert inner is not None, "no session entry for this process"
    assert inner.name == "inner"


def test_session_lookup_is_none_when_no_ancestor_is_a_session(monkeypatch):
    """Silence here is what lets the CLI refuse instead of guessing."""
    monkeypatch.setattr(store, "ancestor_pids", lambda start=None: [11, 22])
    monkeypatch.setattr(store, "discover_agents", lambda home=None: [_discovered(99)])
    assert store.session_entry_for_current_process() is None


def test_nearest_ancestor_match_prefers_the_newer_entry_on_a_pid_collision(monkeypatch):
    """Two entries sharing a pid (a dead-but-retained one and a live one,
    after recycling) is possible for a caller that passes an unfiltered
    roster. Picked by updatedAt, not dict-insertion order, which
    `load_roster`'s `os.listdir` never guarantees."""
    from agent_bus.store import RosterEntry, _nearest_ancestor_match

    monkeypatch.setattr(store, "ancestor_pids", lambda start=None: [42])
    older = RosterEntry(
        id=MailboxRef("old"), name="old-thing", kind="claude", pid=42, cwd=None,
        status="idle", inbox="", native={},
        registeredAt="2026-01-01T00:00:00Z", updatedAt="2026-01-01T00:00:00Z",
    )
    newer = RosterEntry(
        id=MailboxRef("new"), name="reviewer", kind="omp", pid=42, cwd=None,
        status="idle", inbox="", native={},
        registeredAt="2026-01-02T00:00:00Z", updatedAt="2026-01-02T00:00:00Z",
    )
    # Order reversed from insertion-order intuition, on purpose -- the
    # tiebreak must not depend on which one came first in the list.
    found_a = _nearest_ancestor_match([newer, older])
    found_b = _nearest_ancestor_match([older, newer])
    assert found_a is not None and found_a.name == "reviewer"
    assert found_b is not None and found_b.name == "reviewer"


def test_nearest_ancestor_match_tolerates_a_null_updated_at(tmp_path, monkeypatch):
    """`load_roster` accepts a roster file with `"updatedAt": null` --
    `dict_to_roster` assigns it straight through, and a missing key (KeyError)
    is the only shape `load_roster`'s per-file except clause catches. Sorting
    on that field must not crash `get_self` just because one file on disk
    was hand-written or migrated.

    A `thread`-space id is used for the null entry because it is live
    regardless of pid (`adapters/addressing/thread.py`) -- a pid-keyed space
    would just get pruned as dead before ever reaching the sort, proving
    nothing."""
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    register("current", "other", pid=os.getpid(), home=home)

    roster_dir = store.roster_dir(home=home)
    stale_path = os.path.join(roster_dir, "null-updated-at.json")
    with open(stale_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "id": "codex:thread:null-updated-at",
                "name": "null-updated-at",
                "kind": "codex",
                "pid": None,
                "cwd": None,
                "status": "idle",
                "inbox": "",
                "native": {},
                "registeredAt": "2026-01-01T00:00:00Z",
                "updatedAt": None,
            },
            f,
        )

    s = store.get_self(home=home)
    assert s is not None
    assert s.name == "current"


def test_list_agents_tolerates_a_null_name_or_kind(tmp_path, monkeypatch):
    """`list_agents`' own sort (`agents.sort(key=lambda a: (a.kind, a.name,
    a.id))`) has the same None-comparison exposure as the ancestor-match
    sort: `dict_to_roster` assigns `name`/`kind` straight through with no
    validation, and a roster file with an explicit `"name": null` or
    `"kind": null` constructs fine -- `load_roster`'s per-file except only
    catches a missing key. Nothing must crash `agent-bus list` over it.

    A `thread`-space id, same reason and same trick as the sibling
    ancestor-match test: `list_agents` calls `get_live_roster`, which drops
    anything `addressing.is_live` says is dead, and a pid-keyed entry with
    `pid=None` would be filtered out before ever reaching the sort. Asserted
    explicitly rather than inferred from `"current"` alone, so the test
    fails loudly if that ever stops being true instead of passing for the
    wrong reason."""
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    register("current", "other", pid=os.getpid(), home=home)

    roster_dir = store.roster_dir(home=home)
    stale_path = os.path.join(roster_dir, "null-fields.json")
    with open(stale_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "id": "codex:thread:null-fields",
                "name": None,
                "kind": None,
                "pid": None,
                "cwd": None,
                "status": "idle",
                "inbox": "",
                "native": {},
                "registeredAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            f,
        )

    names = [a.name for a in list_agents(home=home)]
    assert None in names, (
        "test setup: the null-name entry must actually reach the sort, or "
        "this test passes without exercising the fix at all"
    )
    assert "current" in names


def test_ancestor_pids_walks_once_and_caches_after(monkeypatch):
    """`_who()` (log.py) calls this on every single log record, and on any
    machine with no /proc every hop shells out to a real `ps` process
    (`_parent_pid`) -- so a second call must not walk again."""
    monkeypatch.setattr(store, "_ANCESTOR_PIDS_CACHE", None)
    calls: list[int] = []
    real_parent_pid = store._parent_pid

    def counting(pid):
        calls.append(pid)
        return real_parent_pid(pid)

    monkeypatch.setattr(store, "_parent_pid", counting)

    first = store.ancestor_pids()
    walked = len(calls)
    assert walked > 0, "this process has at least one ancestor to walk to"

    # The cache hit path (second call onward), not the first: the first
    # call always returns a freshly built list either way, so mutating it
    # proves nothing about whether the cache hands out its own object.
    second = store.ancestor_pids()
    assert second == first
    assert len(calls) == walked, "a second call must not walk again"

    second.append(999999)
    assert store.ancestor_pids() == first, (
        "mutating a returned list must not corrupt what the next call returns"
    )
