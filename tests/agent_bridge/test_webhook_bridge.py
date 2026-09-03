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


def test_an_event_nobody_asked_for_wakes_nobody(bus, peer):
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}:pr.close")

    _run(FakeCloud([merge_event()]), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    assert not any("#181" in (m["text"] or "") for m in inbox), (
        "a merge reached a subscriber who asked for closes")


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
