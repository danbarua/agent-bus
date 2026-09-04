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
