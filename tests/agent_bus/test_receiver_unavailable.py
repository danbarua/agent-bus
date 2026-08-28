"""Sending to a receiver that is not there.

Reading and writing want opposite answers, and conflating them is how this went
wrong. An entry is retained after its process exits so queued mail stays
*readable* -- deleting it took the mailbox with it, and a reply to an agent that
had just exited failed with "no such agent" (test_presence_vs_mailbox.py).

But that retention silently licensed *writes* too. Sending to a dead peer
succeeded: the sender was told it worked, the message was filed into an inbox
nothing would drain, and with a 1h TTL it then expired unread with no error
anywhere. The bus reported a delivery that could not happen.

So the gate is at the router -- the verb the CLI and MCP server call -- and not
in the store, which stays the mechanical writer that retention depends on.
"""

from __future__ import annotations

import subprocess

import pytest

from agent_bus import store
from agent_bus.commands import messages


@pytest.fixture
def bus(tmp_path):
    return str(tmp_path / "bus")


@pytest.fixture
def holder():
    p = subprocess.Popen(["sleep", "30"])
    yield p
    if p.poll() is None:
        p.kill()
        p.wait()


def _dead_peer(bus, holder, kind="other", name="offline-peer", **kw):
    """A dead peer that is still *addressable* -- i.e. retained.

    The mail matters. An entry with an empty inbox is pruned the moment its
    process dies, so it fails earlier with "no such agent" and never reaches the
    gate. Retention is what keeps a dead entry around, and retention is exactly
    what used to let sends pile into it.
    """
    entry = store.register(name, kind, pid=holder.pid, home=bus, **kw)
    store.send_message(to=entry.name, text="queued while alive", from_name="s", home=bus)
    holder.kill()
    holder.wait()
    return entry


# ------------------------------------------------------------------ the gate

def test_sending_to_a_dead_peer_is_refused(bus, holder):
    entry = _dead_peer(bus, holder)
    with pytest.raises(ValueError, match="receiver unavailable"):
        messages.send(to=entry.name, text="hello", from_name="s", home=bus)


def test_the_refusal_says_it_was_not_sent(bus, holder):
    """A refusal that leaves the sender unsure whether it went is worse than
    either outcome."""
    entry = _dead_peer(bus, holder)
    with pytest.raises(ValueError, match="Not sent"):
        messages.send(to=entry.name, text="hello", from_name="s", home=bus)


def test_a_refused_send_writes_nothing(bus, holder):
    """No phantom mail. The refusal must not also file the message -- the inbox
    should still hold only what was queued while the peer was alive."""
    entry = _dead_peer(bus, holder)
    with pytest.raises(ValueError):
        messages.send(to=entry.name, text="hello", from_name="s", home=bus)
    assert [m["text"] for m in store.get_inbox(entry.name, home=bus)] == ["queued while alive"]


def test_a_live_peer_is_unaffected(bus, holder):
    """Verify the guard by watching it *not* fire -- otherwise a gate that
    refused everything would pass every test above."""
    entry = store.register("live-peer", "other", pid=holder.pid, home=bus)
    result = messages.send(to=entry.name, text="hello", from_name="s", home=bus)
    assert result["to"] == entry.name
    assert result["delivery"] == "now"
    assert result["id"], "a sender must be able to reference what it sent (#108)"


def test_a_dead_bridge_refuses(bus, holder):
    """The case that motivated this. A dead bridge must not accept mail for a
    desktop peer it can no longer carry."""
    entry = _dead_peer(bus, holder, kind="desktop", name="desktop-claude",
                       aliases=["desktop:claude"])
    assert entry.kind == "desktop"
    with pytest.raises(ValueError, match="receiver unavailable"):
        messages.send(to="desktop:claude", text="ask the desktop", from_name="s", home=bus)


# -------------------------------------------------------- spaces answer for themselves

def test_a_codex_thread_is_still_addressable_while_nothing_runs():
    """Not an exception carved out for codex -- `thread.is_live` is True, and
    that is deliberate: a thread is addressable *because* nothing is running.
    The gate asks the address space and gets the right answer for free."""
    messages._refuse_if_not_live(
        "codex:thread:abc", {"id": "codex:thread:abc", "kind": "codex", "name": "t"}
    )


def test_the_gate_is_not_vacuous_for_a_process_backed_space(bus, holder):
    """The mirror of the test above: if is_live answered True everywhere, that
    test would pass while meaning nothing."""
    entry = _dead_peer(bus, holder)
    with pytest.raises(ValueError):
        messages._refuse_if_not_live(entry.name, entry)


# ------------------------------------------------------------------- reading

def test_mail_already_queued_stays_readable(bus, holder):
    """Retention is about reading, and this change does not touch it."""
    entry = _dead_peer(bus, holder, name="has-mail")
    assert [m["text"] for m in store.get_inbox(entry.name, home=bus)] == ["queued while alive"]


def test_a_dead_peer_with_no_mail_still_says_no_such_agent(bus, holder):
    """The other half of the story, recorded so the two errors are not mistaken
    for an inconsistency.

    An empty dead entry is pruned, so it never reaches the gate and fails with
    "no such agent" instead. Different words, same honest outcome: the send did
    not happen. The gate exists for the *retained* case, which is the one that
    used to succeed.
    """
    store.register("empty-and-gone", "other", pid=holder.pid, home=bus)
    holder.kill()
    holder.wait()
    with pytest.raises(ValueError, match="no such agent"):
        messages.send(to="empty-and-gone", text="hello", from_name="s", home=bus)


def test_the_store_stays_permissive(bus, holder):
    """Deliberate layering, recorded so it does not look like an oversight.

    store.send_message is the mechanical writer retention depends on, and it is
    also how a delivered copy is filed for a peer that *was* reachable. The
    policy question -- may this be delivered at all -- belongs to the router.
    """
    entry = _dead_peer(bus, holder)
    assert store.send_message(to=entry.name, text="written directly", from_name="s", home=bus)
