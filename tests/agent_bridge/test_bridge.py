"""The bridge, as a team secretary.

Takes the message, says "got it -- they haven't read it yet", knows who is in
the office, passes replies back. Not an AI secretary: it never reads,
summarises, filters or re-orders anything it carries.

The tests are organised around the two facts a naive "delivered" would conflate:
the hand-off to the bridge is *instant* and really did succeed, and the desktop
peer has *not* read it and will not until a human prods it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

import pytest
from roster import found

from agent_bridge import bridge as bridge_mod
from agent_bridge.bridge import bridge, bridge_name, receipt_for
from agent_bus import log as bus_log
from agent_bus import store
from agent_bus.protocol import AgentTarget, BridgeAddress


@pytest.fixture
def bus(tmp_path, monkeypatch, short_sock_dir):
    """An isolated bus, sessions dir and socket dir.

    The last two are not optional here. A bridge joins the bus the way a harness
    session does -- register, then publish a listener -- and a published
    listener writes into ~/.claude/sessions and binds under /tmp/cc-socks. Left
    unset, a unit run would spawn real listeners on the developer's machine and
    then discover their own, which is exactly how this fixture was found: the
    roster assertion came back holding live agents from other projects.
    """
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    # Grok's and omp's registries too, or `list_agents` unions in whatever is
    # live on the developer's machine: a roster assertion here was reading
    # `exo-grok` and a real omp session out of ~/. Every registry, not just the
    # two this fixture started with.
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(tmp_path / "grok"))
    monkeypatch.setenv("AGENT_BUS_OMP_DIR", str(tmp_path / "omp"))
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
        self.ack_calls: list[list[str]] = []
        self.rosters: list[list[dict]] = []

    def push(self, address, message):
        self.pushed.append(message)
        return message["id"]

    def pull(self, address):
        out, self.replies = self.replies, []
        return out

    def ack(self, address, ids):
        self.ack_calls.append(list(ids))
        self.acked.extend(ids)

    def publish_roster(self, address, agents):
        self.rosters.append(agents)

    def read(self, address, message_id):
        """Part of the CloudClient contract since #225, and this double never
        grew it -- nothing in these tests calls `read`, so nothing noticed.

        That is #190's failure exactly: a Protocol is structural, nobody
        `isinstance`s this, and the gap was invisible until `src/` and the
        suite were checked against each other."""
        for m in [*self.pushed, *self.replies]:
            if m.get("id") == message_id:
                return {"queue": "outbox", "message": m}
        return {"queue": None, "message": None}


class Refuses(FakeCloud):
    def push(self, address, message):
        raise OSError("cloud unreachable")


# The address under test. A bridge is `<kind>:<name>` and nothing else -- there
# is no enum of permitted addresses to pick from any more.
ADDRESS = BridgeAddress("desktop:claude")
# The other half of the pair that used to be indistinguishable: ADDRESS is what
# the bridge *holds*, BUS_NAME is what it registers as, and `bridge_name` is the
# one-way transform between them. Both were the bare strings "desktop:claude"
# and "desktop-claude", and nothing at a call site said which was which.
BUS_NAME = bridge_name(ADDRESS)


def _run(cloud, bus, kind="desktop", name="claude", auto_reply=False):
    logged: list[str] = []
    bridge(kind, name, cloud, home=bus, once=True, log=logged.append,
           auto_reply=auto_reply)
    return logged


# --------------------------------------------------------------- the receipt

def test_the_receipt_says_both_things():
    """A sender told only "delivered" would reasonably assume it had been read.
    The receipt has to carry the hand-off *and* the fact that it has not been
    read, or it is misleading in the reassuring direction."""
    text = receipt_for(ADDRESS)
    assert "Got it" in text
    assert "Not read yet" in text
    assert ADDRESS in text


def test_the_receipt_is_marked_automated_and_short():
    text = receipt_for(BridgeAddress("desktop:chatgpt"))
    assert text.startswith("[auto]")
    assert "desktop:chatgpt" in text
    assert len(text.splitlines()) == 1, "an FYI, not a conversation"


def test_the_sender_gets_the_receipt(bus, sender):
    """The secretary replies the way any peer would -- through the router."""
    them = store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    cloud = FakeCloud()
    bridge_mod._join(ADDRESS, bus)
    store.send_message(to=BUS_NAME, text="review this", from_name=AgentTarget(them.name), home=bus)

    _run(cloud, bus, auto_reply=True)

    got = [m["text"] for m in store.get_inbox(AgentTarget(them.name), home=bus)]
    assert any(t.startswith("[auto]") and "Not read yet" in t for t in got)


def test_the_receipt_is_off_unless_asked_for(bus, sender):
    """Opt-in. An unprompted message into someone else's context is offered,
    not imposed -- so the default has to be silence, and a default that drifts
    would be invisible without this."""
    them = store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._join(ADDRESS, bus)
    store.send_message(to=BUS_NAME, text="review this", from_name=AgentTarget(them.name), home=bus)

    cloud = FakeCloud()
    # Deliberately NOT via _run: that helper passes auto_reply= explicitly, so
    # it would test the helper's default rather than the code's. This asserts
    # the production default, which is the thing that could silently drift.
    bridge("desktop", "claude", cloud, home=bus, once=True, log=lambda _: None)

    assert [m["text"] for m in cloud.pushed] == ["review this"], "still forwarded"
    assert store.get_inbox(AgentTarget(them.name), home=bus) == [], "but nothing sent back"


def test_a_receipt_that_cannot_be_delivered_does_not_undo_the_forward(bus, sender):
    """Best-effort on purpose. The message has been accepted for forwarding; a
    failed receipt must not report a failure that did not happen."""
    them = store.register("gone-away", "other", pid=sender.pid, home=bus)
    bridge_mod._join(ADDRESS, bus)
    store.send_message(to=BUS_NAME, text="review this", from_name=AgentTarget(them.name), home=bus)
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
    entry = bridge_mod._join(ADDRESS, bus)
    store.send_message(to=BUS_NAME, text="x", from_name=AgentTarget("labkit-dev"), home=bus)

    from agent_bus.commands import messages as m

    msg = m.inbox(target=entry["name"], unread_only=True, home=bus)[0]
    assert bridge_mod.sender_name(msg) == "labkit-dev"


def test_a_forwarded_message_carries_the_real_sender(bus, sender):
    store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._join(ADDRESS, bus)
    store.send_message(to=BUS_NAME, text="x", from_name=AgentTarget("labkit-dev"), home=bus)

    cloud = FakeCloud()
    _run(cloud, bus)
    assert cloud.pushed[0]["from"] == "labkit-dev", "attributed to the wrong sender"


# --------------------------------------------------------------- forwarding

def test_mail_is_forwarded_and_then_acked(bus, sender):
    entry = bridge_mod._join(ADDRESS, bus)
    store.send_message(to=BUS_NAME, text="for the desktop", from_name=AgentTarget("s"), home=bus)

    cloud = FakeCloud()
    _run(cloud, bus)

    assert [m["text"] for m in cloud.pushed] == ["for the desktop"]
    from agent_bus.commands import messages as m

    assert m.inbox(target=entry["name"], unread_only=True, home=bus) == [], "should be acked"


def test_a_failed_push_leaves_the_message_unread_for_the_next_pass(bus, sender):
    """Push-then-ack. A courier that loses post is worse than one that delivers
    twice -- and the cloud write carries the local id, so the duplicate is
    absorbed there rather than surfacing twice in someone's chat."""
    entry = bridge_mod._join(ADDRESS, bus)
    store.send_message(to=BUS_NAME, text="must not vanish", from_name=AgentTarget("s"), home=bus)

    _run(Refuses(), bus)

    from agent_bus.commands import messages as m

    still = [x["text"] for x in m.inbox(target=entry["name"], unread_only=True, home=bus)]
    assert still == ["must not vanish"]


