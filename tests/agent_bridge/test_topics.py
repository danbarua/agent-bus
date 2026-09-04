"""Which topics an event matches -- the whole of the bridge's GitHub knowledge.

One pure function, so these are the cases that decide behaviour rather than
plumbing. #59 puts this local rather than in the cloud, which means the rules
change without a deploy and this file is where a change is argued.
"""

from __future__ import annotations

import pytest

from agent_bridge.topics import Topic, topics_for

REPO = "danbarua/agent-bus"
OWNER, NAME = REPO.split("/")


def pr_event(action, *, merged=False, base="main"):
    return {"action": action, "repository": {"full_name": REPO},
            "pull_request": {"number": 181, "merged": merged, "base": {"ref": base}}}


def test_an_opened_pr_wakes_the_bare_and_numbered_topics():
    assert topics_for("pull_request", pr_event("opened")) == {
        Topic(OWNER, NAME, "pulls"),
        Topic(OWNER, NAME, "pulls", 181),
        Topic(OWNER, NAME, "pulls", subfilter="opened"),
        Topic(OWNER, NAME, "pulls", 181, "opened"),
    }


def test_a_merge_names_the_target_branch():
    """The merge target is what changes an agent's next action -- named here
    rather than left to be inferred. A merge always carries both `:merged`
    (any branch) and `:merged:<branch>` (this one) -- a subscriber to either
    form has to be woken by the same event."""
    topics = topics_for("pull_request", pr_event("closed", merged=True))
    assert topics == {
        Topic(OWNER, NAME, "pulls"),
        Topic(OWNER, NAME, "pulls", 181),
        Topic(OWNER, NAME, "pulls", subfilter="merged"),
        Topic(OWNER, NAME, "pulls", subfilter="merged", branch="main"),
        Topic(OWNER, NAME, "pulls", 181, "merged"),
        Topic(OWNER, NAME, "pulls", 181, "merged", "main"),
    }


def test_a_branch_name_carrying_a_slash_is_a_valid_target():
    event = pr_event("closed", merged=True, base="release/2.0")
    event["pull_request"]["head"] = {"ref": "fix/some-ephemeral-branch"}
    topics = topics_for("pull_request", event)
    expected = Topic(OWNER, NAME, "pulls", subfilter="merged", branch="release/2.0")
    assert expected in topics
    assert Topic.parse(str(expected)) == expected
    assert not any(t.branch and "ephemeral" in t.branch for t in topics)


def test_closed_without_merging_still_wakes_bare_pulls():
    """A bare `pulls` subscriber needs every PR event, including an
    abandoned close -- narrowing to only merges is what `:merged` is for."""
    topics = topics_for("pull_request", pr_event("closed", merged=False))
    assert Topic(OWNER, NAME, "pulls") in topics
    assert Topic(OWNER, NAME, "pulls", subfilter="closed") in topics
    assert not any(t.subfilter == "merged" for t in topics)


def test_a_synchronize_wakes_bare_pulls_too():
    """Not a separate opt-in topic: a bare `pulls` subscriber gets the
    re-review signal without needing to know `:synchronized` exists."""
    topics = topics_for("pull_request", pr_event("synchronize"))
    assert Topic(OWNER, NAME, "pulls") in topics
    assert Topic(OWNER, NAME, "pulls", 181) in topics
    assert Topic(OWNER, NAME, "pulls", subfilter="synchronized") in topics
    assert Topic(OWNER, NAME, "pulls", 181, "synchronized") in topics


def test_a_comment_on_a_pull_request_is_pr_conversation():
    """GitHub sends `issue_comment` for pull requests too and tells them apart
    only by this key. A `pulls` subscriber that missed it would be missing
    the common case -- it is how review conversation arrives."""
    topics = topics_for("issue_comment", {
        "action": "created", "repository": {"full_name": REPO},
        "issue": {"number": 181, "pull_request": {"url": "..."}}})
    assert topics == {
        Topic(OWNER, NAME, "pulls"),
        Topic(OWNER, NAME, "pulls", 181),
        Topic(OWNER, NAME, "pulls", subfilter="comment"),
        Topic(OWNER, NAME, "pulls", 181, "comment"),
    }
    assert not any(t.kind == "issues" for t in topics), "a PR thread is never issues/<n>"


def test_a_comment_on_a_real_issue_is_that_thread():
    topics = topics_for("issue_comment", {
        "action": "created", "repository": {"full_name": REPO},
        "issue": {"number": 242}})
    assert topics == {Topic(OWNER, NAME, "issues"), Topic(OWNER, NAME, "issues", 242)}


def test_a_completed_check_run_wakes_the_linked_pr():
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_run": {"pull_requests": [{"number": 181}]}}
    assert topics_for("check_run", payload) == {
        Topic(OWNER, NAME, "pulls"), Topic(OWNER, NAME, "pulls", 181)}


def test_a_check_run_still_running_matches_nothing():
    payload = {"action": "in_progress", "repository": {"full_name": REPO},
               "check_run": {"pull_requests": [{"number": 181}]}}
    assert topics_for("check_run", payload) == set()


@pytest.mark.parametrize("event,payload", [
    ("push", {"repository": {"full_name": REPO}}),
    ("pull_request", {"action": "closed", "pull_request": {"merged": True}}),
])
def test_an_event_nothing_subscribes_to_matches_nothing(event, payload):
    """Empty is the common case, not a failure. The second case has no
    repository, which is still silence rather than a raise."""
    assert topics_for(event, payload) == set()


@pytest.mark.parametrize("raw,expected", [
    (f"{REPO}/pulls", Topic(OWNER, NAME, "pulls")),
    (f"{REPO}/pulls:merged", Topic(OWNER, NAME, "pulls", subfilter="merged")),
    (f"{REPO}/pulls:merged:main",
     Topic(OWNER, NAME, "pulls", subfilter="merged", branch="main")),
    (f"{REPO}/pull/181", Topic(OWNER, NAME, "pulls", 181)),
    (f"{REPO}/pull/181:closed", Topic(OWNER, NAME, "pulls", 181, "closed")),
    (f"{REPO}/issues", Topic(OWNER, NAME, "issues")),
    (f"{REPO}/issues/242", Topic(OWNER, NAME, "issues", 242)),
])
def test_a_valid_string_parses_to_the_expected_topic(raw, expected):
    assert Topic.parse(raw) == expected


@pytest.mark.parametrize("raw", [
    f"{REPO}/pulls", f"{REPO}/pulls:merged", f"{REPO}/pulls:merged:main",
    f"{REPO}/pull/181", f"{REPO}/pull/181:closed", f"{REPO}/issues", f"{REPO}/issues/242",
])
def test_a_valid_string_round_trips_through_str(raw):
    assert str(Topic.parse(raw)) == raw


@pytest.mark.parametrize("raw", [
    "", "danbarua/agent-bus", f"{REPO}:pr", f"{REPO}:pr.merge.main",
    f"{REPO}#pulls", f"{REPO}/pulls#merged", f"{REPO}/issue/242",
    f"{REPO}/issues:opened", f"{REPO}/pulls:bogus", f"{REPO}/pull/abc",
    f"{REPO}/pulls:merged:", f"{REPO}/pulls:merged:main:extra",
])
def test_an_invalid_string_does_not_parse(raw):
    """`#` in particular: `owner/repo#pulls:merged` collides with GitHub's
    autolink wherever a topic appears in an issue or commit message, and
    these strings are echoed back into chat logs."""
    assert Topic.parse(raw) is None
