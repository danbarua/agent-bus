"""Who wants which topic.

The in-memory half of #249. `snapshot`/`load` are what `bridge.py` persists to
and restores from Firestore -- this class itself still knows nothing about the
cloud, on purpose: it is the same dict either way, and the only thing that
changed is who else gets a copy of it and when.

Fan-out is one addressed copy per subscriber, never a broadcast (#59) -- so
what this returns is a set of names to send to, and the sending is the
caller's.
"""

from __future__ import annotations

from collections import defaultdict

from .topics import Topic


class Subscriptions:
    """Topic -> subscribers, and the inverse for answering an agent."""

    def __init__(self) -> None:
        self._by_topic: dict[Topic, set[str]] = defaultdict(set)

    def add(self, subscriber: str, topic: Topic) -> None:
        """Idempotent. Subscribing twice is one subscription, not two
        deliveries -- #67 asked which, and duplicate wake-ups for one event is
        the answer nobody wants."""
        self._by_topic[topic].add(subscriber)

    def remove(self, subscriber: str, topic: Topic) -> None:
        """Also idempotent, and takes the same value `add` took. That is why
        a topic is an exact value rather than a pattern that might
        normalise (#67)."""
        self._by_topic.get(topic, set()).discard(subscriber)
        if not self._by_topic.get(topic):
            self._by_topic.pop(topic, None)

    def of(self, subscriber: str) -> list[Topic]:
        """What this agent is subscribed to, sorted so a reply reads the same
        way twice. An agent that has been compacted or restarted has to be able
        to *ask* -- without it the choices are re-subscribing defensively and
        double-delivering, or assuming and being silently deaf (#67)."""
        return sorted((t for t, subs in self._by_topic.items() if subscriber in subs), key=str)

    def subscribers_for(self, topics: set[Topic]) -> set[str]:
        """Everyone who asked for any of these. Set membership, so the cost is
        the same for one subscriber or fifty."""
        out: set[str] = set()
        for topic in topics:
            out |= self._by_topic.get(topic, set())
        return out

    def __len__(self) -> int:
        return sum(len(s) for s in self._by_topic.values())

    def snapshot(self) -> dict[str, list[str]]:
        """The whole map, JSON-safe. Sorted for the same reason `of` is --
        two restores from the same state should read the same."""
        return {str(t): sorted(subs) for t, subs in self._by_topic.items() if subs}

    def load(self, snapshot: dict[str, list[str]]) -> None:
        """Replace the whole map from a restored `snapshot`. Called once, right
        after construction, before a bridge starts serving -- this is a
        restore, not a merge, and merging a partial snapshot into a fresh
        object would be indistinguishable from replacing it anyway.

        A topic string that fails to parse raises `ValueError` -- the caller
        already wraps this call to start empty rather than crash on a
        malformed restore (#249)."""
        by_topic: dict[Topic, set[str]] = defaultdict(set)
        for raw, subs in snapshot.items():
            topic = Topic.parse(raw)
            if topic is None:
                raise ValueError(f"not a topic: {raw!r}")
            by_topic[topic] = set(subs)
        self._by_topic = by_topic
