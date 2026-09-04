"""`notify.py` against real GitHub deliveries captured from staging.

Same reasoning as `test_topics_against_real_payloads.py`: a real payload is
not obligated to match the shape imagined when a matcher was written, and the
digest bug this file guards found exactly that -- `scripts/preview_notifications.py`,
run against these same fixtures, was the first thing to ever build a digest
from an issue-shaped event rather than a hand-built pull_request one.
"""

from __future__ import annotations

import json
import os

from agent_bridge import notify

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "cloud", "tests", "fixtures", "github_webhooks")

with open(os.path.join(FIXTURES, "MANIFEST.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


def _load(entry):
    with open(os.path.join(FIXTURES, entry["file"]), encoding="utf-8") as f:
        return json.load(f)


def test_a_digest_of_real_issue_events_lists_real_numbers():
    """The original bug: `digest()` only ever read `payload["pull_request"]`,
    so every issue-shaped digest rendered `numbers: ` with nothing after it.
    No existing test caught it because none built a digest from anything but
    a hand-built pull_request event. `sub_issues` deliberately excluded here
    -- it always has a real number regardless of this bug, since it never
    went through the same `payload["pull_request"]`-only path; isolating to
    plain `issues` events is what actually exercises the fixed branch."""
    entries = [m for m in MANIFEST if m["event"] == "issues"]
    assert entries, "need at least one real issues delivery"
    events = [notify.parse_event(e["event"], _load(e), e["delivery_id"]) for e in entries]

    result = notify.digest("danbarua/agent-bus:issue", events)

    numbers_line = next(line for line in result.text.splitlines()
                        if line.startswith("- numbers:"))
    assert numbers_line != "- numbers: ", "the numbers list is empty for a real issue digest"
    assert "?" not in numbers_line, numbers_line


def test_a_digest_of_issue_events_recovers_with_gh_issue_not_gh_pr():
    """`next:` used to say `gh pr list` unconditionally, even for a digest
    that has nothing to do with pull requests."""
    entries = [m for m in MANIFEST if m["event"] == "issues"]
    assert entries, "need at least one real issues delivery"
    events = [notify.parse_event(e["event"], _load(e), e["delivery_id"]) for e in entries]

    result = notify.digest("danbarua/agent-bus:issue", events)

    assert "gh issue list" in result.text
    assert "gh pr list" not in result.text

def test_notification_structure_and_delivery_metadata():
    entries = [m for m in MANIFEST if m["event"] == "pull_request"]
    assert entries
    payload = _load(entries[0])
    parsed = notify.parse_event(entries[0]["event"], payload, entries[0]["delivery_id"])
    matched = {"danbarua/agent-bus:pr", "danbarua/agent-bus:pr.open"}

    notif = notify.notification(matched, parsed)

    assert notif.summary
    assert notif.body
    assert isinstance(notif.delivery_metadata, notify.DeliveryMetadata)

    metadata = notif.delivery_metadata
    assert metadata.matched_topics == ("danbarua/agent-bus:pr", "danbarua/agent-bus:pr.open")
    assert metadata.delivery_ids == (entries[0]["delivery_id"],)
    assert notif.text == f"{notif.body}\n\n{notif.delivery_metadata.trailer()}"

    expected =  "<sub>matched: danbarua/agent-bus:pr, danbarua/agent-bus:pr.open · delivery "
    assert expected in notif.text


def test_digest_notification_structure_and_delivery_metadata():
    entries = [m for m in MANIFEST if m["event"] == "issues"]
    assert entries
    events = [notify.parse_event(e["event"], _load(e), e["delivery_id"]) for e in entries]

    result = notify.digest("danbarua/agent-bus:issue", events)

    assert result.summary
    assert result.body
    assert isinstance(result.delivery_metadata, notify.DeliveryMetadata)
    assert result.delivery_metadata.matched_topics == ("danbarua/agent-bus:issue",)
    assert result.delivery_metadata.delivery_ids == tuple(e["delivery_id"] for e in entries)
    assert result.text == f"{result.body}\n\n{result.delivery_metadata.trailer()}"


def test_a_merge_via_auto_merge_names_its_real_merge_method():
    """The one thing `test_notify_against_real_payloads.py` cannot cover:
    every real merge captured so far went through a direct click of the
    merge button, not GitHub's "enable auto-merge" flow, so
    `pull_request.auto_merge` is `null` on all of them even though they
    merged. This is the other half -- auto_merge populated, as it is
    documented to be -- confirming the known value is rendered verbatim
    rather than the 'unknown' fallback."""
    entry = next(m for m in MANIFEST if m["event"] == "pull_request" and m["action"] == "closed")
    payload = _load(entry)
    payload["pull_request"]["merged"] = True
    payload["pull_request"]["auto_merge"] = {"merge_method": "squash"}

    parsed = notify.parse_event("pull_request", payload, entry["delivery_id"])
    notif = notify.notification({"danbarua/agent-bus:pr.merge"}, parsed)

    assert "- merge type: squash" in notif.body


def test_a_digest_of_merges_names_each_ones_merge_type():
    """#106's collapse can't lose the fact #278 established mattered: a PR
    squashed into a digest is still squashed. Each number in the digest's
    own `numbers:` line carries its merge type the same way a single
    notification's body does -- one known, one not, so the digest can't get
    away with reporting only the easy case."""
    entry = next(m for m in MANIFEST if m["event"] == "pull_request" and m["action"] == "closed")
    squashed = _load(entry)
    squashed["pull_request"]["number"] = 501
    squashed["pull_request"]["merged"] = True
    squashed["pull_request"]["auto_merge"] = {"merge_method": "squash"}
    direct = _load(entry)
    direct["pull_request"]["number"] = 502
    direct["pull_request"]["merged"] = True
    direct["pull_request"]["auto_merge"] = None

    events = [
        notify.parse_event("pull_request", squashed, "squashed-delivery"),
        notify.parse_event("pull_request", direct, "direct-delivery"),
    ]

    result = notify.digest("danbarua/agent-bus:pr.merge", events)

    numbers_line = next(line for line in result.body.splitlines()
                        if line.startswith("- numbers:"))
    assert "#501 (squash)" in numbers_line
    assert "#502 (merge type unknown)" in numbers_line
