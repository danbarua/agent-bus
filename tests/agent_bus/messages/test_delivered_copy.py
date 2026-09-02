"""The durable copy kept when a native transport already delivered.

Claude and Codex never poll a bus inbox -- their own transport hands them the
message. They still get a record, written already-read, so that every peer runs
one code path and an *unread* means something precise: the transport failed.

That is what dissolved NO_MAILBOX_KINDS. The exclusion existed because a file
inbox for a Claude peer left an unread nobody could clear (four inboxes on the
maintainer's machine were orphaned that way). Pre-acking means the unread never
exists, so the objection cannot recur.
"""

from __future__ import annotations

import subprocess

import pytest

from agent_bus import store
from agent_bus.commands import messages
from agent_bus.protocol import AgentTarget


@pytest.fixture
def bus(tmp_path):
    return str(tmp_path / "bus")


@pytest.fixture
def holder():
    p = subprocess.Popen(["sleep", "30"])
    yield p
    p.kill()
    p.wait()


class _Delivered:
    """A transport that succeeds. Success is "did not raise" -- there is no
    boolean above the adapter boundary, which is why the hook sits where it
    does in commands.messages.send."""

    KIND = "claude"

    @staticmethod
    def send(entry, text, summary="", from_name=None, home=None):
        return {"transport": "stub", "to": entry.get("name")}


class _Refused:
    KIND = "claude"

    @staticmethod
    def send(entry, text, summary="", from_name=None, home=None):
        raise ValueError("claude peer refused the message")


def _claude_peer(bus, holder, name="a-claude"):
    return store.register(name, "claude", pid=holder.pid, home=bus)


def test_a_delivered_message_is_filed_already_read(bus, holder, monkeypatch):
    monkeypatch.setattr("agent_bus.adapters.transport.for_kind", lambda k: _Delivered)
    entry = _claude_peer(bus, holder)

    messages.send(to=entry.name, text="hello", from_name="s", home=bus)

    filed = store.get_inbox(AgentTarget(entry.name), home=bus)
    assert len(filed) == 1, "a delivered message must still leave a durable copy"
    assert filed[0]["read"] is True, (
        "the peer never polls this inbox -- an unread here can never be cleared"
    )
    assert store.get_inbox(AgentTarget(entry.name), unread_only=True, home=bus) == []


def test_a_refused_message_leaves_no_copy(bus, holder, monkeypatch):
    """No phantom mail. If the transport raised, nothing was delivered, and a
    record would claim otherwise."""
    monkeypatch.setattr("agent_bus.adapters.transport.for_kind", lambda k: _Refused)
    entry = _claude_peer(bus, holder)

    with pytest.raises(ValueError):
        messages.send(to=entry.name, text="hello", from_name="s", home=bus)

    assert store.get_inbox(AgentTarget(entry.name), home=bus) == []


def test_a_mailbox_failure_never_fails_a_delivered_send(bus, holder, monkeypatch):
    """The swallow is deliberate. The message HAS been delivered; raising here
    would report a failure that did not happen, which is the expensive direction
    to be wrong in."""
    monkeypatch.setattr("agent_bus.adapters.transport.for_kind", lambda k: _Delivered)
    entry = _claude_peer(bus, holder)

    def _boom(*a, **kw):
        raise OSError("disk gone")

    monkeypatch.setattr(store, "send_message", _boom)
    result = messages.send(to=entry.name, text="hello", from_name="s", home=bus)
    assert result["to"] == entry.name


def test_a_claude_session_now_has_a_mailbox():
    """The policy reversal itself, at the addressing layer."""
    from agent_bus.adapters import addressing

    assert addressing.has_mailbox({"id": "claude:sid-1", "kind": "claude"}) is True
