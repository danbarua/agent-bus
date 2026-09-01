"""#148: a `register`-driven rename leaves the old name resolving for a grace
window, instead of going dead the instant the rename lands.

Before this, a sender holding a name it learned before a peer renamed got
nothing on its next send -- not an error naming what happened, just the same
"no such agent" a typo gets. The window is `FORMER_NAME_GRACE_SECONDS`
(aliased to `MESSAGE_TTL_SECONDS`): a rename is a symptom of active
collaboration, not a long-lived redirect, and a stale reference gets the same
grace a message already in flight gets before it expires. Aligns with Claude
Code's own `formerNames` field and its `{name, until}` shape --
docs/harnesses/claude-code-presence.md#2 -- though `until` here, as there, is
the timestamp the rename happened, not a deadline stored up front; whether a
name still resolves is evaluated at read time against the current policy.
"""

from __future__ import annotations

import datetime
import subprocess

import pytest

from agent_bus import store


@pytest.fixture
def bus(tmp_path):
    return str(tmp_path / "bus")


@pytest.fixture
def holder():
    p = subprocess.Popen(["sleep", "30"])
    yield p
    p.kill()
    p.wait()


def _age_the_rename(name: str, seconds: float, bus: str) -> None:
    """Backdate an entry's most recent former-name record by `seconds`.

    Rewrites `until` rather than mocking a clock, matching
    test_message_ttl.py's `_age_the_mail` -- the code under test does the same
    arithmetic it does in production.
    """
    entry = store.find_entry(name, home=bus)
    when = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=seconds)
    entry.formerNames[0]["until"] = when.isoformat()
    store.save_roster_entry(entry, home=bus)


def test_a_sender_can_still_reach_the_old_name_after_a_rename(bus, holder):
    """The regression #148 is actually about: send to a name, rename the
    holder, send to the old name again. Before this it failed silently."""
    store.register("before", "other", pid=holder.pid, home=bus)
    assert store.send_message(to="before", text="first", from_name="s", home=bus)

    store.register("after", "other", pid=holder.pid, home=bus)

    sent = store.send_message(to="before", text="second", from_name="s", home=bus)
    assert sent, "a sender holding the pre-rename name got nothing"

    texts = [m["text"] for m in store.get_inbox("after", home=bus)]
    assert texts == ["first", "second"], (
        "both sends should have reached the one entry, under either name"
    )


def test_the_old_name_stops_resolving_after_the_grace_window(bus, holder):
    store.register("before", "other", pid=holder.pid, home=bus)
    store.register("after", "other", pid=holder.pid, home=bus)
    assert store.find_entry("before", home=bus) is not None

    _age_the_rename("after", store.FORMER_NAME_GRACE_SECONDS + 60, bus)
    assert store.find_entry("before", home=bus) is None
    with pytest.raises(ValueError, match="no such agent"):
        store.send_message(to="before", text="too late", from_name="s", home=bus)


def test_renaming_back_to_the_same_name_records_nothing(bus, holder):
    """Claude Code's own rule: renaming to the name you already hold keeps
    the original `since` rather than logging a no-op rename as history."""
    store.register("steady", "other", pid=holder.pid, home=bus)
    store.register("steady", "other", pid=holder.pid, home=bus)

    entry = store.find_entry("steady", home=bus)
    assert entry.formerNames == []


def test_a_former_name_still_in_its_window_cannot_be_claimed_by_someone_else(
    bus, holder
):
    """The old name still resolves to the renamed entry, so handing it to a
    second, unrelated entry would make `find_entry` have to pick between two
    live agents for the same address."""
    store.register("shared", "other", pid=holder.pid, home=bus)
    store.register("claimed", "other", pid=holder.pid, home=bus)

    second = subprocess.Popen(["sleep", "30"])
    try:
        newcomer = store.register("shared", "other", pid=second.pid, home=bus)
        assert newcomer.name != "shared", (
            "a name still inside another entry's grace window must not be "
            "handed to an unrelated registrant"
        )
        # And the original rename's grace window still resolves correctly.
        assert store.find_entry("shared", home=bus).name == "claimed"
    finally:
        second.kill()
        second.wait()


def test_expired_former_names_are_dropped_rather_than_accumulated(bus, holder):
    """Housekeeping, not correctness: a former name past its window is pruned
    the next time this entry renames again, rather than growing forever."""
    store.register("one", "other", pid=holder.pid, home=bus)
    store.register("two", "other", pid=holder.pid, home=bus)
    _age_the_rename("two", store.FORMER_NAME_GRACE_SECONDS + 60, bus)

    store.register("three", "other", pid=holder.pid, home=bus)
    entry = store.find_entry("three", home=bus)
    assert [f["name"] for f in entry.formerNames] == ["two"], (
        "the expired 'one' entry should have been dropped, not kept alongside 'two'"
    )