def test_the_secretary_does_not_read_the_post(bus, sender):
    """Not an AI secretary. What goes out is what came in, byte for byte."""
    bridge_mod._join(ADDRESS, bus)
    body = "  weird\n\nspacing  and **markdown** and a URL https://x.test  "
    store.send_message(to=BUS_NAME, text=body, from_name=AgentTarget("s"), home=bus)

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
    bridge_mod._join(ADDRESS, bus)

    cloud = FakeCloud()
    cloud.replies = [{"id": "r1", "to": them.name, "text": "reviewed, ship it"}]
    _run(cloud, bus)

    assert [m["text"] for m in store.get_inbox(AgentTarget(
        them.name,
    ), home=bus)] == ["reviewed, ship it"]
    assert cloud.acked == ["r1"], "a delivered reply must be acked in the cloud"


def test_the_cloud_id_is_the_id_the_recipient_sees(bus, sender):
    """One identifier for the whole journey, not one per hop.

    A message crossing inbound used to change identity at the bridge: the cloud
    minted an id, the bridge dropped it, and the local bus minted a second with
    nothing linking the two. "Where did that message get to" then had no answer
    that spanned both halves -- which is the entire reason two logs can be as
    good as one view (#93, #94) or can be two useless logs.

    Outbound has always done this, as the dedupe key. This is inbound catching
    up.
    """
    them = store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._join(ADDRESS, bus)

    cloud = FakeCloud()
    cloud.replies = [{"id": "cloud-abc123", "to": them.name, "text": "one id, please"}]
    _run(cloud, bus)

    delivered = store.get_inbox(AgentTarget(them.name), home=bus)
    assert [m["id"] for m in delivered] == ["cloud-abc123"], (
        "the recipient sees a different id than the cloud does, so nothing "
        "joins the two halves of the journey"
    )


