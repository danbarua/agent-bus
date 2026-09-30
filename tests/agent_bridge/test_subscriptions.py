"""`Subscriptions`' own persistence seam: `snapshot()` out, `load()` back in.

The exact boundary #249's persistence work is built around -- `snapshot()`
converts `Topic -> str`, `load()` converts `str -> Topic` on the way back,
and the wire format in between is JSON-safe strings regardless of what
`Subscriptions` stores internally.
"""

from __future__ import annotations

from agent_bridge.subscriptions import Subscriptions
from agent_bridge.topics import Topic

OWNER, NAME = "danbarua", "agent-bus"
TOPIC = Topic(OWNER, NAME, "labels", label="area:web")


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


def test_load_drops_a_topic_that_no_longer_parses_and_keeps_the_rest():
    """A topic stored under an older grammar costs only itself: it is left
    out and handed back with its subscribers, for the caller to log and to
    write the cleaned map back."""
    subs = Subscriptions()
    dropped = subs.load({f"{OWNER}/{NAME}/pulls:merged": ["labkit-dev"],
                         str(TOPIC): ["labkit-web"]})

    assert dropped == [(f"{OWNER}/{NAME}/pulls:merged", ["labkit-dev"])]
    assert subs.snapshot() == {str(TOPIC): ["labkit-web"]}


def test_labels_in_names_every_label_subscribed_on_that_repo_only():
    subs = Subscriptions()
    subs.add("a", TOPIC)
    subs.add("b", Topic(OWNER, NAME, "labels", label="area:agent"))
    subs.add("c", Topic(OWNER, NAME, "pulls"))
    subs.add("d", Topic(OWNER, "other", "labels", label="area:record"))

    assert subs.labels_in(f"{OWNER}/{NAME}") == {"area:web", "area:agent"}
