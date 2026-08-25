"""The bridge, as a team secretary.

Takes the message, says "got it -- they haven't read it yet", knows who is in
the office, passes replies back. Not an AI secretary: it never reads,
summarises, filters or re-orders anything it carries.

The tests are organised around the two facts a naive "delivered" would conflate:
the hand-off to the bridge is *instant* and really did succeed, and the desktop
peer has *not* read it and will not until a human prods it.
"""

from __future__ import annotations

import subprocess

import pytest

from agent_bus import bridge as bridge_mod
from agent_bus import store
from agent_bus.bridge import bridge, receipt_for


@pytest.fixture
def bus(tmp_path):
    return str(tmp_path / "bus")


@pytest.fixture
def sender():
    p = subprocess.Popen(["sleep", "30"])
    yield p
    if p.poll() is None:
        p.kill()
        p.wait()


class FakeCloud:
    """Records what the secretary sent on, and hands back what it is given."""

    def __init__(self):
        self.pushed: list[dict] = []
        self.replies: list[dict] = []
        self.acked: list[str] = []
        self.rosters: list[list[dict]] = []

    def push(self, provider, message):
        self.pushed.append(message)
        return message["id"]

    def pull(self, provider):
        out, self.replies = self.replies, []
        return out

    def ack(self, provider, ids):
        self.acked.extend(ids)

    def publish_roster(self, provider, agents):
        self.rosters.append(agents)


class Refuses(FakeCloud):
    def push(self, provider, message):
        raise OSError("cloud unreachable")


def _run(cloud, bus, provider="claude", auto_reply=False):
    logged: list[str] = []
    bridge(provider, cloud, home=bus, once=True, log=logged.append, auto_reply=auto_reply)
    return logged


# --------------------------------------------------------------- the receipt

def test_the_receipt_says_both_things():
    """A sender told only "delivered" would reasonably assume it had been read.
    The receipt has to carry the hand-off *and* the fact that it has not been
    read, or it is misleading in the reassuring direction."""
    text = receipt_for("claude")
    assert "Got it" in text
    assert "Not read yet" in text
    assert "Claude Desktop" in text


def test_the_receipt_is_marked_automated_and_short():
    text = receipt_for("chatgpt")
    assert text.startswith("[auto]")
    assert "ChatGPT" in text
    assert len(text.splitlines()) == 1, "an FYI, not a conversation"


def test_the_sender_gets_the_receipt(bus, sender):
    """The secretary replies the way any peer would -- through the router."""
    them = store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    cloud = FakeCloud()
    bridge_mod._register("claude", bus)
    store.send_message(to="desktop-claude", text="review this", from_name=them.name, home=bus)

    _run(cloud, bus, auto_reply=True)

    got = [m["text"] for m in store.get_inbox(them.name, home=bus)]
    assert any(t.startswith("[auto]") and "Not read yet" in t for t in got)


def test_the_receipt_is_off_unless_asked_for(bus, sender):
    """Opt-in. An unprompted message into someone else's context is offered,
    not imposed -- so the default has to be silence, and a default that drifts
    would be invisible without this."""
    them = store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._register("claude", bus)
    store.send_message(to="desktop-claude", text="review this", from_name=them.name, home=bus)

    cloud = FakeCloud()
    # Deliberately NOT via _run: that helper passes auto_reply= explicitly, so
    # it would test the helper's default rather than the code's. This asserts
    # the production default, which is the thing that could silently drift.
    bridge("claude", cloud, home=bus, once=True, log=lambda _: None)

    assert [m["text"] for m in cloud.pushed] == ["review this"], "still forwarded"
    assert store.get_inbox(them.name, home=bus) == [], "but nothing sent back"


def test_a_receipt_that_cannot_be_delivered_does_not_undo_the_forward(bus, sender):
    """Best-effort on purpose. The message has been accepted for forwarding; a
    failed receipt must not report a failure that did not happen."""
    them = store.register("gone-away", "other", pid=sender.pid, home=bus)
    bridge_mod._register("claude", bus)
    store.send_message(to="desktop-claude", text="review this", from_name=them.name, home=bus)
    sender.kill()
    sender.wait()

    cloud = FakeCloud()
    logged = _run(cloud, bus, auto_reply=True)

    assert [m["text"] for m in cloud.pushed] == ["review this"]
    assert any("receipt" in line for line in logged)


def test_the_sender_is_read_from_where_it_is_actually_stored(bus, sender):
    """A stored message keeps its sender as an AgentRef under `from_`, not a
    flat `from_name`. Reading the wrong key fails silently in two directions --
    no receipt is ever sent, and every forwarded message is attributed to
    "unknown" -- so it is worth pinning rather than trusting."""
    store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    entry = bridge_mod._register("claude", bus)
    store.send_message(to="desktop-claude", text="x", from_name="labkit-dev", home=bus)

    msg = store.get_inbox(entry.id, unread_only=True, home=bus)[0]
    assert bridge_mod.sender_name(msg) == "labkit-dev"


