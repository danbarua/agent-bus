"""A webhook bridge end to end: subscribe, then be woken by a matching event.

The right half of the pipeline -- the ingress (#247) puts a mail-shaped event
on the queue, and this is what pulls it off, filters it and fans it out.

Driven through `bridge(..., once=True)` rather than by calling the pieces,
because the branch under test is *which* path a message takes, and that is a
property of the loop rather than of any function in it.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_bridge.bridge import bridge, bridge_name
from agent_bus import log as bus_log
from agent_bus import store
from agent_bus.commands import messages
from agent_bus.protocol import AgentTarget, BridgeAddress

ADDRESS = BridgeAddress("webhook:github")
BUS_NAME = bridge_name(ADDRESS)
REPO = "danbarua/agent-bus"


class FakeCloud:
    """Hands back what it is given, and records nothing upward -- a webhook
    queue is one-way, so `push` should never be called at all."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.pushed: list[dict] = []
        self.acked: list[str] = []
        self._subscriptions: dict[str, list[str]] = {}

    def push(self, address, message):
        self.pushed.append(message)
        return message["id"]

    def pull(self, address):
        out, self.replies = self.replies, []
        return out

    def ack(self, address, ids):
        self.acked.extend(ids)

    def publish_roster(self, address, agents):
        pass

    def read(self, address, message_id):
        return {"queue": None, "message": None}

    def subscriptions(self, address, snapshot):
        if snapshot is None:
            return self._subscriptions
        self._subscriptions = snapshot
        return snapshot


def merge_event(mid="d-1", base="main"):
    """One delivery, shaped the way the ingress shapes it: the event type in
    `summary` because it arrives as a header, the raw body in `text`."""
    return {"id": mid, "from": "github", "to": ADDRESS, "summary": "pull_request",
            "text": json.dumps({
                "action": "closed",
                "repository": {"full_name": REPO},
                "pull_request": {"number": 181, "title": "Name the strings",
                                 "merged": True, "base": {"ref": base},
                                 "merge_commit_sha": "abc123def4567",
                                 "html_url": f"https://github.com/{REPO}/pull/181"}})}



@pytest.fixture
def peer():
    p = subprocess.Popen(["sleep", "30"])
    yield p
    if p.poll() is None:
        p.kill()
        p.wait()


def _run(cloud, bus):
    bridge("webhook", "github", cloud, home=bus, once=True)


def _joined(bus):
    """One pass with nothing to do, so the bridge is on the roster.

    `once=True` deliberately does not leave the bus afterwards, so this is a
    join and nothing else -- and until it has happened there is no
    `webhook-github` for an agent to address.
    """
    _run(FakeCloud(), bus)


def _subscribe(them, bus, topic):
    messages.send(to=AgentTarget(BUS_NAME), text=f"SUBSCRIBE {topic}",
                  from_name=AgentTarget(them.name), home=bus)


