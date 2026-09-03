"""`topics_for()` against real GitHub deliveries captured from staging.

Same fixtures `cloud/tests/test_webhook_fixtures.py` uses -- one canonical set
of real payloads, read across the package boundary the way
`test_cloud_client.py` already reaches into `cloud/` for integration coverage,
rather than a second copy that could drift from the first.

`test_topics.py` covers the grammar with hand-built payloads; this file exists
because a real GitHub event is not obligated to match the shape we imagined
when we wrote the matcher, and the honest way to find out is to run it against
what GitHub actually sent.
"""

from __future__ import annotations

import json
import os

import pytest

from agent_bridge.topics import topics_for

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "cloud", "tests", "fixtures", "github_webhooks")

with open(os.path.join(FIXTURES, "MANIFEST.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


def _load(entry):
    with open(os.path.join(FIXTURES, entry["file"]), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("entry", MANIFEST, ids=[m["file"] for m in MANIFEST])
def test_every_real_delivery_is_matched_without_raising(entry):
    """A real payload from a repository we do not control must never take the
    bridge down -- this is the local, filtering half of the ingress's own
    promise, applied to the same untrusted shape."""
    topics_for(entry["event"], _load(entry))


def test_a_real_merge_produces_the_branch_topic():
    """`pr.merge.main` was the highest-value topic in the #67 review: it
    invalidates a checkout, an open branch's base, and any review just made
    stale. This is that topic, produced from an actual GitHub delivery rather
    than a hand-built one."""
    merges = [m for m in MANIFEST if m["event"] == "pull_request" and m["action"] == "closed"]
    assert merges, "no real merge event was captured"
    for entry in merges:
        topics = topics_for("pull_request", _load(entry))
        assert f"{entry['repo']}:pr.merge.main" in topics, (entry["file"], topics)


def test_a_real_comment_on_an_issue_produces_its_thread_topic():
    entry = next(m for m in MANIFEST if m["event"] == "issue_comment")
    payload = _load(entry)
    topics = topics_for("issue_comment", payload)
    assert topics == {f"{entry['repo']}:issue/{payload['issue']['number']}"}


def test_check_run_and_push_match_nothing_yet():
    """Not a bug -- #59's design scopes the topic grammar to pull_request and
    issue_comment, and #67 leaves the rest open. Recorded here as a fact about
    real traffic rather than left to be discovered: on the day these fixtures
    were captured, check_run and push were 19 of 33 live deliveries (58%), and
    every one of them currently wakes nobody, however an agent subscribes.

    Worth naming because #59's own design doc argues `check.fail.main` --
    a check_run event -- is the single highest-value topic ("no natural
    watcher"), and it is not implemented."""
    for entry in MANIFEST:
        if entry["event"] not in ("check_run", "push"):
            continue
        assert topics_for(entry["event"], _load(entry)) == set(), entry["file"]


def test_a_real_issue_opening_wakes_the_repo_wide_subscriber():
    """#265: `owner/repo:issue` (bare) is the catch-all a subscriber uses to
    hear about any issue on the repo, the same way `owner/repo:pr` already
    covers any PR. An `issues` event never carried a topic before this."""
    entry = next(m for m in MANIFEST if m["event"] == "issues" and m["action"] == "opened")
    payload = _load(entry)
    topics = topics_for("issues", payload)
    assert topics == {
        f"{entry['repo']}:issue", f"{entry['repo']}:issue/{payload['issue']['number']}"
    }


def test_a_real_sub_issue_link_wakes_both_threads():
    """#265's second half. GitHub sends *two* deliveries for one linking
    action -- `sub_issue_added` on the parent's own delivery, `parent_issue_added`
    on the child's -- but each payload already carries both issue numbers
    regardless of which one fired. A subscriber to either thread should hear
    about the link either way, from either delivery.
    """
    sub_issues = [m for m in MANIFEST if m["event"] == "sub_issues"]
    assert len(sub_issues) >= 2, "need both halves of a real linking action captured"
    for entry in sub_issues:
        payload = _load(entry)
        topics = topics_for("sub_issues", payload)
        repo = entry["repo"]
        assert topics == {
            f"{repo}:issue",
            f"{repo}:issue/{payload['sub_issue']['number']}",
            f"{repo}:issue/{payload['parent_issue']['number']}",
        }, (entry["file"], topics)
