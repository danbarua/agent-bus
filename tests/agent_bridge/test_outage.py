"""A run of identical failures is one record, then a few, then the end of the run.

The real log this exists for was 14 MB, 95% of it two warnings repeated on every
poll for six days. Nothing said the outage began, was continuing, or ended.
Clock and rng are injected so nothing here sleeps.
"""

from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from loopdriver import DNS, Clock, Cloud, drive

from agent_bridge import bridge as bridge_mod
from agent_bridge.outage import RETRY_CAP_SECONDS, CloudError, Gates, Outage
from agent_bus import store
from agent_bus.protocol import AgentTarget, BridgeAddress


@pytest.fixture
def clock():
    return Clock()


def _outage(clock, rng=lambda: 0.5, op="pull"):
    return Outage(op, clock=clock.monotonic, wall=clock.wall, rng=rng)


def _fail(exc):
    def fn():
        raise exc

    return fn


def _records(dest):
    return [json.loads(line) for line in dest.read_text().splitlines()
            if json.loads(line).get("service") == "agent-bridge"]


def _outage_records(dest):
    return [r for r in _records(dest) if r["message"].startswith("cloud_call_")]


def _hammer(outage, exc, times, *, interval=5.0):
    for _ in range(times):
        outage.attempt(_fail(exc), interval=interval, force=True)


# -- the schedule ------------------------------------------------------------


def test_the_first_failure_is_logged_with_everything_needed_to_read_it(bridge_log, clock):
    out = _outage(clock)
    out.attempt(_fail(DNS), interval=120.0)

    (rec,) = _outage_records(bridge_log)
    assert rec["message"] == "cloud_call_failed"
    assert rec["severity"] == "WARNING"
    assert rec["op"] == "pull"
    assert rec["error"] == "URLError"
    assert "nodename nor servname" in rec["error_message"]
    assert rec["consecutive"] == 1
    assert rec["suppressed"] == 0
    assert rec["retry_in_seconds"] == 120.0
    assert rec["since"] == bridge_mod.bus_log.iso_utc(clock.wall())
    assert "status" not in rec


def test_a_hundred_failures_are_seven_records_at_the_powers_of_two(bridge_log, clock):
    _hammer(_outage(clock), DNS, 100)

    assert [r["consecutive"] for r in _outage_records(bridge_log)] == [1, 2, 4, 8, 16, 32, 64]


def test_four_thousand_failures_are_twelve_records_not_four_thousand(bridge_log, clock):
    _hammer(_outage(clock), DNS, 4000)

    assert len(_outage_records(bridge_log)) == 12


def test_every_failure_is_accounted_for_by_a_record_or_a_suppressed_count(bridge_log, clock):
    _hammer(_outage(clock), DNS, 100)

    recs = _outage_records(bridge_log)
    assert [r["suppressed"] for r in recs] == [0, 0, 1, 3, 7, 15, 31]
    assert len(recs) + sum(r["suppressed"] for r in recs) == recs[-1]["consecutive"]


def test_every_record_of_one_run_names_when_the_run_began(bridge_log, clock):
    out = _outage(clock)
    began = clock.wall()
    for _ in range(9):
        clock.advance(7)
        out.attempt(_fail(DNS), interval=5.0, force=True)

    assert {r["since"] for r in _outage_records(bridge_log)} == {
        bridge_mod.bus_log.iso_utc(began + 7)}


def test_the_first_success_after_failures_says_how_long_and_how_many(bridge_log, clock):
    out = _outage(clock)
    _hammer(out, DNS, 5)
    clock.advance(600)
    got = out.attempt(lambda: "fine", interval=5.0)

    assert got.ok
    assert got.value == "fine"
    rec = _outage_records(bridge_log)[-1]
    assert rec["message"] == "cloud_call_recovered"
    assert rec["severity"] == "INFO"
    assert rec["op"] == "pull"
    assert rec["failures"] == 5
    assert rec["outage_seconds"] == 600.0
    assert rec["since"] == _outage_records(bridge_log)[0]["since"]


def test_success_on_a_healthy_operation_writes_nothing(bridge_log, clock):
    out = _outage(clock)
    for _ in range(3):
        out.attempt(lambda: None, interval=5.0)

    assert _records(bridge_log) == []


def test_a_recovered_outage_starts_the_count_again(bridge_log, clock):
    out = _outage(clock)
    _hammer(out, DNS, 3)
    out.attempt(lambda: None, interval=5.0, force=True)
    _hammer(out, DNS, 2)

    kinds = [(r["message"], r.get("consecutive")) for r in _outage_records(bridge_log)]
    assert kinds == [
        ("cloud_call_failed", 1), ("cloud_call_failed", 2),
        ("cloud_call_recovered", None),
        ("cloud_call_failed", 1), ("cloud_call_failed", 2),
    ]