def test_a_subscriber_is_woken_by_a_matching_event(bus, peer):
    """The whole point, in one pass: the control message is drained from the
    local inbox first, then the cloud is polled -- so a SUBSCRIBE and the event
    it asks for can both land in a single cycle."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    _run(FakeCloud([merge_event()]), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    texts = [m["text"] for m in inbox]
    assert any("#181" in t for t in texts), inbox
    assert any(f"{REPO}/pulls" in t for t in texts), "it says why it woke you"
    assert any('delivery="d-1"' in t for t in texts), "it says which delivery, for debugging"


def test_an_event_nobody_asked_for_wakes_nobody(bus, peer):
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/issues")

    _run(FakeCloud([merge_event()]), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    assert not any("#181" in (m["text"] or "") for m in inbox), (
        "a merge reached a subscriber who asked for issues")


class MalformedSubscriptions(FakeCloud):
    """A cloud whose stored subscriptions are not the shape they should be --
    not a network failure, a genuinely bad document. `RuntimeError` is what
    `HttpCloudClient` raises for every transport failure; this is the other
    thing that can go wrong on this same read and must degrade the same way,
    not crash startup while a network failure two lines earlier would not
    have."""

    def subscriptions(self, address, snapshot):
        if snapshot is None:
            return ["not", "a", "dict"]  # .items() raises
        return super().subscriptions(address, snapshot)


def test_a_malformed_stored_subscription_starts_empty_not_crashed(bus):
    # The whole assertion is that this does not raise. A start that crashed
    # here would never have joined the bus at all, so the roster is the
    # cheapest proof it came up: `_joined` -> `_run` -> `bridge()` completing.
    _run(MalformedSubscriptions(), bus)

    from agent_bus import store as agent_bus_store
    assert agent_bus_store.find_entry(BUS_NAME, home=bus) is not None, (
        "a malformed subscriptions document must not stop the bridge from starting"
    )


def test_a_subscription_survives_a_restart(bus, peer):
    """#249's actual claim, not the docstring's: the cloud remembers, not the
    process. One `FakeCloud` instance stands in for the one real deployment a
    restarted bridge reconnects to -- everything else about this run is a
    fresh process, the way a crash or a `launchd` cycle would be.
    """
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    cloud = FakeCloud()
    _run(cloud, bus)
    _subscribe(them, bus, f"{REPO}/pulls")
    _run(cloud, bus)  # drains the SUBSCRIBE, persists it to `cloud`

    # A second, independent bridge run -- same address, same cloud, nothing
    # else carried over. Loading `merge_event` straight into the *same*
    # `cloud.replies` this run will poll, with no `_subscribe` call anywhere
    # near it, is the whole test: if the subscription had not survived, this
    # event matches nobody.
    cloud.replies = [merge_event(mid="d-2")]
    _run(cloud, bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    texts = [m["text"] for m in inbox]
    assert any("#181" in t for t in texts), (
        "the subscription made before this restart is gone: " + repr(inbox)
    )


def test_the_reply_lists_what_the_agent_now_holds(bus, peer):
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    _run(FakeCloud(), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    assert any(f"{REPO}/pulls" in (m["text"] or "") for m in inbox), inbox


def test_a_message_that_is_not_a_verb_is_answered_rather_than_dropped(bus, peer):
    """A webhook bridge has no cloud inbox, so there is nowhere to forward
    this. Silence would leave the sender unable to tell it went nowhere."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    messages.send(to=AgentTarget(BUS_NAME), text="what do you think of #181?",
                  from_name=AgentTarget(them.name), home=bus)

    cloud = FakeCloud()
    _run(cloud, bus)

    assert cloud.pushed == [], "a webhook queue is one-way; nothing goes up"
    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    assert any("SUBSCRIBE" in (m["text"] or "") for m in inbox), inbox