def test_a_redelivered_reply_does_not_become_two_local_messages(bus, sender):
    """The dedupe bug outbound already solved, appearing inbound.

    `_deliver_reply` returns False on a transport failure and the cloud copy
    stays unacked, so the next poll retries it. If each attempt minted a fresh
    local id, one cloud message would land twice in someone's inbox -- and the
    second copy would look like a genuine second message rather than a retry.
    """
    them = store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._join(ADDRESS, bus)

    reply = {"id": "cloud-retry", "to": them.name, "text": "sent twice"}
    for _ in range(2):
        cloud = FakeCloud()
        cloud.replies = [dict(reply)]
        _run(cloud, bus)

    ids = [m["id"] for m in store.get_inbox(AgentTarget(them.name), home=bus)]
    assert ids.count("cloud-retry") == 2, (
        "expected the same id twice -- if these differ, a retry is "
        "indistinguishable from a new message"
    )
    assert len(set(ids)) == 1


def test_a_reply_for_someone_who_has_gone_is_held_not_dropped(bus, sender):
    """Reversed deliberately; this test used to assert the opposite.

    Dropping was justified as "it would expire at TTL anyway, and a stale reply
    delivered late is what the design exists to prevent". But that is a second
    staleness policy layered on the one the design already has -- messages
    expire uniformly and briefly, and `expireAt` is what ends a delivery nobody
    can take.

    It was also wrong about the common case. A receiver is routinely gone for
    seconds: a user stops a Claude session to rename its worktree and starts it
    again. Refusing once and discarding turns a self-healing absence into lost
    mail, which is the failure direction this design rejects everywhere else.

    Unacked means the cloud hands it back next poll. That is the whole retry
    mechanism, and it is why no dead-letter queue is needed.
    """
    store.register("vanished", "other", pid=sender.pid, home=bus)
    bridge_mod._join(ADDRESS, bus)
    sender.kill()
    sender.wait()

    cloud = FakeCloud()
    cloud.replies = [{"id": "r1", "to": "vanished", "text": "too late"}]
    logged = _run(cloud, bus)

    assert cloud.acked == [], "an undelivered reply must stay in the cloud queue"
    assert any("will retry" in line for line in logged), logged


def test_a_delivered_reply_is_acked(bus, sender):
    """The other half: acking only what failed would be just as wrong."""
    store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._join(ADDRESS, bus)

    cloud = FakeCloud()
    cloud.replies = [{"id": "r1", "to": "labkit-dev", "text": "reviewed"}]
    _run(cloud, bus)

    assert cloud.acked == ["r1"]
    got = store.get_inbox(AgentTarget("labkit-dev"), home=bus)
    assert [m["text"] for m in got] == ["reviewed"]


def test_each_reply_is_acked_as_it_lands(bus, sender):
    """One ack per message, not one per batch.

    Acking at the end of a pass means a crash part-way through redelivers
    everything already delivered in it. Acking as we go bounds that at the
    single message in flight. Asserted on the *number of calls*, because the
    ids alone look identical either way.
    """
    store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._join(ADDRESS, bus)

    cloud = FakeCloud()
    cloud.replies = [
        {"id": "r1", "to": "labkit-dev", "text": "first"},
        {"id": "r2", "to": "labkit-dev", "text": "second"},
    ]
    _run(cloud, bus)

    assert cloud.acked == ["r1", "r2"]
    assert cloud.ack_calls == [["r1"], ["r2"]], cloud.ack_calls