# -- severity ------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_refusal_that_will_not_fix_itself_is_an_error(bridge_log, clock, status):
    _outage(clock).attempt(_fail(CloudError("pull", status, "no")), interval=5.0)

    (rec,) = _outage_records(bridge_log)
    assert rec["message"] == "cloud_call_refused"
    assert rec["severity"] == "ERROR"
    assert rec["status"] == status
    assert rec["error"] == "CloudError"
    assert rec["error_message"] == f"cloud refused pull: HTTP {status} no"


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
def test_a_cloud_error_that_may_pass_is_a_warning(bridge_log, clock, status):
    _outage(clock).attempt(_fail(CloudError("pull", status, "busy")), interval=5.0)

    (rec,) = _outage_records(bridge_log)
    assert rec["message"] == "cloud_call_failed"
    assert rec["severity"] == "WARNING"
    assert rec["status"] == status


def test_a_change_of_class_is_recorded_at_once_and_does_not_reset_the_count(bridge_log, clock):
    out = _outage(clock)
    out.attempt(_fail(DNS), interval=5.0, force=True)
    out.attempt(_fail(DNS), interval=5.0, force=True)
    out.attempt(_fail(CloudError("pull", 401, "expired")), interval=5.0, force=True)
    out.attempt(_fail(CloudError("pull", 401, "expired")), interval=5.0, force=True)
    out.attempt(_fail(CloudError("pull", 401, "expired")), interval=5.0, force=True)

    seen = [(r["consecutive"], r["severity"], r["suppressed"]) for r in _outage_records(bridge_log)]
    assert seen == [(1, "WARNING", 0), (2, "WARNING", 0), (3, "ERROR", 0), (4, "ERROR", 0)]


# -- when the next attempt is due -------------------------------------------------


def test_a_failing_operation_is_not_asked_again_before_it_is_due(clock):
    out = _outage(clock)
    calls = []

    def fn():
        calls.append(clock.t)
        raise DNS

    out.attempt(fn, interval=5.0)
    clock.advance(4.9)
    skipped = out.attempt(fn, interval=5.0)
    clock.advance(0.2)
    out.attempt(fn, interval=5.0)

    assert skipped.skipped
    assert not skipped.ok
    assert skipped.error is None
    assert len(calls) == 2


def test_the_wait_doubles_from_the_loops_own_interval_up_to_the_cap(bridge_log, clock):
    out = _outage(clock)
    waits = []
    for _ in range(9):
        out.attempt(_fail(DNS), interval=5.0, force=True)
        waits.append(out._retry_at - clock.t)

    assert waits == [5, 10, 20, 40, 80, 160, 300, 300, 300]
    assert RETRY_CAP_SECONDS == 300.0


def test_the_wait_is_measured_in_the_interval_of_the_moment(clock):
    out = _outage(clock)
    out.attempt(_fail(DNS), interval=120.0, force=True)
    assert out._retry_at - clock.t == 120.0
    out.attempt(_fail(DNS), interval=5.0, force=True)
    assert out._retry_at - clock.t == 10.0


@pytest.mark.parametrize(("rng", "factor"), [(lambda: 0.0, 0.8), (lambda: 0.999999, 1.2)])
def test_jitter_is_twenty_percent_either_way(clock, rng, factor):
    out = _outage(clock, rng=rng)
    out.attempt(_fail(DNS), interval=10.0, force=True)
    assert out._retry_at - clock.t == pytest.approx(10.0 * factor, rel=1e-4)


def test_jitter_is_applied_after_the_cap(clock):
    out = _outage(clock, rng=lambda: 1.0)
    for _ in range(12):
        out.attempt(_fail(DNS), interval=5.0, force=True)
    assert out._retry_at - clock.t == pytest.approx(RETRY_CAP_SECONDS * 1.2)


def test_a_very_long_outage_does_not_overflow(clock):
    out = _outage(clock)
    out.consecutive = 5000
    out.attempt(_fail(DNS), interval=120.0, force=True)
    assert out._retry_at - clock.t == RETRY_CAP_SECONDS


def test_once_attempts_every_call_whatever_the_gate_says(clock):
    out = _outage(clock)
    calls = []

    def fn():
        calls.append(1)
        raise DNS

    for _ in range(4):
        out.attempt(fn, interval=5.0, force=True)
    assert len(calls) == 4


def test_the_gates_are_independent_operations(bridge_log, clock):
    gates = Gates.new(clock=clock.monotonic, wall=clock.wall, rng=lambda: 0.5)
    gates.roster.attempt(_fail(DNS), interval=5.0)
    assert not gates.roster.due()
    assert gates.pull.due() and gates.push.due() and gates.ack.due()
    assert [r["op"] for r in _outage_records(bridge_log)] == ["roster"]


# -- the client says why -----------------------------------------------------------