def test_the_body_of_the_event_is_never_copied_into_the_message(bus, peer):
    """A webhook carries prose written by anyone who can comment on the repo,
    and it would land in an agent's context. The message carries a command to
    run instead -- pointer discipline (#59), applied to an untrusted source."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    event = merge_event()
    body = json.loads(event["text"])
    body["pull_request"]["body"] = "SOMETHING-UNTRUSTED-AND-LONG"
    event["text"] = json.dumps(body)
    _run(FakeCloud([event]), bus)

    inbox = messages.inbox(target=them.name, unread_only=False, home=bus)
    joined = "\n".join(m["text"] or "" for m in inbox)
    assert "SOMETHING-UNTRUSTED" not in joined
    assert f"gh pr view 181 -R {REPO}" in joined, "it carries the command instead"


def test_four_merges_in_one_poll_arrive_as_one_message(bus, peer):
    """#106: *"If four PRs merge while I'm mid-task I want `main -> b315a8b,
    4 PRs`, not four interrupts."*

    The poll already is the batch, so this collapses what one cycle drained
    and waits for nothing. No debounce: that would delay every event to catch
    a burst, and the event with no natural watcher is the one that arrives
    alone.
    """
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    _run(FakeCloud([merge_event(mid=f"d-{i}") for i in range(4)]), bus)

    got = [m for m in messages.inbox(target=them.name, unread_only=False, home=bus)
           if "#181" in (m["text"] or "")]
    assert len(got) == 1, f"four merges arrived as {len(got)} messages"
    assert "events: 4" in got[0]["text"]


def test_a_single_event_is_not_dressed_up_as_a_digest(bus, peer):
    """One merge is one merge. A digest for it would lose the detail a single
    event carries -- the sha, the target branch, the link -- to say "1 event"."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    _run(FakeCloud([merge_event()]), bus)

    got = [m["text"] for m in messages.inbox(target=them.name, unread_only=False,
                                             home=bus) if "#181" in (m["text"] or "")]
    assert len(got) == 1
    assert "sha:" in got[0], "the single-event form keeps the per-event detail"
    assert "events:" not in got[0]


def test_a_digest_says_the_individual_events_are_gone(bus, peer):
    """#106 names the consequence: the sha is the last one and the events are
    gone, because the bridge acked them. Right for "what does main look like
    now", wrong for "what happened" -- the digest carries only the aggregate
    fields (no per-event sha or target) plus the command that recovers the
    detail, rather than narrating what it does not have."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    _run(FakeCloud([merge_event(mid=f"d-{i}") for i in range(3)]), bus)

    got = next(m["text"] for m in messages.inbox(target=them.name, unread_only=False,
                                                 home=bus) if "events:" in (m["text"] or ""))
    assert "target:" not in got, "per-event detail belongs to the single-event form, not the digest"
    assert f"gh pr list -R {REPO}" in got



def _bridge_records(dest):
    if not dest.exists():
        return []
    return [json.loads(line) for line in dest.read_text().splitlines() if line.strip()]


def test_a_delivery_log_names_the_raw_github_event_and_the_topic(bus, peer, bridge_log):
    """The delivered case should carry at least as much as the discarded
    case already does -- `event_matched_nobody` already logs the raw
    event; a subscriber actually being woken is the more useful thing to
    debug, not the less."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    _run(FakeCloud([merge_event()]), bus)

    records = _bridge_records(bridge_log)
    delivered = [r for r in records if r.get("message") == "event_delivered"]
    assert delivered, f"no structured record for the delivery: {records}"
    assert delivered[0]["gh_event"] == "pull_request"
    assert delivered[0]["topic"] == f"{REPO}/pulls"


def _subscriber_inbox_ids(them, bus):
    return [m["id"] for m in messages.inbox(target=them.name, unread_only=False, home=bus)
            if "#181" in (m["text"] or "") or "events:" in (m["text"] or "")]


def test_a_delivery_is_one_record_per_source_event_joined_to_the_cloud_id(bus, peer, bridge_log):
    """`trace_id` is the cloud's id for the source event, so a delivery record
    ties to the cloud's copy; `delivered_id` is the local message it produced."""
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    _run(FakeCloud([merge_event(mid="d-77")]), bus)

    (rec,) = [r for r in _bridge_records(bridge_log) if r.get("message") == "event_delivered"]
    assert rec["trace_id"] == "d-77"
    assert rec["to"] == them.name
    assert rec["count"] == 1
    assert rec["delivered_id"] in _subscriber_inbox_ids(them, bus)


def test_a_digest_is_one_local_message_and_one_record_per_source_event(bus, peer, bridge_log):
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    _run(FakeCloud([merge_event(mid=f"d-{i}") for i in range(3)]), bus)

    recs = [r for r in _bridge_records(bridge_log) if r.get("message") == "event_delivered"]
    assert sorted(r["trace_id"] for r in recs) == ["d-0", "d-1", "d-2"]
    assert len({r["delivered_id"] for r in recs}) == 1, "three events, one local message"
    assert {r["count"] for r in recs} == {3}
    assert recs[0]["delivered_id"] in _subscriber_inbox_ids(them, bus)


def test_a_failed_delivery_names_the_event_and_the_cause(bus, peer, bridge_log):
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")
    peer.kill()
    peer.wait()

    _run(FakeCloud([merge_event(mid="d-9")]), bus)

    (rec,) = [r for r in _bridge_records(bridge_log)
              if r.get("message") == "event_not_delivered"]
    assert rec["trace_id"] == "d-9"
    assert rec["severity"] == "WARNING"
    assert rec["to"] == them.name
    assert rec["error"] == "ValueError"
    assert rec["gh_event"] == "pull_request"


class AckRefused(FakeCloud):
    def ack(self, address, ids):
        raise RuntimeError("cloud refused ack: HTTP 500 boom")


def test_the_webhook_path_records_its_ack_either_way(bus, peer, bridge_log):
    """The ack was `contextlib.suppress(Exception)`: a failed one left no record
    and a successful one left none either, so a message re-pulled every poll had
    nothing to explain it."""
    _joined(bus)
    good = FakeCloud([merge_event(mid="d-ok")])
    _run(good, bus)
    _run(AckRefused([merge_event(mid="d-bad")]), bus)

    records = _bridge_records(bridge_log)
    acked = [r for r in records if r.get("message") == "acked_in_cloud"]
    failed = [r for r in records if r.get("message") == "ack_failed"]
    assert [r["trace_id"] for r in acked] == ["d-ok"]
    assert [r["trace_id"] for r in failed] == ["d-bad"]
    assert failed[0]["error"] == "RuntimeError"
    assert "HTTP 500" in failed[0]["error_message"]
    assert good.acked == ["d-ok"]


def test_a_non_json_event_is_named_and_still_acked(bus, bridge_log):
    _joined(bus)
    cloud = FakeCloud([{"id": "d-bad", "summary": "push", "text": "{nope"}])
    _run(cloud, bus)

    (rec,) = [r for r in _bridge_records(bridge_log) if r.get("message") == "event_not_json"]
    assert rec["trace_id"] == "d-bad"
    assert rec["error"] == "JSONDecodeError"
    assert cloud.acked == ["d-bad"]


def test_control_names_the_subscriber_as_sender_not_recipient(bus, peer, bridge_log):
    them = store.register("labkit-dev", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/pulls")

    _run(FakeCloud(), bus)

    (rec,) = [r for r in _bridge_records(bridge_log) if r.get("message") == "control"]
    assert rec["sender"] == them.name
    assert rec["verb"] == "SUBSCRIBE"
    assert "to" not in rec
    assert rec["trace_id"]


def test_an_event_nobody_wanted_is_traced_with_its_id_and_never_at_info(bus, bridge_log,
                                                                        monkeypatch):
    """Most of the firehose is discarded on purpose, so a line per discarded
    event is TRACE -- and it names the event, which is what a person asks."""
    monkeypatch.setenv("AGENT_BUS_LOG_LEVEL", "trace")
    bus_log.configure(force=True, service="agent-bridge")
    _joined(bus)

    _run(FakeCloud([{"id": "d-star", "summary": "star", "text": "{}"}]), bus)

    (rec,) = [r for r in _bridge_records(bridge_log) if r.get("message") == "event_matched_nobody"]
    assert rec["trace_id"] == "d-star"
    assert rec["gh_event"] == "star"
    assert rec["severity"] == "DEBUG", "TRACE carries the nearest severity that exists"


# --------------------------------------------------------------- label topics


def pr_event(mid, action, labels=(), label=None, number=181):
    payload = {"action": action, "repository": {"full_name": REPO},
               "pull_request": {"number": number, "title": "Name the strings",
                                "base": {"ref": "main"}, "head": {"sha": "abc123"},
                                "labels": [{"name": n} for n in labels]}}
    if label:
        payload["label"] = {"name": label}
    return {"id": mid, "from": "github", "to": ADDRESS, "summary": "pull_request",
            "text": json.dumps(payload)}


def suite_event(mid, number=181, conclusion="success"):
    return {"id": mid, "from": "github", "to": ADDRESS, "summary": "check_suite",
            "text": json.dumps({
                "action": "completed", "repository": {"full_name": REPO},
                "check_suite": {"conclusion": conclusion, "status": "completed",
                                "head_sha": "abc123", "app": {"slug": "github-actions"},
                                "pull_requests": [{"number": number,
                                                   "head": {"sha": "abc123"}}]}})}


def _texts(them, bus):
    return [m["text"] or "" for m in messages.inbox(target=them.name, unread_only=False,
                                                     home=bus)
            if "subscriptions" not in (m.get("summary") or "")]


def test_a_label_subscriber_hears_about_a_pr_once_it_carries_the_label(bus, peer):
    them = store.register("labkit-web", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(them, bus, f"{REPO}/labels/area:web")
    cloud = FakeCloud([pr_event("d-1", "synchronize", labels=("area:agent",))])

    _run(cloud, bus)
    assert not any("#181" in t for t in _texts(them, bus)), "another area's pull request"

    cloud.replies = [pr_event("d-2", "labeled", labels=("area:agent", "area:web"),
                              label="area:web")]
    _run(cloud, bus)
    got = [t for t in _texts(them, bus) if "#181" in t]
    assert len(got) == 1 and "label: `area:web`" in got[0]


def test_one_delivery_matching_several_of_your_topics_is_one_message(bus, peer):
    """An agent holding two labels hears about a pull request carrying both
    once, not once per label."""
    them = store.register("labkit-web", "other", pid=peer.pid, home=bus)
    _joined(bus)
    for topic in (f"{REPO}/labels/area:web", f"{REPO}/labels/area:agent", f"{REPO}/pulls"):
        _subscribe(them, bus, topic)

    _run(FakeCloud([pr_event("d-1", "synchronize", labels=("area:web", "area:agent"))]), bus)

    got = [t for t in _texts(them, bus) if "#181" in t]
    assert len(got) == 1, got
    assert all(topic in got[0] for topic in (f"{REPO}/labels/area:web",
                                             f"{REPO}/labels/area:agent", f"{REPO}/pulls"))


def test_a_ci_result_reaches_a_label_subscriber_from_the_labels_the_bridge_saw(bus, peer):
    """A check suite carries no labels: the pull request's own delivery in
    the same poll says which it has."""
    other = subprocess.Popen(["sleep", "30"])
    web = store.register("labkit-web", "other", pid=peer.pid, home=bus)
    agent = store.register("labkit-agent", "other", pid=other.pid, home=bus)
    _joined(bus)
    _subscribe(web, bus, f"{REPO}/labels/area:web")
    _subscribe(agent, bus, f"{REPO}/labels/area:agent")

    _run(FakeCloud([pr_event("d-1", "labeled", labels=("area:agent",), label="area:agent"),
                    suite_event("d-2")]), bus)

    try:
        # Same topic, same poll: the label and the result arrive as one digest.
        assert any("check suite: success" in t for t in _texts(agent, bus))
        assert not any("check" in t for t in _texts(web, bus)), "not labelled area:web"
    finally:
        other.kill()
        other.wait()


def test_a_ci_result_for_a_pr_the_bridge_has_not_seen_reaches_every_label_subscriber(bus, peer):
    """After a restart the bridge does not know the pull request's labels.
    One CI result too many is noise; one too few is a result nobody saw."""
    web = store.register("labkit-web", "other", pid=peer.pid, home=bus)
    _joined(bus)
    _subscribe(web, bus, f"{REPO}/labels/area:web")

    _run(FakeCloud([suite_event("d-1")]), bus)

    assert any("check_suite" in t for t in _texts(web, bus))


class StaleSubscriptions(FakeCloud):
    """Stored under the grammar before label topics: one topic no longer parses."""

    def __init__(self):
        super().__init__()
        self._subscriptions = {f"{REPO}/pulls:merged": ["labkit-dev"],
                               f"{REPO}/pulls": ["labkit-web"]}


def test_a_stored_topic_that_no_longer_parses_is_dropped_logged_and_rewritten(bus, bridge_log):
    cloud = StaleSubscriptions()
    _run(cloud, bus)

    assert cloud._subscriptions == {f"{REPO}/pulls": ["labkit-web"]}, "the cloud copy is cleaned"
    dropped = [r for r in _bridge_records(bridge_log) if r["message"] == "subscription_dropped"]
    assert dropped and dropped[0]["topic"] == f"{REPO}/pulls:merged"
    assert dropped[0]["subscribers"] == ["labkit-dev"]
    assert dropped[0]["severity"] == "WARNING"


def test_a_merge_reaches_only_the_subscribers_of_its_own_labels(bus, peer):
    """The close is matched before the bridge forgets the pull request, so it
    goes by the labels it carried -- not to every label on the repository."""
    other = subprocess.Popen(["sleep", "30"])
    web = store.register("labkit-web", "other", pid=peer.pid, home=bus)
    agent = store.register("labkit-agent", "other", pid=other.pid, home=bus)
    _joined(bus)
    _subscribe(web, bus, f"{REPO}/labels/area:web")
    _subscribe(agent, bus, f"{REPO}/labels/area:agent")

    closed = pr_event("d-1", "closed", labels=("area:agent",))
    body = json.loads(closed["text"])
    body["pull_request"]["merged"] = True
    closed["text"] = json.dumps(body)
    _run(FakeCloud([closed]), bus)

    try:
        assert any("action: merged" in t for t in _texts(agent, bus))
        assert not any("#181" in t for t in _texts(web, bus)), "not labelled area:web"
    finally:
        other.kill()
        other.wait()
