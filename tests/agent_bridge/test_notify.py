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

def test_notification_structure_and_provenance():
    """A single notification's trailer names its one delivery as an
    attribute and lists every matched topic on its own line -- #279's
    correction of the earlier design, which comma-joined delivery ids into
    that same attribute to also serve digest()'s different (one topic, many
    deliveries) shape."""
    entries = [m for m in MANIFEST if m["event"] == "pull_request"]
    assert entries
    payload = _load(entries[0])
    parsed = notify.parse_event(entries[0]["event"], payload, entries[0]["delivery_id"])
    matched = {"danbarua/agent-bus:pr", "danbarua/agent-bus:pr.open"}

    notif = notify.notification(matched, parsed)

    assert notif.summary
    assert notif.body
    assert isinstance(notif.provenance, notify.Provenance)

    provenance = notif.provenance
    assert provenance.matched_topics == ("danbarua/agent-bus:pr", "danbarua/agent-bus:pr.open")
    assert provenance.delivery_id == entries[0]["delivery_id"]
    assert notif.text == f"{notif.body}\n\n{notif.provenance.trailer()}"

    expected = (
        f'<sub delivery="{entries[0]["delivery_id"]}">\n'
        "danbarua/agent-bus:pr\ndanbarua/agent-bus:pr.open\n</sub>"
    )
    assert expected in notif.text


def test_digest_notification_structure_and_provenance():
    """A digest's trailer is the inverse shape: one topic as body content,
    every delivery id nested inside its own `<digest>` block -- not the same
    `<sub delivery="...">` attribute a single notification uses."""
    entries = [m for m in MANIFEST if m["event"] == "issues"]
    assert entries
    events = [notify.parse_event(e["event"], _load(e), e["delivery_id"]) for e in entries]

    result = notify.digest("danbarua/agent-bus:issue", events)

    assert result.summary
    assert result.body
    assert isinstance(result.provenance, notify.DigestProvenance)
    assert result.provenance.topic == "danbarua/agent-bus:issue"
    assert result.provenance.delivery_ids == tuple(e["delivery_id"] for e in entries)
    assert result.text == f"{result.body}\n\n{result.provenance.trailer()}"

    deliveries = "\n".join(e["delivery_id"] for e in entries)
    expected = f"<sub>\ndanbarua/agent-bus:issue\n<digest>\n{deliveries}\n</digest>\n</sub>"
    assert expected in result.text
    assert 'delivery="' not in result.text, "a digest has no single delivery to attribute"


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


def test_an_issue_notification_names_its_title():
    """Consistent with what `PullRequestEvent` already shipped: a title is
    already echoed in a PR's own summary today, so excluding it from an
    issue's summary was never a real distinction the code drew -- corrected
    after being raised as a false one."""
    entry = next(m for m in MANIFEST if m["event"] == "issues")
    payload = _load(entry)

    parsed = notify.parse_event("issues", payload, entry["delivery_id"])

    assert isinstance(parsed, notify.IssueEvent)
    assert parsed.title == payload["issue"]["title"]
    assert parsed.title in parsed.summary


def test_a_check_run_in_progress_produces_no_notification():
    """`topics.py` only emits a topic for `action: completed` -- `created`/
    `in_progress` are intermediate states nobody subscribed for. Parsing
    still works (never crash on a real payload), it just never reaches
    `notification()` in the bridge's own fan-out because nothing matches."""
    from agent_bridge.topics import topics_for

    entry = next(m for m in MANIFEST if m["event"] == "check_run" and m["action"] == "created")
    payload = _load(entry)

    assert topics_for("check_run", payload) == set()
    parsed = notify.parse_event("check_run", payload, entry["delivery_id"])
    assert isinstance(parsed, notify.CheckRunEvent)


def test_a_completed_check_run_names_its_pr_and_conclusion():
    from agent_bridge.topics import topics_for

    entry = next(m for m in MANIFEST if m["event"] == "check_run" and m["action"] == "completed")
    payload = _load(entry)
    pr_number = payload["check_run"]["pull_requests"][0]["number"]

    matched = topics_for("check_run", payload)
    assert matched == {"danbarua/agent-bus:pr", f"danbarua/agent-bus:pr/{pr_number}"}

    parsed = notify.parse_event("check_run", payload, entry["delivery_id"])
    notif = notify.notification(matched, parsed)

    assert f"#{pr_number}" not in notif.summary  # summary carries the path, not a bare number
    assert f"pull/{pr_number}" in notif.summary
    assert payload["check_run"]["conclusion"] in notif.summary
    assert f"pull request: #{pr_number}" in notif.body
    assert f"gh pr checks {pr_number} -R danbarua/agent-bus" in notif.body


def test_a_failed_check_run_says_failure_not_success():
    """No real captured failure exists yet -- the only completed check_runs
    in the fixture set both concluded `success`. Hand-built so a red build is
    at least proven to render correctly once one is captured for real."""
    entry = next(m for m in MANIFEST if m["event"] == "check_run" and m["action"] == "completed")
    payload = _load(entry)
    payload["check_run"]["conclusion"] = "failure"

    parsed = notify.parse_event("check_run", payload, entry["delivery_id"])

    assert isinstance(parsed, notify.CheckRunEvent)
    assert parsed.conclusion == "failure"
    assert "failure" in parsed.render_body()
    assert "success" not in parsed.render_body()
