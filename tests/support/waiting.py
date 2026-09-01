"""Wait for a thing to become true, rather than assuming a signal was instant.

Most of our teardown is asynchronous by design. `stop_uds_listen` sends SIGTERM
and returns; the listener clears its own session file from its signal handler,
whenever it next gets scheduled. "I asked it to stop" and "it has stopped" are
two different moments, so an assertion written at the first one is testing the
scheduler, not the code.

Several tests already hand-roll this loop correctly (`test_presence_
reconciliation.py` is the clearest). This is that loop, named, so the timeout
is the only thing a test has to think about.

Deliberately not a `sleep`. A fixed sleep is either too short (flaky again) or
too long (a slower suite for every passing run), and it never says what it was
waiting for when it gives up.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

DEFAULT_TIMEOUT = 10.0
POLL_SECONDS = 0.05


def wait_until(predicate: Callable[[], Any], what: str,
               timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Poll `predicate` until it returns something truthy; return that value.

    Raises `AssertionError` naming `what` on timeout, so a genuine regression
    reads as "the listener never went away" rather than as a bare `False`.

    `what` is required on purpose: an un-named wait that starts failing tells
    the next reader nothing about which of the several things in flight was
    the one that never happened.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout:g}s waiting for: {what}")
        time.sleep(POLL_SECONDS)


def wait_until_gone(predicate: Callable[[], Any], what: str,
                    timeout: float = DEFAULT_TIMEOUT) -> None:
    """The other half, for the commoner case: wait for something to stop being
    true. Separate rather than a `negate=` flag so the call site reads as the
    thing it is waiting for."""
    wait_until(lambda: not predicate(), what, timeout)