def test_a_reply_with_no_addressee_is_dropped(bus, sender):
    bridge_mod._join(ADDRESS, bus)
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

def _crashed_holding(bus, *texts):
    """A previous incarnation that took mail and died before forwarding it.

    Registered **alive**, given the mail, and killed afterwards -- which is the
    only way this state occurs. Retention keeps a dead entry that has a mailbox;
    a row registered against an already-dead pid is pruned by the next
    `find_entry` before any mail can land, so building the fixture the other way
    round tests a state the bus cannot be in.
    """
    proc = subprocess.Popen(["sleep", "60"])
    entry = store.register("desktop-claude", "desktop", pid=proc.pid, home=bus,
                           aliases=["desktop:claude"])
    ids = [store.send_message(AgentTarget("desktop:claude"), t, from_name=AgentTarget("labkit-dev"),
                              home=bus) for t in texts]
    proc.kill()
    proc.wait()
    return entry.id, ids


def test_mail_a_crashed_bridge_never_forwarded_is_recovered(bus):
    """The push-then-ack promise, made true.

    A crash between push and ack is meant to retry. It did not: a restart
    reclaims the name and not the inbox -- `register` mints a new id when the
    old row is dead, and the mailbox is keyed by id -- so the message sat in an
    inbox nobody read and expired. Losing mail silently is the failure direction
    the design rejected, and the fix is ordering: read the role before
    re-registering hides it.
    """
    _crashed_holding(bus, "review this branch")

    cloud = FakeCloud()
    _run(cloud, bus)

    assert [m["text"] for m in cloud.pushed] == ["review this branch"]


def test_recovered_mail_is_acked_where_it_lay(bus):
    """Recovery must not become replay.

    Asserted on the *old* mailbox rather than on a second run, because a second
    run cannot reach it: once this process has joined, the live-holder guard
    skips the drain, so a missing ack would hide until the next real crash and
    then replay the whole mailbox into someone's chat.

    Read by id: a mailbox outlives the entry that owned it, and acking the last
    unread prunes the row.
    """
    old_id, _ = _crashed_holding(bus, "review this branch")

    _run(FakeCloud(), bus)

    left = store.get_inbox(AgentTarget(old_id), unread_only=True, home=bus)
    assert left == [], [m["text"] for m in left]


def test_only_what_the_previous_bridge_had_not_forwarded_is_recovered(bus):
    """Acked means forwarded, so recovery reads unread only.

    The partially-drained mailbox is the case that matters and the only one
    reachable: a crash after forwarding some of it. Acking *everything* prunes
    the row outright, so a test built that way asserts nothing about the filter
    -- which is what the first version of this test did.
    """
    _old_id, (sent, _) = _crashed_holding(
        bus, "already forwarded", "never forwarded")
    store.ack_message(sent, target=AgentTarget("desktop:claude"), home=bus)

    cloud = FakeCloud()
    _run(cloud, bus)

    assert [m["text"] for m in cloud.pushed] == ["never forwarded"]


def test_a_crashed_bridge_can_come_back(bus):
    """A restart is still us, and must not be refused.

    The user asked for a bridge at this address; which process is serving it
    is our business, not theirs. A crash leaves the old row behind, and the
    refusal in _join must key on a *live* holder -- otherwise the one thing a
    crashed bridge cannot do is the thing it must do, and the fix for a crash
    becomes hand-editing the roster.
    """
    dead = subprocess.Popen(["sleep", "30"])
    dead.kill()
    dead.wait()
    store.register("desktop-claude", "desktop", pid=dead.pid, home=bus,
                   aliases=["desktop:claude"])

    entry = bridge_mod._join(ADDRESS, bus)

    assert entry["name"] == "desktop-claude", (
        "a restart must reclaim the name, not be de-collided into "
        f"desktop-claude-2: got {entry['name']}"
    )
    assert entry["pid"] == os.getpid()
    me = bridge_mod._me(ADDRESS, bus, entry)
    assert me["pid"] == os.getpid(), "the restarted process is who we are now"


