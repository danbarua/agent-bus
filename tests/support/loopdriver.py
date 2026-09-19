"""Drive the bridge loop on a clock that only moves when the loop sleeps.

Backoff is measured in loop time, so a test that watches minutes of outage
takes none of them.
"""

from __future__ import annotations

import urllib.error

import pytest

from agent_bridge import bridge as bridge_mod

DNS = urllib.error.URLError("[Errno 8] nodename nor servname provided")


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t

    def wall(self) -> float:
        return 1_800_000_000.0 + self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Stop(Exception):
    pass


class Cloud:
    def __init__(self):
        self.healthy = False
        self.pushes: list[str] = []
        self.pulls = 0
        self.acked: list[str] = []

    def push(self, address, message):
        if not self.healthy:
            raise DNS
        self.pushes.append(message["id"])
        return message["id"]

    def pull(self, address):
        self.pulls += 1
        if not self.healthy:
            raise DNS
        return []

    def ack(self, address, ids):
        self.acked.extend(ids)

    def publish_roster(self, address, agents):
        if not self.healthy:
            raise DNS

    def read(self, address, message_id):
        return {"queue": None, "message": None}

    def subscriptions(self, address, snapshot):
        if not self.healthy:
            raise DNS
        return snapshot or {}

    def pair(self, address, peer):
        pass


def drive(cloud, bus, clock, gates, passes, *, kind="desktop", name="claude", subs=None,
           before=None):
    """Run the real loop for `passes` passes on a clock that only moves when the
    loop sleeps, so backoff is measured in loop time and the test takes none."""
    address = bridge_mod.bridge_address(kind, name)
    entry = bridge_mod._join(address, bus)
    seen = {"n": 0}

    def sleep(seconds):
        seen["n"] += 1
        if before:
            before(seen["n"])
        if seen["n"] >= passes:
            raise Stop
        clock.advance(seconds)

    with pytest.raises(Stop):
        bridge_mod._serve(cloud, address, entry, bus, False, False,
                          1.0, 120.0, None, subs, gates=gates, clock=clock.monotonic,
                          sleep=sleep)
    return entry
