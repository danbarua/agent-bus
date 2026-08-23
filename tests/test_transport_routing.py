"""One verb, and the bus works out the channel.

Before this, a caller had to know a target's harness to pick a command:
`send` wrote a file inbox, `send-peer` spoke UDS, `send-codex` spawned a codex
app-server. These tests pin that `send` alone now routes by kind, and that a
kind never falls back to a channel its agent does not read.
"""
import json
import os
import subprocess

import pytest

from agent_bus import store
from agent_bus.adapters import discovery, lifecycle, transport
from agent_bus.adapters.contracts import Discovery, HarnessLifecycle, Transport
from agent_bus.commands import messages
from agent_bus.protocol import roster_to_dict


@pytest.fixture
def bus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def holder():
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


# --- the contracts are real, not aspirational -----------------------------

@pytest.mark.parametrize("mod", discovery.ADAPTERS, ids=lambda m: m.KIND)
def test_discovery_adapters_satisfy_the_contract(mod):
    assert isinstance(mod, Discovery)


@pytest.mark.parametrize("mod", lifecycle.ADAPTERS, ids=lambda m: m.KIND)
def test_lifecycle_adapters_satisfy_the_contract(mod):
    assert isinstance(mod, HarnessLifecycle)


@pytest.mark.parametrize("mod", transport.ADAPTERS, ids=lambda m: m.KIND)
def test_transport_adapters_satisfy_the_contract(mod):
    assert isinstance(mod, Transport)


def test_the_capability_matrix_is_sparse():
    """The reason the package is split by capability and not by vendor.

    If these ever coincide, the split has stopped paying for itself.
    """
    disc = {m.KIND for m in discovery.ADAPTERS}
    life = {m.KIND for m in lifecycle.ADAPTERS}
    tran = {m.KIND for m in transport.ADAPTERS}
    assert disc == {"claude", "grok", "omp", "codex"}
    assert life == {"claude", "grok"}
    assert tran == {"claude", "codex"}
    assert life != disc and tran != disc


# --- routing --------------------------------------------------------------

@pytest.mark.parametrize("kind,expected", [
    ("claude", "claude"),
    ("codex", "codex"),
    ("grok", None),
    ("omp", None),
    ("never-heard-of-it", None),
])
def test_for_kind_routes(kind, expected):
    got = transport.for_kind(kind)
    assert (got.KIND if got else None) == expected


def test_a_filebus_kind_is_delivered_to_its_inbox(bus, holder):
    store.register("grok-peer", "grok", pid=holder.pid, home=bus)
    result = messages.send("grok-peer", "hello", home=bus)
    assert result["transport"] == "filebus"
    assert [m["text"] for m in messages.inbox("grok-peer", home=bus)] == ["hello"]


def test_an_unknown_kind_falls_to_the_filebus(bus, holder):
    store.register("mystery", "never-heard-of-it", pid=holder.pid, home=bus)
    assert messages.send("mystery", "hi", home=bus)["transport"] == "filebus"


def test_a_claude_peer_with_no_socket_is_refused_not_silently_filed(bus, holder):
    """The failure that must not become a phantom unread.

    A Claude session never reads a file inbox -- its harness hands it peer
    messages directly. Falling back would report success for a message that
    arrived nowhere, and leave an unread count nobody can ever clear.
    """
    entry = store.register("claude-peer", "claude", pid=holder.pid, home=bus)
    with pytest.raises(ValueError, match="no reachable socket"):
        messages.send("claude-peer", "hello", home=bus)
    assert messages.inbox("claude-peer", home=bus) == []
    assert not os.path.exists(store._inbox_path_for(entry.id, bus))


def test_an_unresolvable_target_is_still_an_error(bus):
    with pytest.raises(ValueError, match="no such agent"):
        messages.send("nobody-here", "hello", home=bus)


# --- the claude transport's address resolution ----------------------------