def test_the_bridge_is_excluded_after_it_re_registers(bus):
    """The CI failure, in one test: `desktop-claude` in its own broadcast.

    The snapshot used to exclude the bridge by comparing each row's id against
    the id `join` returned. An id read once is a guess by the second pass -- a
    row that is re-created gets a new one -- and then the secretary appears in
    the list of people it is describing, inviting the desktop to write to it.
    Excluding by pid cannot drift: the process is the bridge.
    """
    entry = bridge_mod._join(ADDRESS, bus)
    store.unregister("desktop-claude", home=bus)
    store.register("desktop-claude", "desktop", pid=os.getpid(), home=bus,
                   aliases=["desktop:claude"])
    fresh = store.find_entry(AgentTarget("desktop:claude"), home=bus)
    assert fresh is not None, "the peer never re-registered"
    assert fresh.id != entry["id"], "the row must be re-created for this to bite"

    me = bridge_mod._me(ADDRESS, bus, entry)
    names = [a["name"] for a in bridge_mod._roster_snapshot(ADDRESS, me, bus)]
    assert "desktop-claude" not in names, names


def test_the_bridge_finds_itself_after_a_rename(bus):
    """The other CI failure: `no such agent: desktop-claude`.

    register() de-collides names, so the name a bridge was given at join is not
    a name it owns forever. Holding that string and asking for its inbox by it
    is how the bus came to answer that it had never heard of us. `_me` resolves
    through the pid and the role alias, neither of which can be renamed.
    """
    entry = bridge_mod._join(ADDRESS, bus)
    store.register("desktop-claude-elsewhere", "desktop", pid=os.getpid(),
                   home=bus)

    me = bridge_mod._me(ADDRESS, bus, entry)
    assert me["name"] == "desktop-claude-elsewhere"
    assert me["name"] != entry["name"], "the rename must land for this to bite"


def test_a_second_bridge_for_one_address_is_refused(bus, sender):
    """`desktop:claude` is the whole address of a desktop peer -- there is no
    conversation dimension and there will not be one, so two holders is not an
    ambiguity to resolve at delivery, it is a thing that must not exist.

    The bus will not stop it: register() de-collides names and not aliases, so
    a second bridge registers cleanly as `desktop-claude-2`, shows up in `list`,
    and competes for the address. Keeping the refusal here leaves the bus dumb.
    """
    store.register("desktop-claude", "desktop", pid=sender.pid, home=bus,
                   aliases=["desktop:claude"])
    with pytest.raises(RuntimeError, match="already held"):
        bridge_mod._join(ADDRESS, bus)


def test_the_bridge_registers_under_the_kind_it_was_given(bus):
    """`--kind` has to change the registration, not just the address.

    It was hardcoded to "desktop", so a webhook bridge registered as a desktop
    peer -- and `delivery_expectation` keys on kind, so the bus would have told
    senders a human has to prod a GitHub webhook before it reads anything.

    `webhook` is deliberately **not** added to `KNOWN_KINDS`. Kinds are open
    strings, and what belongs in the hint list is a product decision rather than
    something a bridge grants itself by starting.
    """
    entry = bridge_mod._join(BridgeAddress("webhook:github"), bus)

    assert entry["kind"] == "webhook"
    assert entry["name"] == "webhook-github", "the name is derived, not looked up"
    assert found(AgentTarget("webhook:github"), home=bus).id == entry["id"]


def test_a_bridge_registers_as_an_ordinary_desktop_peer(bus):
    entry = bridge_mod._join(ADDRESS, bus)
    assert entry["kind"] == "desktop"
    assert found(AgentTarget("desktop:claude"), home=bus).id == entry["id"]


def test_a_bridge_publishes_a_listener_so_claude_can_message_it(bus, tmp_path):
    """The design point, and the thing store.register alone silently omits.

    The bridge acts as a peer, therefore Claude can message it. pi proves the
    shape: no MCP server at all, and it still reaches a Claude session, because
    `listen` publishes a Claude-shaped session file and socket. Claiming a name
    without one leaves a peer that is on the bus and invisible to Claude's
    native ListAgents -- reachable only by an agent that has been *told* to use
    a CLI, which is the opposite of the point.

    This assertion was vacuous until the socket dir got short enough to bind;
    worth knowing that a green run proved nothing here for a while.
    """
    import time

    bridge_mod._join(ADDRESS, bus)
    sessions = tmp_path / "sessions"
    deadline = time.time() + 10
    published: list = []
    while time.time() < deadline:
        published = sorted(sessions.glob("*.json")) if sessions.exists() else []
        if published:
            break
        time.sleep(0.2)

    assert published, (
        "the bridge published no Claude-shaped session file, so Claude cannot "
        "see it in ListAgents and cannot SendMessage it"
    )
    doc = json.loads(published[0].read_text())
    assert doc.get("messagingSocketPath"), "published a session with no socket to reach"
    assert doc.get("agentBus"), "must be marked ours, or it looks like a real Claude session"


