"""SUBSCRIBE, UNSUBSCRIBE, SUBSCRIPTIONS -- a peer reading its own mail.

The distinction that keeps the "not an AI secretary" rule intact (#59): these
are addressed *to* the bridge, not carried by it. The rule binds the courier
role, and none of this is couriering.
"""

from __future__ import annotations

import pytest

from agent_bridge.control import handle
from agent_bridge.subscriptions import Subscriptions
from agent_bridge.topics import Topic

REPO = "danbarua/agent-bus"
OWNER, NAME = REPO.split("/")
TOPIC = Topic(OWNER, NAME, "pulls", subfilter="merged", branch="main")


@pytest.fixture
def subs():
    return Subscriptions()


def test_subscribing_replies_with_everything_held_not_just_the_change(subs):
    """#223 specifies the full list after every change, and that is not
    ceremony: an agent that has been compacted cannot otherwise tell what it
    is holding, so every reply doubles as a status query."""
    handle(f"SUBSCRIBE {TOPIC}", "labkit-dev", subs)
    reply = handle(f"SUBSCRIBE {REPO}/pulls:comment", "labkit-dev", subs)
    assert reply is not None
    assert str(TOPIC) in reply and f"{REPO}/pulls:comment" in reply


def test_subscribing_twice_is_one_subscription(subs):
    """#67 asked: one subscription or two deliveries? Two wake-ups for one
    event is the answer nobody wants."""
    handle(f"SUBSCRIBE {TOPIC}", "labkit-dev", subs)
    handle(f"SUBSCRIBE {TOPIC}", "labkit-dev", subs)
    assert subs.of("labkit-dev") == [TOPIC]
    assert subs.subscribers_for({TOPIC}) == {"labkit-dev"}


def test_unsubscribing_takes_the_same_literal(subs):
    """Which is why a topic is an exact string rather than a pattern that
    might normalise -- the key has to hit."""
    handle(f"SUBSCRIBE {TOPIC}", "labkit-dev", subs)
    reply = handle(f"UNSUBSCRIBE {TOPIC}", "labkit-dev", subs)
    assert reply == "No active subscriptions."
    assert subs.subscribers_for({TOPIC}) == set()


def test_an_agent_can_ask_what_it_holds(subs):
    """Without this a compacted agent has two bad options: re-subscribe
    defensively and double-deliver, or assume and be silently deaf (#67)."""
    handle(f"SUBSCRIBE {TOPIC}", "labkit-dev", subs)
    assert str(TOPIC) in (handle("SUBSCRIPTIONS", "labkit-dev", subs) or "")
    assert handle("SUBSCRIPTIONS", "someone-else", subs) == "No active subscriptions."


@pytest.mark.parametrize("verb", ["SUBSCRIBE", "Subscribe", "subscribe"])
def test_the_verb_is_case_insensitive(verb, subs):
    """#59 wrote `Subscribe` and #223 wrote `SUBSCRIBE`. An agent composing one
    from a skill file writes whichever it saw, and being deaf to the other
    spelling is a silent failure."""
    assert handle(f"{verb} {TOPIC}", "labkit-dev", subs) is not None
    assert subs.of("labkit-dev") == [TOPIC]


def test_a_topic_that_cannot_match_is_refused_rather_than_stored(subs):
    """An agent holding a subscription that can never fire is silently deaf,
    which is the failure this whole surface exists to remove."""
    reply = handle("SUBSCRIBE not-a-topic", "labkit-dev", subs)
    assert reply is not None and "not a topic" in reply
    assert subs.of("labkit-dev") == []


def test_a_verb_with_no_topic_says_what_the_form_is(subs):
    reply = handle("SUBSCRIBE", "labkit-dev", subs)
    assert reply is not None and "needs a topic" in reply


@pytest.mark.parametrize("text", ["", "   ", "hello there", "PR #181 merged"])
def test_ordinary_mail_is_not_a_control_message(text, subs):
    """None, not an error. A webhook bridge has no cloud inbox, so the caller
    has to decide what to say about a message with nowhere to go -- and this
    function's only job is knowing a verb when it sees one."""
    assert handle(text, "labkit-dev", subs) is None


def test_one_topic_can_wake_several_subscribers(subs):
    """Fan-out is one addressed copy per subscriber, never a broadcast (#59):
    delete-on-consume plus broadcast means exactly one random session sees a
    notification and then destroys it."""
    for who in ("labkit-dev", "exo-ledger", "claude-bus-dev"):
        handle(f"SUBSCRIBE {TOPIC}", who, subs)
    assert subs.subscribers_for({TOPIC}) == {"labkit-dev", "exo-ledger", "claude-bus-dev"}