def test_socket_resolves_from_the_session_file_when_native_is_empty(
    bus, holder, tmp_path, monkeypatch
):
    """A roster entry has native={}; only a discovered one carries the socket.

    Resolving from `native` alone silently failed for every *registered* peer,
    which is exactly the population that reaches this transport by name.
    """
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sessions))
    sock = tmp_path / "peer.sock"
    sock.write_text("")
    (sessions / f"{holder.pid}.json").write_text(json.dumps({
        "pid": holder.pid, "sessionId": "s1", "messagingSocketPath": str(sock),
    }))
    entry = store.register("claude-peer", "claude", pid=holder.pid, home=bus)
    assert transport.claude.socket_for(roster_to_dict(entry)) == str(sock)


def test_socket_is_none_for_a_dead_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path))
    assert transport.claude.socket_for({"name": "x", "pid": None, "native": {}}) is None


# --- codex threads are addressable but never roster entries ---------------

def test_codex_resolution_is_only_tried_after_the_bus_misses(bus, monkeypatch):
    """resolve_unknown must not run during a listing -- it spawns a process."""
    calls = []
    monkeypatch.setattr(transport.codex, "resolve",
                        lambda target: calls.append(target) or None)
    monkeypatch.setattr(transport.claude, "resolve", lambda target: None)
    with pytest.raises(ValueError, match="no such agent"):
        messages.send("some-thread", "hi", home=bus)
    assert calls == ["some-thread"]


def test_a_resolved_codex_thread_is_sent_over_its_own_transport(bus, monkeypatch):
    thread = {"id": "codex:thread:abc", "name": "my-thread", "kind": "codex",
              "pid": None, "cwd": None, "status": "unknown",
              "native": {"threadId": "abc"}}
    monkeypatch.setattr(transport.codex, "resolve", lambda target: thread)
    sent = {}
    monkeypatch.setattr(transport.codex, "send_to_codex",
                        lambda tid, text: sent.update(tid=tid, text=text) or {"id": "q1"})
    result = messages.send("my-thread", "do the thing", home=bus)
    assert sent == {"tid": "abc", "text": "do the thing"}
    assert result["transport"] == "codex-app-server"


def test_a_codex_process_entry_cannot_be_addressed_as_a_thread(bus, holder):
    """Discovery lists codex *processes*; the transport addresses *threads*."""
    store.register("codex-proc", "codex", pid=holder.pid, home=bus)
    with pytest.raises(ValueError, match="not a thread"):
        messages.send("codex-proc", "hi", home=bus)


# --- an address the bus prints is an address the bus accepts ---------------

THREAD_ID = "01a01cb8-1f72-7e71-97ca-69349d003abc"
THREADS = [{"id": THREAD_ID, "name": "Review S-13 architecture direction"}]


@pytest.fixture
def fake_codex(monkeypatch):
    """A CodexAppServer that answers thread/list and records what was queued."""
    from agent_bus.adapters.transport import codex

    queued = []

    class _Server:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def list_threads(self): return list(THREADS)
        def queue_message(self, thread_id, text):
            queued.append((thread_id, text))
            return {"id": "q1"}

    monkeypatch.setattr(codex, "CodexAppServer", _Server)
    monkeypatch.setattr(codex, "_codex_available", lambda: True)
    return queued


@pytest.mark.parametrize("spelling", [
    f"codex:thread:{THREAD_ID}",          # the id the bus itself emits
    THREAD_ID,                            # a bare thread id
    "Review S-13 architecture direction",  # a thread name
])
def test_every_spelling_of_a_thread_reaches_the_same_thread(fake_codex, spelling):
    from agent_bus.adapters.transport import codex

    codex.send_to_codex(spelling, "do the thing")
    assert fake_codex == [(THREAD_ID, "do the thing")]


def test_the_address_we_emit_is_one_we_accept(fake_codex):
    """The property. We were printing `codex:thread:<uuid>` as a resolved
    thread's id and then answering "no such agent" when handed it back."""
    from agent_bus.adapters.transport import codex

    emitted = codex.resolve(THREAD_ID)
    assert emitted is not None
    again = codex.resolve(emitted["id"])
    assert again is not None
    assert again["id"] == emitted["id"]
    assert again["native"]["threadId"] == THREAD_ID


def test_a_pid_address_is_not_mistaken_for_a_thread(fake_codex):
    from agent_bus.adapters.transport import codex

    assert codex._as_thread_id("codex:pid:42") is None