class _Refusing(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(401)
        self.send_header("Content-Type", "application/problem+json")
        self.end_headers()
        self.wfile.write(json.dumps({"title": "Unauthorized", "detail": "token expired"}).encode())

    def log_message(self, format, *args):
        pass


@pytest.fixture
def refusing_server():
    server = HTTPServer(("127.0.0.1", 0), _Refusing)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


ADDRESS = BridgeAddress("desktop:claude")


def test_an_http_refusal_carries_its_status_and_keeps_its_text(refusing_server):
    client = bridge_mod.HttpCloudClient(refusing_server, "t")
    with pytest.raises(RuntimeError) as caught:
        client.pull(ADDRESS)

    exc = caught.value
    assert isinstance(exc, CloudError)
    assert (exc.op, exc.status, exc.detail) == ("pull", 401, "token expired")
    assert str(exc) == "cloud refused pull: HTTP 401 token expired"


def test_a_dead_connection_stays_a_transport_error_not_a_refusal():
    client = bridge_mod.HttpCloudClient("http://127.0.0.1:9", "t", timeout=2)
    with pytest.raises(urllib.error.URLError) as caught:
        client.pull(ADDRESS)
    assert not isinstance(caught.value, CloudError)


# -- through the loop --------------------------------------------------------------


def _unread(bus, name):
    from agent_bus.commands import messages

    return [m["id"] for m in messages.poll_inbox(target=name, unread_only=True, home=bus)]


def test_mail_stays_unread_while_the_push_is_backing_off_and_goes_after_recovery(
        bus, bridge_log, clock):
    address = bridge_mod.bridge_address("desktop", "claude")
    bridge_mod._join(address, bus)
    mid = store.send_message(to=bridge_mod.bridge_name(address), text="must not vanish",
                             from_name=AgentTarget("s"), home=bus)
    cloud = Cloud()
    gates = Gates.new(clock=clock.monotonic, wall=clock.wall, rng=lambda: 0.5)

    def heal_at_pass_twenty_five(n):
        if not cloud.pushes:
            assert _unread(bus, "desktop-claude") == [mid], f"pass {n}: the mail went missing"
        if n == 25:
            cloud.healthy = True

    drive(cloud, bus, clock, gates, 60, before=heal_at_pass_twenty_five)

    push_records = [r for r in _outage_records(bridge_log) if r["op"] == "push"]
    assert cloud.pushes == [mid], "forwarded once, after the cloud came back"
    assert [r["message"] for r in push_records][-1] == "cloud_call_recovered"
    assert push_records[0]["trace_id"] == mid, "the failure names the message it held"
    assert [r["consecutive"] for r in push_records if "consecutive" in r] == [1, 2, 4]
    assert push_records[-1]["failures"] == 5
    assert _unread(bus, "desktop-claude") == [], "and acked locally once it went"


def test_a_dead_cloud_is_asked_less_often_than_the_loop_runs(bus, bridge_log, clock):
    cloud = Cloud()
    gates = Gates.new(clock=clock.monotonic, wall=clock.wall, rng=lambda: 0.5)

    drive(cloud, bus, clock, gates, 600)

    assert cloud.pulls < 12, f"600 seconds of a dead cloud cost {cloud.pulls} pulls"
    assert {r["op"] for r in _outage_records(bridge_log)} >= {"pull", "roster"}


def test_roster_and_pull_are_gated_independently(bus, bridge_log, clock):
    class RosterOnly(Cloud):
        def pull(self, address):
            self.pulls += 1
            return []

    cloud = RosterOnly()
    gates = Gates.new(clock=clock.monotonic, wall=clock.wall, rng=lambda: 0.5)

    drive(cloud, bus, clock, gates, 40)

    ops = {r["op"] for r in _outage_records(bridge_log)}
    assert "roster" in ops
    assert "pull" not in ops
    assert cloud.pulls > 5, "a healthy pull is not slowed by a failing roster"


def test_a_webhook_bridge_still_answers_its_own_mail_during_an_outage(bus, bridge_log, clock):
    from agent_bridge.subscriptions import Subscriptions
    from agent_bus.commands import messages

    address = bridge_mod.bridge_address("webhook", "github")
    bridge_mod._join(address, bus)
    import subprocess

    peer = subprocess.Popen(["sleep", "30"])
    try:
        them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
        store.send_message(to=bridge_mod.bridge_name(address), text="SUBSCRIPTIONS",
                           from_name=AgentTarget(them.name), home=bus)
        cloud = Cloud()
        gates = Gates.new(clock=clock.monotonic, wall=clock.wall, rng=lambda: 0.5)

        drive(cloud, bus, clock, gates, 30, kind="webhook", name="github", subs=Subscriptions())

        got = messages.inbox(target=them.name, unread_only=False, home=bus)
        assert any("subscri" in (m["text"] or "").lower() for m in got), got
        assert [r for r in _records(bridge_log) if r["message"] == "control"]
    finally:
        peer.kill()
        peer.wait()


def test_a_single_pass_attempts_every_call_however_recently_it_failed(bus, clock):
    cloud = Cloud()
    gates = Gates.new(clock=clock.monotonic, wall=clock.wall, rng=lambda: 0.5)
    address = bridge_mod.bridge_address("desktop", "claude")
    entry = bridge_mod._join(address, bus)

    for _ in range(3):
        bridge_mod._serve(cloud, address, entry, bus, False, True,
                          1.0, 120.0, None, None, gates=gates, clock=clock.monotonic)

    assert cloud.pulls == 3
