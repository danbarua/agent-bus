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

from agent_bridge.topics import Topic, topics_for

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "cloud", "tests", "fixtures", "github_webhooks")

with open(os.path.join(FIXTURES, "MANIFEST.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


def _load(entry):
    with open(os.path.join(FIXTURES, entry["file"]), encoding="utf-8") as f:
        return json.load(f)


def _owner_name(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    return owner, name


@pytest.mark.parametrize("entry", MANIFEST, ids=[m["file"] for m in MANIFEST])
def test_every_real_delivery_is_matched_without_raising(entry):
    """A real payload from a repository we do not control must never take the
    bridge down -- this is the local, filtering half of the ingress's own
    promise, applied to the same untrusted shape."""
    topics_for(entry["event"], _load(entry))


def test_a_real_merge_wakes_the_repo_and_the_pr():
    merges = [m for m in MANIFEST if m["event"] == "pull_request" and m["action"] == "closed"]
    assert merges, "no real merge event was captured"
    for entry in merges:
        payload = _load(entry)
        owner, name = _owner_name(entry["repo"])
        number = payload["pull_request"]["number"]
        assert {Topic(owner, name, "pulls"), Topic(owner, name, "pulls", number)} <= topics_for(
            "pull_request", payload), entry["file"]


@pytest.mark.parametrize("action", ["labeled", "unlabeled"])
def test_a_real_label_change_by_the_ci_job_wakes_only_that_label(action):
    """Applied and removed by labkit's `label-areas` job, as `github-actions[bot]`."""
    entry = next(m for m in MANIFEST if m["event"] == "pull_request" and m["action"] == action)
    payload = _load(entry)
    owner, name = _owner_name(entry["repo"])
    assert payload["sender"]["login"] == "github-actions[bot]"
    assert topics_for("pull_request", payload) == {
        Topic(owner, name, "labels", label=payload["label"]["name"])}


def test_a_real_comment_on_an_issue_produces_its_thread_topic():
    entry = next(m for m in MANIFEST if m["event"] == "issue_comment")
    payload = _load(entry)
    topics = topics_for("issue_comment", payload)
    owner, name = _owner_name(entry["repo"])
    assert topics == {
        Topic(owner, name, "issues"),
        Topic(owner, name, "issues", payload["issue"]["number"]),
    }


def test_push_matches_nothing_yet():
    """Not a bug -- #59's design scopes the topic grammar to pull_request,
    issue_comment, issues, sub_issues, check_run and check_suite; #67 leaves the rest
    open."""
    for entry in MANIFEST:
        if entry["event"] != "push":
            continue
        assert topics_for(entry["event"], _load(entry)) == set(), entry["file"]


def test_a_completed_check_suite_wakes_the_repo_wide_pr_subscriber():
    """A CI result is useless without knowing which PR it belongs to, and a
    subscriber who only hears open/merge/comment still has to poll CI status
    by hand -- the exact redundant work this exists to remove. The suite
    carries the result once every run in it has finished (#350)."""
    entries = [m for m in MANIFEST if m["event"] == "check_suite"
               and _load(m)["check_suite"]["pull_requests"]
               and _load(m)["check_suite"]["conclusion"] == "success"]
    assert entries, "need at least one real check_suite delivery linked to a PR"
    for entry in entries:
        payload = _load(entry)
        owner, name = _owner_name(entry["repo"])
        number = payload["check_suite"]["pull_requests"][0]["number"]
        assert topics_for("check_suite", payload) == {
            Topic(owner, name, "pulls"), Topic(owner, name, "pulls", number)
        }, entry["file"]


def test_a_real_check_run_that_failed_wakes_the_repo_wide_pr_subscriber():
    """No real failed check_run is captured yet; a real passing one with its
    conclusion flipped keeps the rest of the shape real."""
    entries = [m for m in MANIFEST if m["event"] == "check_run" and m["action"] == "completed"]
    assert entries, "need at least one real completed check_run delivery"
    for entry in entries:
        payload = _load(entry)
        payload["check_run"]["conclusion"] = "failure"
        owner, name = _owner_name(entry["repo"])
        number = payload["check_run"]["pull_requests"][0]["number"]
        assert topics_for("check_run", payload) == {
            Topic(owner, name, "pulls"), Topic(owner, name, "pulls", number)
        }, entry["file"]


def test_a_check_run_still_in_progress_matches_nothing():
    """Only the terminal state matters -- `queued`/`in_progress` are a
    running commentary nobody subscribed for."""
    entries = [m for m in MANIFEST if m["event"] == "check_run" and m["action"] != "completed"]
    assert entries, "need at least one real non-completed check_run delivery"
    for entry in entries:
        assert topics_for("check_run", _load(entry)) == set(), entry["file"]


def test_a_real_issue_opening_wakes_the_repo_wide_subscriber():
    entry = next(m for m in MANIFEST if m["event"] == "issues" and m["action"] == "opened")
    payload = _load(entry)
    topics = topics_for("issues", payload)
    owner, name = _owner_name(entry["repo"])
    assert topics == {
        Topic(owner, name, "issues"),
        Topic(owner, name, "issues", payload["issue"]["number"]),
    }


def test_a_real_sub_issue_link_wakes_both_threads():
    """GitHub sends *two* deliveries for one linking action --
    `sub_issue_added` on the parent's own delivery, `parent_issue_added` on
    the child's -- but each payload already carries both issue numbers
    regardless of which one fired. A subscriber to either thread should hear
    about the link either way, from either delivery."""
    sub_issues = [m for m in MANIFEST if m["event"] == "sub_issues"]
    assert len(sub_issues) >= 2, "need both halves of a real linking action captured"
    for entry in sub_issues:
        payload = _load(entry)
        topics = topics_for("sub_issues", payload)
        owner, name = _owner_name(entry["repo"])
        assert topics == {
            Topic(owner, name, "issues"),
            Topic(owner, name, "issues", payload["sub_issue"]["number"]),
            Topic(owner, name, "issues", payload["parent_issue"]["number"]),
        }, (entry["file"], topics)