def test_a_forwarded_message_carries_the_real_sender(bus, sender):
    store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._register("claude", bus)
    store.send_message(to="desktop-claude", text="x", from_name="labkit-dev", home=bus)

    cloud = FakeCloud()
    _run(cloud, bus)
    assert cloud.pushed[0]["from"] == "labkit-dev", "attributed to the wrong sender"


# --------------------------------------------------------------- forwarding

def test_mail_is_forwarded_and_then_acked(bus, sender):
    entry = bridge_mod._register("claude", bus)
    store.send_message(to="desktop-claude", text="for the desktop", from_name="s", home=bus)

    cloud = FakeCloud()
    _run(cloud, bus)

    assert [m["text"] for m in cloud.pushed] == ["for the desktop"]
    assert store.get_inbox(entry.id, unread_only=True, home=bus) == [], "should be acked"


def test_a_failed_push_leaves_the_message_unread_for_the_next_pass(bus, sender):
    """Push-then-ack. A courier that loses post is worse than one that delivers
    twice -- and the cloud write carries the local id, so the duplicate is
    absorbed there rather than surfacing twice in someone's chat."""
    entry = bridge_mod._register("claude", bus)
    store.send_message(to="desktop-claude", text="must not vanish", from_name="s", home=bus)

    _run(Refuses(), bus)

    still = [m["text"] for m in store.get_inbox(entry.id, unread_only=True, home=bus)]
    assert still == ["must not vanish"]


def test_the_secretary_does_not_read_the_post(bus, sender):
    """Not an AI secretary. What goes out is what came in, byte for byte."""
    bridge_mod._register("claude", bus)
    body = "  weird\n\nspacing  and **markdown** and a URL https://x.test  "
    store.send_message(to="desktop-claude", text=body, from_name="s", home=bus)

    cloud = FakeCloud()
    _run(cloud, bus)

    assert cloud.pushed[0]["text"] == body


# ------------------------------------------------------------------ replies

def test_a_reply_is_delivered_through_the_router(bus, sender):
    """The branch that silently rots if it is wrong.

    A reply addressed to a Claude Code session, written straight to a file
    inbox, would sit unread forever. Through the router it takes that peer's
    real channel. Here the recipient is an ordinary file-bus peer, so the
    observable effect is a message in its inbox -- but it arrived by routing,
    not by a direct store write.
    """
    them = store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._register("claude", bus)

    cloud = FakeCloud()
    cloud.replies = [{"id": "r1", "to": them.name, "text": "reviewed, ship it"}]
    _run(cloud, bus)

    assert [m["text"] for m in store.get_inbox(them.name, home=bus)] == ["reviewed, ship it"]
    assert cloud.acked == ["r1"], "a delivered reply must be acked in the cloud"


def test_a_reply_for_someone_who_has_gone_is_dropped_not_retried(bus, sender):
    """Log and drop. It would expire at TTL anyway, and a stale reply delivered
    late is exactly what the design exists to prevent."""
    store.register("vanished", "other", pid=sender.pid, home=bus)
    bridge_mod._register("claude", bus)
    sender.kill()
    sender.wait()

    cloud = FakeCloud()
    cloud.replies = [{"id": "r1", "to": "vanished", "text": "too late"}]
    logged = _run(cloud, bus)

    assert cloud.acked == ["r1"], "dropping still consumes it"
    assert any("dropped" in line for line in logged)


def test_a_reply_with_no_addressee_is_dropped(bus, sender):
    bridge_mod._register("claude", bus)
    cloud = FakeCloud()
    cloud.replies = [{"id": "r1", "text": "to nobody"}]
    logged = _run(cloud, bus)
    assert cloud.acked == ["r1"]
    assert any("no addressee" in line for line in logged)


# ------------------------------------------------------------------- roster

def test_the_roster_is_published_so_the_desktop_can_check_first(bus, sender):
    """Published, not queried -- nothing can reach into this machine."""
    store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    cloud = FakeCloud()
    _run(cloud, bus)

    names = [a["name"] for a in cloud.rosters[-1]]
    assert "labkit-dev" in names
    assert "desktop-claude" not in names, "the secretary is not one of the agents"


# ---------------------------------------------------------------- identity

def test_a_bridge_registers_as_an_ordinary_desktop_peer(bus):
    entry = bridge_mod._register("claude", bus)
    assert entry.kind == "desktop"
    assert store.find_entry("desktop:claude", home=bus).id == entry.id


def test_an_unknown_provider_is_refused(bus):
    with pytest.raises(ValueError, match="unknown provider"):
        bridge("gemini", FakeCloud(), home=bus, once=True, log=lambda _: None)
