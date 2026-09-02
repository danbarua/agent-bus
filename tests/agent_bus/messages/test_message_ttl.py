"""Message expiry, and the delivered-copy that made every peer share one path.

Expiry lives in three places doing three different jobs, and the tests are
organised that way because conflating them is how it would go wrong:

    get_inbox   filters at 1x TTL   -- correctness; nothing stale is returned
    watch       compacts at 1x TTL  -- housekeeping, by the offset owner
    reap        collects at 2x TTL  -- garbage collection, no correctness burden

The load-bearing invariant underneath: send_message never shrinks the file, so a
live watcher's offset can only be invalidated by the watcher itself.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess

import pytest

from agent_bus import store
from agent_bus.protocol import AgentTarget


@pytest.fixture
def bus(tmp_path):
    return str(tmp_path / "bus")


@pytest.fixture
def holder():
    p = subprocess.Popen(["sleep", "30"])
    yield p
    p.kill()
    p.wait()


def _age_the_mail(path: str, seconds: float) -> None:
    """Backdate every message in an inbox by `seconds`.

    Rewrites `ts` rather than mocking a clock, so the code under test does the
    same arithmetic it does in production.
    """
    when = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=seconds)
    lines = []
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        rec["ts"] = when.isoformat()
        lines.append(json.dumps(rec) + "\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _inbox_of(name: str, bus: str) -> str:
    return store._inbox_path_for(store.find_entry(AgentTarget(name), home=bus).id, home=bus)


# --------------------------------------------------------------- read filter

def test_get_inbox_never_returns_an_expired_message(bus, holder):
    """The correctness half. Everything else is housekeeping on top of this."""
    store.register("reader", "other", pid=holder.pid, home=bus)
    store.send_message(to=AgentTarget("reader"), text="stale", from_name=AgentTarget("s"), home=bus)
    assert len(store.get_inbox(AgentTarget("reader"), home=bus)) == 1

    _age_the_mail(_inbox_of("reader", bus), store.MESSAGE_TTL_SECONDS + 60)
    assert store.get_inbox(AgentTarget("reader"), home=bus) == []


def test_an_unreadable_timestamp_counts_as_live(bus, holder):
    """Never delete because we could not parse a date. For a store whose job is
    delivery, that is the worst available failure mode."""
    store.register("reader", "other", pid=holder.pid, home=bus)
    store.send_message(to=AgentTarget("reader"), text="keep me", from_name=AgentTarget(
        "s",
    ), home=bus)

    path = _inbox_of("reader", bus)
    rec = json.loads(open(path).readline())
    rec["ts"] = "not-a-timestamp"
    open(path, "w").write(json.dumps(rec) + "\n")

    assert store.is_expired(rec) is False
    assert len(store.get_inbox(AgentTarget("reader"), home=bus)) == 1
    assert store.reap(home=bus) == 0


# ------------------------------------------------------------------- reaper

def test_reap_collects_at_twice_the_ttl_and_leaves_anything_younger(bus, holder):
    """2x, not 1x. Anything reap removes is already invisible to every reader,
    so it has no correctness burden and cannot lose a race."""
    store.register("reader", "other", pid=holder.pid, home=bus)
    store.send_message(to=AgentTarget("reader"), text="m", from_name=AgentTarget("s"), home=bus)
    path = _inbox_of("reader", bus)

    # Past the TTL but not past the reap threshold: filtered on read, still on disk.
    _age_the_mail(path, store.MESSAGE_TTL_SECONDS + 60)
    assert store.reap(home=bus) == 0, "reap must not do the read filter's job"
    assert sum(1 for _ in open(path)) == 1
    assert store.get_inbox(AgentTarget("reader"), home=bus) == []

    _age_the_mail(path, store.REAP_AFTER_SECONDS + 60)
    assert store.reap(home=bus) == 1
    assert open(path).read().strip() == ""


# ---------------------------------------------------------------- invariant

def test_send_message_never_shrinks_the_file(bus, holder):
    """The invariant the whole arrangement rests on. If a write could compact,
    a live watcher's offset would break under it -- which is exactly why
    compaction lives in the watcher and in reap, and nowhere else."""
    store.register("reader", "other", pid=holder.pid, home=bus)
    store.send_message(to=AgentTarget("reader"), text="first", from_name=AgentTarget("s"), home=bus)
    path = _inbox_of("reader", bus)
    _age_the_mail(path, store.REAP_AFTER_SECONDS + 60)

    before = os.path.getsize(path)
    store.send_message(to=AgentTarget("reader"), text="second", from_name=AgentTarget(
        "s",
    ), home=bus)
    assert os.path.getsize(path) > before, "a send compacted the file"


# ------------------------------------------------------------------- sizing

def test_the_size_boundary_is_32768(bus, holder):
    store.register("reader", "other", pid=holder.pid, home=bus)
    assert store.MAX_TEXT == 32_768
    assert store.send_message(to=AgentTarget("reader"), text="x" * 32_768, from_name=AgentTarget(
        "s",
    ), home=bus)
    with pytest.raises(ValueError, match="text too long"):
        store.send_message(to=AgentTarget("reader"), text="x" * 32_769, from_name=AgentTarget(
            "s",
        ), home=bus)


def test_the_size_error_points_at_the_alternative(bus, holder):
    """The refusal has to teach the discipline, or it just looks arbitrary."""
    store.register("reader", "other", pid=holder.pid, home=bus)
    with pytest.raises(ValueError, match="pointer"):
        store.send_message(to=AgentTarget("reader"), text="x" * 40_000, from_name=AgentTarget(
            "s",
        ), home=bus)


# --------------------------------------------------------------------- watch

def test_read_records_recovers_when_the_file_shrinks(bus, holder):
    """Without this the watcher goes silent for good.

    _read_records seeks to a stored byte offset. Compaction rewrites the file
    shorter, so the offset lands past EOF, every read returns nothing, and the
    offset never advances. agent-bus watch is our own process, so this is one
    bug in one call site -- but a silent watcher is grok, omp and pi no longer
    hearing about mail.

    The guard is size-based, so it detects the file getting SHORTER. A rewrite
    to the same length would leave a stale offset undetected -- which is fine
    here, because the only rewrites are compaction and reap, and both remove
    records. Worth knowing rather than assuming the offset is always right.
    """
    from agent_bus.watch import _read_records

    store.register("reader", "other", pid=holder.pid, home=bus)
    store.send_message(to=AgentTarget("reader"), text="x" * 2000, from_name=AgentTarget(
        "s",
    ), home=bus)
    path = _inbox_of("reader", bus)

    _, offset = _read_records(path, 0)
    assert offset == os.path.getsize(path)

    # Compaction or reap removed it, and the file is now genuinely shorter.
    open(path, "w").close()
    store.send_message(to=AgentTarget("reader"), text="after", from_name=AgentTarget("s"), home=bus)
    assert os.path.getsize(path) < offset, "test needs a real shrink to be meaningful"

    records, new_offset = _read_records(path, offset)
    assert [r["text"] for r in records] == ["after"], "watcher did not recover from truncation"
    assert new_offset == os.path.getsize(path)


def test_compact_inbox_drops_expired_and_keeps_live(bus, holder):
    """What a running watcher calls for its own inbox, at 1x TTL."""
    from agent_bus.store import compact_inbox

    store.register("reader", "other", pid=holder.pid, home=bus)
    store.send_message(to=AgentTarget("reader"), text="old", from_name=AgentTarget("s"), home=bus)
    path = _inbox_of("reader", bus)
    _age_the_mail(path, store.MESSAGE_TTL_SECONDS + 60)
    store.send_message(to=AgentTarget("reader"), text="new", from_name=AgentTarget("s"), home=bus)

    assert compact_inbox(path) == 1
    remaining = [json.loads(line)["text"] for line in open(path) if line.strip()]
    assert remaining == ["new"]
    assert compact_inbox(path) == 0, "compaction must be idempotent"
