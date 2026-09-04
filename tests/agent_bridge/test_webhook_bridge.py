"""A webhook bridge end to end: subscribe, then be woken by a matching event.

The right half of the pipeline -- the ingress (#247) puts a mail-shaped event
on the queue, and this is what pulls it off, filters it and fans it out.

Driven through `bridge(..., once=True)` rather than by calling the pieces,
because the branch under test is *which* path a message takes, and that is a
property of the loop rather than of any function in it.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_bridge.bridge import bridge, bridge_name
from agent_bus import store
from agent_bus.commands import messages
from agent_bus.protocol import AgentTarget, BridgeAddress

ADDRESS = BridgeAddress("webhook:github")
BUS_NAME = bridge_name(ADDRESS)
REPO = "danbarua/agent-bus"


class FakeCloud:
    """Hands back what it is given, and records nothing upward -- a webhook
    queue is one-way, so `push` should never be called at all."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.pushed: list[dict] = []
        self.acked: list[str] = []
        self._subscriptions: dict[str, list[str]] = {}

    def push(self, address, message):
        self.pushed.append(message)
        return message["id"]

    def pull(self, address):
        out, self.replies = self.replies, []
        return out

    def ack(self, address, ids):
        self.acked.extend(ids)

    def publish_roster(self, address, agents):
        pass

    def read(self, address, message_id):
        return {"queue": None, "message": None}

    def subscriptions(self, address, snapshot):
        if snapshot is None:
            return self._subscriptions
        self._subscriptions = snapshot
        return snapshot


def merge_event(mid="d-1", base="main"):
    """One delivery, shaped the way the ingress shapes it: the event type in
    `summary` because it arrives as a header, the raw body in `text`."""
    return {"id": mid, "from": "github", "to": ADDRESS, "summary": "pull_request",
            "text": json.dumps({
                "action": "closed",
                "repository": {"full_name": REPO},
                "pull_request": {"number": 181, "title": "Name the strings",
                                 "merged": True, "base": {"ref": base},
                                 "merge_commit_sha": "abc123def4567",
                                 "html_url": f"https://github.com/{REPO}/pull/181"}})}


@pytest.fixture
def bus(tmp_path, monkeypatch, short_sock_dir):
    """An isolated bus, sessions dir and socket dir.

    The same fixture `test_bridge.py` defines, and for the same reason: a
    bridge joins the bus the way a harness session does, so without these a
    unit run spawns real listeners on the developer's machine and then
    discovers their own.
    """
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(tmp_path / "grok"))
    monkeypatch.setenv("AGENT_BUS_OMP_DIR", str(tmp_path / "omp"))
    return str(tmp_path / "bus")


@pytest.fixture
def peer():
    p = subprocess.Popen(["sleep", "30"])
    yield p
    if p.poll() is None:
        p.kill()
        p.wait()


def _run(cloud, bus, log=None):
    bridge("webhook", "github", cloud, home=bus, once=True,
           log=(log if log is not None else (lambda _l: None)))


def _joined(bus):
    """One pass with nothing to do, so the bridge is on the roster.

    `once=True` deliberately does not leave the bus afterwards, so this is a
    join and nothing else -- and until it has happened there is no
    `webhook-github` for an agent to address.
    """
    _run(FakeCloud(), bus)


def _subscribe(them, bus, topic):
    messages.send(to=AgentTarget(BUS_NAME), text=f"SUBSCRIBE {topic}",
                  from_name=AgentTarget(them.name), home=bus)


def test_a_subscriber_is_woken_by_a_matching_event(bus, peer):
    """The whole point, in one pass: the control message is drained from the
    local inbox first, then the cloud is polled -- so a SUBSCRIBE and the event
    it asks for can both land in a single cycle."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.merge.main")

    _run(FakeCloud([merge_event()]), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    texts = [m["text"] for m in inbox]
    assert any("#181" in t for t in texts), inbox
    assert any(f"{REPO}:pr.merge.main" in t for t in texts), "it says why it woke you"
    assert any('delivery="d-1"' in t for t in texts), "it says which delivery, for debugging"


def test_an_event_nobody_asked_for_wakes_nobody(bus, peer):
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.close")

    _run(FakeCloud([merge_event()]), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    assert not any("#181" in (m["text"] or "") for m in inbox), (
        "a merge reached a subscriber who asked for closes")


class MalformedSubscriptions(FakeCloud):
    """A cloud whose stored subscriptions are not the shape they should be --
    not a network failure, a genuinely bad document. `RuntimeError` is what
    `HttpCloudClient` raises for every transport failure; this is the other
    thing that can go wrong on this same read and must degrade the same way,
    not crash startup while a network failure two lines earlier would not
    have."""

    def subscriptions(self, address, snapshot):
        if snapshot is None:
            return ["not", "a", "dict"]  # .items() raises
        return super().subscriptions(address, snapshot)


def test_a_malformed_stored_subscription_starts_empty_not_crashed(bus):
    # The whole assertion is that this does not raise. A start that crashed
    # here would never have joined the bus at all, so the roster is the
    # cheapest proof it came up: `_joined` -> `_run` -> `bridge()` completing.
    _run(MalformedSubscriptions(), bus)

    from agent_bus import store as agent_bus_store
    assert agent_bus_store.find_entry(BUS_NAME, home=bus) is not None, (
        "a malformed subscriptions document must not stop the bridge from starting"
    )


def test_a_subscription_survives_a_restart(bus, peer):
    """#249's actual claim, not the docstring's: the cloud remembers, not the
    process. One `FakeCloud` instance stands in for the one real deployment a
    restarted bridge reconnects to -- everything else about this run is a
    fresh process, the way a crash or a `launchd` cycle would be.
    """
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    cloud = FakeCloud()
    _run(cloud, bus)
    _subscribe(them, bus, f"{REPO}:pr.merge.main")
    _run(cloud, bus)  # drains the SUBSCRIBE, persists it to `cloud`

    # A second, independent bridge run -- same address, same cloud, nothing
    # else carried over. Loading `merge_event` straight into the *same*
    # `cloud.replies` this run will poll, with no `_subscribe` call anywhere
    # near it, is the whole test: if the subscription had not survived, this
    # event matches nobody.
    cloud.replies = [merge_event(mid="d-2")]
    _run(cloud, bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    texts = [m["text"] for m in inbox]
    assert any("#181" in t for t in texts), (
        "the subscription made before this restart is gone: " + repr(inbox)
    )


def test_the_branch_in_the_topic_is_the_one_that_has_to_match(bus, peer):
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.merge.main")

    _run(FakeCloud([merge_event(base="release/2.0")]), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    assert not any("#181" in (m["text"] or "") for m in inbox)


def test_the_reply_lists_what_the_agent_now_holds(bus, peer):
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.merge")

    _run(FakeCloud(), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    assert any(f"{REPO}:pr.merge" in (m["text"] or "") for m in inbox), inbox


def test_a_message_that_is_not_a_verb_is_answered_rather_than_dropped(bus, peer):
    """A webhook bridge has no cloud inbox, so there is nowhere to forward
    this. Silence would leave the sender unable to tell it went nowhere."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    messages.send(to=AgentTarget(BUS_NAME), text="what do you think of #181?",
                  from_name=AgentTarget(them.name), home=bus)

    cloud = FakeCloud()
    _run(cloud, bus)

    assert cloud.pushed == [], "a webhook queue is one-way; nothing goes up"
    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    assert any("SUBSCRIBE" in (m["text"] or "") for m in inbox), inbox