def test_an_address_that_would_not_parse_is_refused(bus):
    """Shape, not membership.

    There is no list of permitted kinds or names, deliberately -- a third job
    should not need an enum edited before it can start. But `:` separates the
    two halves, so a value carrying one would silently produce a different
    address than the caller asked for, and that is worth refusing.
    """
    for kind, name in (("", "claude"), ("desktop", ""),
                       ("desk:top", "claude"), ("desktop", "cla:ude")):
        with pytest.raises(ValueError, match="contain no"):
            bridge(kind, name, FakeCloud(), home=bus, once=True, log=lambda _: None)


# --------------------------------------------- structured logging (#197)


@pytest.fixture
def bridge_log(tmp_path, monkeypatch):
    """Configure agent_bus.log to a file this test controls, independent of
    the injected `log` callable the bridge already takes.

    #197 is precisely the claim that both now happen from the same call
    sites -- the human line the injected callable prints, and a structured
    record beside it -- so a test that only reads `logged` (as every other
    test in this file does) cannot see whether the second half exists.
    """
    dest = tmp_path / "agent-bridge.jsonl"
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", str(dest))
    monkeypatch.setenv("AGENT_BUS_LOG_LEVEL", "info")
    bus_log.configure(force=True, service="agent-bridge")
    yield dest
    for h in list(logging.getLogger(bus_log.LOGGER_NAME).handlers):
        h.close()
        logging.getLogger(bus_log.LOGGER_NAME).removeHandler(h)


def _bridge_records(dest):
    if not dest.exists():
        return []
    return [json.loads(line) for line in dest.read_text().splitlines() if line.strip()]


def test_a_push_failure_is_logged_structured_as_well_as_printed(bus, sender, bridge_log):
    """The failure path from the original report -- a push the cloud
    refuses -- now reaches `agent_bus.log` too, with the exception as a
    field rather than folded into the human sentence. The existing
    `logged.append` line (the injected callable) is untouched by this."""
    bridge_mod._join(ADDRESS, bus)
    store.send_message(to=BUS_NAME, text="must not vanish", from_name=AgentTarget("s"), home=bus)

    logged = _run(Refuses(), bus)
    assert any("could not forward" in x for x in logged), (
        f"the injected callable's human line should be untouched: {logged}"
    )

    records = _bridge_records(bridge_log)
    forwarding_failures = [r for r in records if r.get("message") == "could not forward"]
    assert forwarding_failures, f"no structured record for the push failure: {records}"
    assert "cloud unreachable" in forwarding_failures[0]["error"]
    # `trace_id`, not `message_id`. Every record that concerns a message names
    # it under the one field the cloud also uses, so a failure joins to the
    # rest of that message's journey instead of being the one hop a trace
    # cannot find.
    assert forwarding_failures[0]["trace_id"]


def test_records_carry_the_address_that_produced_them(bus, sender, bridge_log):
    """#197's second decision: one `agent-bridge.jsonl` shared by every
    bridge process, `address` is the field that tells `desktop:claude` and
    `desktop:chatgpt` apart in it. Checked directly rather than assumed."""
    _run(FakeCloud(), bus)

    records = _bridge_records(bridge_log)
    assert records, "the bridge run produced no structured records at all"
    assert all(r.get("address") == ADDRESS for r in records), records
    assert all(r.get("service") == "agent-bridge" for r in records), records


def test_two_addresses_share_one_file_without_mixing_up_their_records(bus, bridge_log):
    """The actual claim behind #197's second decision, not just one address
    in isolation: two different bridge processes (`desktop:claude`,
    `desktop:chatgpt`), one after another against the one `agent-bridge.jsonl`
    `bridge_log` already points both at. Neither run's records may end up
    unlabelled or attributed to the other -- `address` has to be enough to
    split the file back into two, the way `jq 'select(.address==...)'` in
    docs/running-the-bridge.md assumes it can."""
    _run(FakeCloud(), bus, kind="desktop", name="claude")
    _run(FakeCloud(), bus, kind="desktop", name="chatgpt")

    records = _bridge_records(bridge_log)
    claude_records = [r for r in records if r.get("address") == "desktop:claude"]
    chatgpt_records = [r for r in records if r.get("address") == "desktop:chatgpt"]

    assert claude_records, f"no records at all for desktop:claude: {records}"
    assert chatgpt_records, f"no records at all for desktop:chatgpt: {records}"
    assert not any(r.get("address") is None for r in records), (
        f"a record with no address at all -- unattributable to either run: {records}"
    )
    standing_in = {
        r["address"]: r["name"] for r in records if r.get("message") == "standing in"
    }
    assert standing_in == {
        "desktop:claude": "desktop-claude", "desktop:chatgpt": "desktop-chatgpt",
    }, "each run's own startup record should name its own entry, not the other's"


