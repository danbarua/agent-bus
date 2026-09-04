"""`Subscriptions`' own persistence seam: `snapshot()` out, `load()` back in.

The exact boundary #249's persistence work is built around -- `snapshot()`
converts `Topic -> str`, `load()` converts `str -> Topic` on the way back,
and the wire format in between is JSON-safe strings regardless of what
`Subscriptions` stores internally.
"""

from __future__ import annotations

import pytest

from agent_bridge.subscriptions import Subscriptions
from agent_bridge.topics import Topic

OWNER, NAME = "danbarua", "agent-bus"
TOPIC = Topic(OWNER, NAME, "pulls", subfilter="merged", branch="main")


def test_a_snapshot_round_trips_through_topic():
    subs = Subscriptions()
    subs.add("labkit-dev", TOPIC)

    restored = Subscriptions()
    restored.load(subs.snapshot())

    assert restored.of("labkit-dev") == [TOPIC]
    assert restored.subscribers_for({TOPIC}) == {"labkit-dev"}


def test_a_snapshot_key_is_the_topics_own_canonical_string():
    subs = Subscriptions()
    subs.add("labkit-dev", TOPIC)

    assert subs.snapshot() == {str(TOPIC): ["labkit-dev"]}


def test_load_raises_on_a_topic_that_no_longer_parses():
    """The caller (`bridge.py`'s `_restore_subscriptions`) already wraps this
    call to start empty rather than crash on a malformed restore (#249) --
    `load()` itself just has to raise, not swallow, on a bad key."""
    with pytest.raises(ValueError, match="not a topic"):
        Subscriptions().load({"not-a-topic": ["labkit-dev"]})