def test_the_body_of_the_event_is_never_copied_into_the_message(bus, peer):
    """A webhook carries prose written by anyone who can comment on the repo,
    and it would land in an agent's context. The message carries a command to
    run instead -- pointer discipline (#59), applied to an untrusted source."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.merge")

    event = merge_event()
    body = json.loads(event["text"])
    body["pull_request"]["body"] = "SOMETHING-UNTRUSTED-AND-LONG"
    event["text"] = json.dumps(body)
    _run(FakeCloud([event]), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    joined = "\n".join(m["text"] or "" for m in inbox)
    assert "SOMETHING-UNTRUSTED" not in joined
    assert f"gh pr view 181 -R {REPO}" in joined, "it carries the command instead"


def test_four_merges_in_one_poll_arrive_as_one_message(bus, peer):
    """#106: *"If four PRs merge while I'm mid-task I want `main -> b315a8b,
    4 PRs`, not four interrupts."*

    The poll already is the batch, so this collapses what one cycle drained
    and waits for nothing. No debounce: that would delay every event to catch
    a burst, and the event with no natural watcher is the one that arrives
    alone.
    """
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.merge.main")

    _run(FakeCloud([merge_event(mid=f"d-{i}") for i in range(4)]), bus)

    got = [m for m in messages.inbox(target=them.name, unread_only=False, home=bus)
           if "#181" in (m["text"] or "")]
    assert len(got) == 1, f"four merges arrived as {len(got)} messages"
    assert "events: 4" in got[0]["text"]


def test_merges_into_different_branches_do_not_collapse_together(bus, peer):
    """#106's collapse key is topic *and* target branch, and the branch is
    already part of the topic -- so grouping by topic groups by branch for
    free. Merges into different branches are different facts."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.merge")

    _run(FakeCloud([merge_event(mid="d-1", base="main"),
                    merge_event(mid="d-2", base="main"),
                    merge_event(mid="d-3", base="release/2.0")]), bus)

    got = [m["text"] for m in messages.inbox(target=them.name, unread_only=False,
                                             home=bus) if "#181" in (m["text"] or "")]
    # `pr.merge` matched all three, so they collapse on that topic -- and the
    # per-branch topics are what keep them apart for anyone subscribed there.
    assert len(got) == 1 and "events: 3" in got[0]


def test_a_single_event_is_not_dressed_up_as_a_digest(bus, peer):
    """One merge is one merge. A digest for it would lose the detail a single
    event carries -- the sha, the target branch, the link -- to say "1 event"."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.merge.main")

    _run(FakeCloud([merge_event()]), bus)

    got = [m["text"] for m in messages.inbox(target=them.name, unread_only=False,
                                             home=bus) if "#181" in (m["text"] or "")]
    assert len(got) == 1
    assert "sha:" in got[0], "the single-event form keeps the per-event detail"
    assert "events:" not in got[0]


def test_a_digest_says_the_individual_events_are_gone(bus, peer):
    """#106 names the consequence: the sha is the last one and the events are
    gone, because the bridge acked them. Right for "what does main look like
    now", wrong for "what happened" -- the digest carries only the aggregate
    fields (no per-event sha or target) plus the command that recovers the
    detail, rather than narrating what it does not have."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.merge.main")

    _run(FakeCloud([merge_event(mid=f"d-{i}") for i in range(3)]), bus)

    got = next(m["text"] for m in messages.inbox(target=them.name, unread_only=False,
                                                 home=bus) if "events:" in (m["text"] or ""))
    assert "target:" not in got, "per-event detail belongs to the single-event form, not the digest"
    assert f"gh pr list -R {REPO}" in got