def test_a_successful_forward_is_a_chain_of_records_carrying_the_id(bus, sender, bridge_log):
    """#217. The bridge logged only when it failed, so every message that went
    wrong was visible and every message that went right was not.

    Louder than a courier would normally log a success, deliberately: what is
    being delivered is a context update to an agent that needs it in time to
    matter, so a delivery that worked has to be visible as a chain rather than
    inferred from the absence of a failure.
    """
    bridge_mod._join(ADDRESS, bus)
    mid = store.send_message(to=BUS_NAME, text="hello", from_name=AgentTarget("s"), home=bus)

    cloud = FakeCloud()
    _run(cloud, bus)
    assert [m["id"] for m in cloud.pushed] == [mid], "the message never reached the cloud"

    records = _bridge_records(bridge_log)
    by_id = [r for r in records if r.get("trace_id") == mid]
    chain = [r["message"] for r in by_id]

    # A subsequence, not an exact list. Everything that touches this message
    # names it now -- including the command layer's own `ack` verb, which
    # gained a trace_id in the same change -- so pinning the exact sequence
    # would fail whenever a hop was correctly added. What matters is that both
    # of these exist, in this order.
    assert "forwarded" in chain and "acked locally" in chain, (
        f"the happy path left no chain for {mid}: "
        f"{[(r.get('message'), r.get('trace_id')) for r in records]}"
    )
    assert chain.index("forwarded") < chain.index("acked locally"), chain
    assert all(r["severity"] == "INFO" for r in by_id), by_id
    assert by_id[chain.index("forwarded")]["to"] == ADDRESS


def test_a_successful_delivery_inbound_is_logged_with_the_id(bus, sender, bridge_log):
    """The other direction. A reply that reaches a local peer leaves a record
    naming the same id the cloud used, so the two halves of one message's
    journey join on one field."""
    them = store.register("labkit-dev", "other", pid=sender.pid, home=bus)
    bridge_mod._join(ADDRESS, bus)
    cloud = FakeCloud()
    cloud.replies = [{"id": "reply-1", "to": them.name, "text": "hi", "summary": ""}]

    _run(cloud, bus)

    records = _bridge_records(bridge_log)
    delivered = [r for r in records
                 if r.get("message") == "delivered" and r.get("trace_id") == "reply-1"]
    assert delivered, (
        "a delivered reply left no record: "
        f"{[(r.get('message'), r.get('trace_id')) for r in records]}"
    )
    assert delivered[0]["severity"] == "INFO"
    assert delivered[0]["to"] == them.name


def test_every_record_that_concerns_a_message_names_it_the_same_way(bus, sender, bridge_log):
    """One field, or a trace joins on nothing. `message_id` and `reply_id` were
    both in use, so a query on the cloud's `trace_id` silently missed exactly
    the failure records anyone would be looking for.

    Both a success and a failure, because that is where the two spellings
    lived: an earlier version of this test drove only `FakeCloud`, saw no
    failure records at all, and passed against a mutant that put `message_id`
    back on `could not forward`.
    """
    bridge_mod._join(ADDRESS, bus)
    store.send_message(to=BUS_NAME, text="x", from_name=AgentTarget("s"), home=bus)
    _run(FakeCloud(), bus)
    store.send_message(to=BUS_NAME, text="y", from_name=AgentTarget("s"), home=bus)
    _run(Refuses(), bus)

    records = _bridge_records(bridge_log)
    assert any(r.get("message") == "could not forward" for r in records), (
        "no failure record was produced, so this test would pass on a mutant"
    )
    stragglers = [r for r in records if "message_id" in r or "reply_id" in r]
    assert not stragglers, (
        f"records naming a message under some other field: {stragglers}"
    )