"""Liveness belongs to the address space now, not to a pid check.

The invariant these pin:

    live  =>  listed          and    (not live and no unread mail)  =>  pruned

No entry is ever both permanently on disk and permanently invisible, which is
what a pid-less entry was before: `prune_dead_roster` skipped it as unprunable
while `get_live_roster` filtered it out as dead.
"""
import os
import subprocess

import pytest

from agent_bus import store
from agent_bus.protocol import RosterEntry, now_iso


@pytest.fixture
def bus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path))
    store.ensure_dirs(str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def holder():
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


def _dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def _write(bus, entry_id, kind, pid, name="x"):
    e = RosterEntry(
        id=entry_id, name=name, kind=kind, pid=pid, cwd="/tmp", status="idle",
        inbox=store._make_inbox_ref(entry_id, bus), native={},
        registeredAt=now_iso(), updatedAt=now_iso(),
    )
    store.save_roster_entry(e, bus)
    return e


# --- the asymmetry, which was the centre of the change --------------------

def test_a_pidless_thread_entry_is_listed_and_never_pruned(bus):
    """Before: invisible (get_live_roster filtered pid=None) AND immortal
    (prune skipped `not entry.pid`). The worst of both."""
    _write(bus, "codex:thread:abc-123", "codex", None, name="my-thread")
    assert store.prune_dead_roster(bus) == 0
    assert [e.name for e in store.get_live_roster(bus)] == ["my-thread"]


def test_a_pidless_mailbox_entry_with_no_mail_is_pruned(bus):
    """The other half: no immortal invisibles left behind either."""
    _write(bus, "11111111-2222-3333-4444-555555555555", "other", None)
    assert store.prune_dead_roster(bus) == 1
    assert store.get_live_roster(bus) == []


def test_a_dead_process_with_unread_mail_is_still_retained(bus, holder):
    """Retention is unchanged -- mail still outranks presence."""
    entry = store.register("target", "grok", pid=holder.pid, home=bus)
    store.send_message(to=entry.id, text="queued", from_name="sender", home=bus)
    holder.kill(); holder.wait()
    assert store.prune_dead_roster(bus) == 0
    assert store.find_entry("target", bus) is not None


def test_a_live_registered_agent_is_untouched(bus, holder):
    store.register("live-one", "grok", pid=holder.pid, home=bus)
    store.prune_dead_roster(bus)
    assert [e.name for e in store.get_live_roster(bus)] == ["live-one"]


def test_a_dead_process_without_mail_is_pruned(bus):
    _write(bus, "22222222-3333-4444-5555-666666666666", "other", _dead_pid())
    assert store.prune_dead_roster(bus) == 1


# --- filenames ------------------------------------------------------------

def test_existing_inbox_paths_do_not_move(bus):
    """Every id on disk today is substitution-free, so its filename is unchanged.
    Rewriting these would re-orphan the very inboxes this work recovers."""
    for real_id in [
        "claude:26bc255e-aee7-43dd-8c3b-ff7a84015756",
        "8054898a-70b8-4f16-9a80-18dcf93f14c2",
        "omp:tty:1234",
        "codex:thread:01a01cb8-1f72-7e71-97ca-69349d003abc",
    ]:
        assert os.path.basename(store._inbox_path_for(real_id, bus)) == f"{real_id}.jsonl"


def test_safe_id_is_injective(bus):
    """`a/b`, `a b` and `a_b` all named one file -- two agents, one inbox."""
    ids = ["a/b", "a b", "a_b", "a:b"]
    assert len({store._safe_id_for_fs(i) for i in ids}) == len(ids)


def test_two_colliding_ids_get_separate_inboxes(bus):
    assert store._inbox_path_for("a/b", bus) != store._inbox_path_for("a b", bus)


# --- the mailbox guard ----------------------------------------------------

def test_writing_to_a_thread_address_is_refused_and_creates_no_file(bus):
    entry = _write(bus, "codex:thread:abc-123", "codex", None, name="my-thread")
    before = set(os.listdir(store._inbox_dir(bus)))
    with pytest.raises(ValueError, match="no bus mailbox"):
        store.send_message(to="my-thread", text="hi", home=bus)
    assert set(os.listdir(store._inbox_dir(bus))) == before
    assert not os.path.exists(store._inbox_path_for(entry.id, bus))


def test_writing_to_a_discovered_claude_session_is_refused(bus, holder):
    """The orphan-creation path: a discovered claude peer persisted and written
    to, then pruned when its pid died, leaving unreadable mail behind."""
    _write(bus, "claude:sid-abc", "claude", holder.pid, name="a-claude")
    with pytest.raises(ValueError, match="no bus mailbox"):
        store.send_message(to="a-claude", text="hi", home=bus)


def test_a_registered_claude_agent_still_has_a_mailbox(bus, holder):
    """Consent is the line: register() asks to be on the bus, mailbox included.
    Being noticed by discovery does not."""
    entry = store.register("claude-that-asked", "claude", pid=holder.pid, home=bus)
    mid = store.send_message(to=entry.id, text="hi", from_name="s", home=bus)
    assert mid


# --- nothing enumerates codex threads ------------------------------------

def test_no_listing_ever_spawns_a_codex_app_server(bus, holder, monkeypatch):
    """Threads reach the bus by registering, never by enumeration. This guard
    makes re-adding a thread/list call to any listing path a build failure."""
    from agent_bus.adapters.transport import codex

    def _boom(*a, **k):
        raise AssertionError("a listing spawned codex app-server")

    monkeypatch.setattr(codex.CodexAppServer, "__init__", _boom)
    store.register("someone", "grok", pid=holder.pid, home=bus)
    store.list_agents(home=bus)
    store.list_agents(kind="codex", home=bus)
    store.get_live_roster(bus)
    store.discover_agents(bus)


def test_discovery_admits_an_agent_with_no_process(bus, monkeypatch):
    """The gate that used to be a bare pid check.

    No adapter yields a pid-less entry today, so nothing else exercises this --
    but it is the single line that would block a registered Codex thread from
    ever being discovered, which is the seat this change leaves open.
    """
    import agent_bus.adapters as adapters_pkg
    monkeypatch.setattr(adapters_pkg, "discover_all", lambda: [{
        "id": "codex:thread:abc-123", "name": "my-thread", "kind": "codex",
        "pid": None, "cwd": None, "status": "unknown", "native": {"threadId": "abc-123"},
    }])
    found = {e.name: e for e in store.discover_agents(bus)}
    assert "my-thread" in found, found
    assert found["my-thread"].pid is None


def test_discovery_still_drops_a_process_backed_agent_whose_process_died(bus, monkeypatch):
    import agent_bus.adapters as adapters_pkg
    monkeypatch.setattr(adapters_pkg, "discover_all", lambda: [{
        "id": "grok:sid-gone", "name": "ghost", "kind": "grok",
        "pid": _dead_pid(), "cwd": None, "status": "unknown", "native": {},
    }])
    assert [e.name for e in store.discover_agents(bus)] == []
