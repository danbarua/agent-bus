"""What a running bridge actually writes to its file.

The unit tests read events; a person debugging reads the file. This runs the
loop through a real configured logger -- outage, recovery, forwards, replies,
refusals and all -- and holds the WRITTEN lines to the contract.
"""

from __future__ import annotations

import json
import logging
import subprocess

import pytest
from loopdriver import DNS, Clock, Cloud, drive

from agent_bridge import bridge as bridge_mod
from agent_bridge import events as _events  # noqa: F401  # registers every event
from agent_bridge.outage import Gates
from agent_bus import log, logevents, store
from agent_bus.protocol import AgentTarget, BridgeAddress

ADDRESS = BridgeAddress("desktop:claude")


class Flaky(Cloud):
    """Down until told otherwise, with replies waiting for whenever a pull works."""

    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)

    def pull(self, address):
        super().pull(address)
        out, self.replies = self.replies, []
        return out

    def ack(self, address, ids):
        if not self.healthy:
            raise DNS
        super().ack(address, ids)


@pytest.fixture
def written(tmp_path, monkeypatch, bus):
    """The bridge's own file. `AGENT_BRIDGE_LOG_FILE` outranks the suite-wide
    `AGENT_BUS_LOG_FILE`, which the listener the bridge spawns inherits and
    writes its own records to."""
    dest = tmp_path / "agent-bridge.jsonl"
    monkeypatch.setenv("AGENT_BRIDGE_LOG_FILE", str(dest))
    monkeypatch.setenv("AGENT_BUS_LOG_LEVEL", "info")
    log.configure(force=True, service="agent-bridge")
    yield dest
    for h in list(logging.getLogger(log.LOGGER_NAME).handlers):
        h.close()
        logging.getLogger(log.LOGGER_NAME).removeHandler(h)


def _run_a_bridge(bus):
    """A start, an outage that heals, mail out, replies in (delivered, held,
    unaddressed), a stop."""
    clock = Clock()
    peer = subprocess.Popen(["sleep", "30"])
    try:
        them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
        cloud = Flaky([
            {"id": "r-ok", "to": them.name, "text": "reviewed"},
            {"id": "r-gone", "to": "vanished", "text": "too late"},
            {"id": "r-none", "text": "to nobody"},
        ])
        gates = Gates.new(clock=clock.monotonic, wall=clock.wall, rng=lambda: 0.5)

        up = Cloud()
        up.healthy = True
        bridge_mod.bridge("desktop", "claude", up, home=bus, once=True)
        store.send_message(to=bridge_mod.bridge_name(ADDRESS), text="out",
                           from_name=AgentTarget(them.name), home=bus)

        def heal(n):
            if n == 12:
                cloud.healthy = True

        drive(cloud, bus, clock, gates, 40, before=heal)
    finally:
        peer.kill()
        peer.wait()


def _lines(dest):
    return [json.loads(line) for line in dest.read_text().splitlines()]


def test_the_written_file_keeps_the_contract(written, bus):
    _run_a_bridge(bus)

    lines = _lines(written)
    assert len(lines) > 20, "the scenario produced too little to prove anything"

    for rec in lines:
        present = [k for k in rec if k in logevents.ENVELOPE]
        assert present == [k for k in logevents.ENVELOPE if k in present], (
            "envelope keys out of order", rec)
        assert list(rec)[: len(present)] == present, ("envelope keys not first", list(rec))
        assert all(v is not None for v in rec.values()), ("null value", rec)

    by_name = {c.message: c for c in logevents.all_events()}
    for rec in lines:
        cls = by_name.get(rec["message"])
        if cls is not None and issubclass(cls, logevents.MessageEvent):
            assert rec.get("trace_id"), ("a message event with no trace_id", rec)

    typed = [r for r in lines if r["message"] in by_name]
    assert {r["message"] for r in typed} >= {
        "bridge_started", "bridge_stopped", "cloud_call_failed", "cloud_call_recovered",
        "forwarded", "acked_locally", "delivered", "acked_in_cloud", "reply_held",
        "reply_without_addressee",
    }
    assert all(r["service"] == "agent-bridge" for r in typed)


def test_no_typed_record_uses_a_key_the_registry_does_not_own(written, bus):
    _run_a_bridge(bus)

    envelope = set(logevents.ENVELOPE)
    by_name = {c.message for c in logevents.all_events()}
    for rec in _lines(written):
        if rec["message"] in by_name:
            stray = set(rec) - envelope - set(logevents.FIELDS)
            assert not stray, (rec["message"], stray)


def test_the_outage_is_a_handful_of_records_and_ends_with_a_recovery(written, bus):
    _run_a_bridge(bus)

    for op in ("pull", "roster", "push"):
        mine = [r for r in _lines(written)
                if r["message"].startswith("cloud_call_") and r["op"] == op]
        assert mine[0]["message"] == "cloud_call_failed", op
        assert mine[-1]["message"] == "cloud_call_recovered", op
        assert len(mine) <= 5, (op, len(mine))
