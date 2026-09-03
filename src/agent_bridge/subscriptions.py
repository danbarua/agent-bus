"""Who wants which topic.

A dictionary with a stated lifetime, which is the honest description: #223
decided subscriptions live in Firestore and #249 records that *how* is still
open -- the op, the document shape, and what `SUBSCRIBE` does when the cloud is
unreachable are three different products, not three implementations of one.

So this is the interface that decision will fill in, and until it does the
dictionary is in memory. That is a real limitation with a real consequence,
and #68 is explicit that the consequence has to be **stated rather than
discovered**: subscriptions do not survive a restart, and the bridge says so
when it starts rather than leaving an agent silently deaf.

Fan-out is one addressed copy per subscriber, never a broadcast (#59) -- so
what this returns is a set of names to send to, and the sending is the
caller's.
"""

from __future__ import annotations

from collections import defaultdict


class Subscriptions:
    """Topic -> subscribers, and the inverse for answering an agent."""

    def __init__(self) -> None:
        self._by_topic: dict[str, set[str]] = defaultdict(set)

    def add(self, subscriber: str, topic: str) -> None:
        """Idempotent. Subscribing twice is one subscription, not two
        deliveries -- #67 asked which, and duplicate wake-ups for one event is
        the answer nobody wants."""
        self._by_topic[topic].add(subscriber)

    def remove(self, subscriber: str, topic: str) -> None:
        """Also idempotent, and takes the same literal `add` took. That is why
        a topic is an exact string rather than a pattern that might
        normalise (#67)."""
        self._by_topic.get(topic, set()).discard(subscriber)
        if not self._by_topic.get(topic):
            self._by_topic.pop(topic, None)

    def of(self, subscriber: str) -> list[str]:
        """What this agent is subscribed to, sorted so a reply reads the same
        way twice. An agent that has been compacted or restarted has to be able
        to *ask* -- without it the choices are re-subscribing defensively and
        double-delivering, or assuming and being silently deaf (#67)."""
        return sorted(t for t, subs in self._by_topic.items() if subscriber in subs)

    def subscribers_for(self, topics: set[str]) -> set[str]:
        """Everyone who asked for any of these. Set membership, so the cost is
        the same for one subscriber or fifty."""
        out: set[str] = set()
        for topic in topics:
            out |= self._by_topic.get(topic, set())
        return out

    def __len__(self) -> int:
        return sum(len(s) for s in self._by_topic.values())
