"""`notify.py` against real GitHub deliveries captured from staging.

Same fixtures, same reasoning as `test_topics_against_real_payloads.py`:
`test_notify.py` covers the shape with hand-built and curated payloads, and
this file exists because a real GitHub delivery is not obligated to match the
shape imagined when `parse_event`/`notification`/`digest` were written. It is
also where the original digest bug would have been caught immediately --
`_digest_number` reading only `payload["pull_request"]` -- had it existed
before that bug did, rather than after `scripts/preview_notifications.py`
found it by hand.
"""

from __future__ import annotations

import json
import os

import pytest

from agent_bridge import notify
from agent_bridge.topics import topics_for

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "cloud", "tests", "fixtures", "github_webhooks")

with open(os.path.join(FIXTURES, "MANIFEST.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


def _load(entry):
    with open(os.path.join(FIXTURES, entry["file"]), encoding="utf-8") as f:
        return json.load(f)


# Only a delivery that actually matches a topic ever reaches `notification()`
# in `bridge.py` -- computed once here rather than inside each parametrized
# case, since `topics_for` is pure and the fixture set does not change.
MATCHED = [m for m in MANIFEST if topics_for(m["event"], _load(m))]


@pytest.mark.parametrize("entry", MANIFEST, ids=[m["file"] for m in MANIFEST])
def test_every_real_delivery_parses_without_raising(entry):
    """A real payload from a repository we do not control must never take the
    bridge down turning into a typed event -- this is the local, parsing half
    of the ingress's own promise, applied to the same untrusted shape
    `test_topics_against_real_payloads.py` already exercises for matching."""
    notify.parse_event(entry["event"], _load(entry), entry["delivery_id"])


@pytest.mark.parametrize("entry", MATCHED, ids=[m["file"] for m in MATCHED])
def test_every_matched_real_delivery_renders_without_raising(entry):
    """The actual `bridge.py` path: `topics_for` matches, then `parse_event`
    and `notification` render what a subscriber receives -- exercised end to
    end against real traffic rather than a hand-built payload."""
    payload = _load(entry)
    matched = topics_for(entry["event"], payload)
    parsed = notify.parse_event(entry["event"], payload, entry["delivery_id"])

    notif = notify.notification(matched, parsed)

    assert notif.summary
    assert notif.body
    assert notif.delivery_metadata.delivery_ids == (entry["delivery_id"],)


def test_a_real_pr_notification_names_its_own_number_and_repo():
    entry = next(m for m in MANIFEST if m["event"] == "pull_request")
    payload = _load(entry)
    parsed = notify.parse_event("pull_request", payload, entry["delivery_id"])

    notif = notify.notification(topics_for("pull_request", payload), parsed)

    number = payload["pull_request"]["number"]
    repo = entry["repo"]
    assert f"#{number}" in notif.summary
    assert f"gh pr view {number} -R {repo} --comments" in notif.body


def test_a_real_merge_notification_says_merged_not_the_raw_action():
    """`action` on a merged PR's own payload is `closed` -- GitHub does not
    send a `merged` action. The notification has to say what actually
    happened, not echo the field, or "merged" and "closed without merging"
    read identically to a subscriber."""
    merges = [m for m in MANIFEST if m["event"] == "pull_request" and m["action"] == "closed"
              and _load(m)["pull_request"].get("merged")]
    assert merges, "no real merge event was captured"
    for entry in merges:
        payload = _load(entry)
        parsed = notify.parse_event("pull_request", payload, entry["delivery_id"])
        notif = notify.notification(topics_for("pull_request", payload), parsed)
        assert "merged into" in notif.summary, (entry["file"], notif.summary)
        assert "- action: merged" in notif.body, (entry["file"], notif.body)


def test_a_real_issue_notification_never_says_gh_pr():
    entry = next(m for m in MANIFEST if m["event"] == "issues")
    payload = _load(entry)
    parsed = notify.parse_event("issues", payload, entry["delivery_id"])

    notif = notify.notification(topics_for("issues", payload), parsed)

    assert "gh issue view" in notif.body
    assert "gh pr" not in notif.body


def test_a_real_sub_issue_link_names_both_numbers():
    entry = next(m for m in MANIFEST if m["event"] == "sub_issues")
    payload = _load(entry)
    parsed = notify.parse_event("sub_issues", payload, entry["delivery_id"])

    notif = notify.notification(topics_for("sub_issues", payload), parsed)

    assert f"#{payload['parent_issue']['number']}" in notif.body
    assert f"#{payload['sub_issue']['number']}" in notif.body


def test_a_real_digest_of_everything_matching_one_topic_never_raises():
    """Whatever `_fan_out_batch` would actually group together in one poll --
    every real delivery that matches the same topic -- collapsed into a
    digest without raising and without an unresolved `?` in its numbers."""
    by_topic: dict[str, list[notify.GitHubEvent]] = {}
    for entry in MATCHED:
        payload = _load(entry)
        parsed = notify.parse_event(entry["event"], payload, entry["delivery_id"])
        for topic in topics_for(entry["event"], payload):
            by_topic.setdefault(topic, []).append(parsed)

    grouped = {topic: events for topic, events in by_topic.items() if len(events) > 1}
    assert grouped, "no real topic had more than one matching delivery to digest"
    for topic, events in grouped.items():
        result = notify.digest(topic, events)
        numbers_line = next(line for line in result.body.splitlines()
                            if line.startswith("- numbers:"))
        assert "?" not in numbers_line, (topic, numbers_line)
