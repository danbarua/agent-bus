"""Which topics an event matches -- the whole of the bridge's GitHub knowledge.

One pure function, so these are the cases that decide behaviour rather than
plumbing. #59 puts this local rather than in the cloud, which means the rules
change without a deploy and this file is where a change is argued.
"""

from __future__ import annotations

import pytest

from agent_bridge.topics import topics_for, valid

REPO = "danbarua/agent-bus"


def pr_event(action, *, merged=False, base="main"):
    return {"action": action, "repository": {"full_name": REPO},
            "pull_request": {"number": 181, "merged": merged, "base": {"ref": base}}}


def test_a_merge_matches_the_repo_the_event_and_the_target_branch():
    """`pr.merge.main` was the highest-value topic in the review on #67: it
    invalidates a checkout, the base of any open branch, and any review given
    on code that just moved."""
    assert topics_for("pull_request", pr_event("closed", merged=True)) == {
        f"{REPO}:pr", f"{REPO}:pr.merge", f"{REPO}:pr.merge.main"}


def test_the_branch_segment_is_the_target_not_the_source():
    """Named rather than inferred, which is what #67 asked for. A source
    branch exists for six hours; subscribing to one is subscribing to
    something already gone."""
    event = pr_event("closed", merged=True, base="release/2.0")
    event["pull_request"]["head"] = {"ref": "fix/some-ephemeral-branch"}
    topics = topics_for("pull_request", event)
    assert f"{REPO}:pr.merge.release/2.0" in topics, (
        "a branch name carries `/`, and the grammar has to accept what the "
        "matcher produces")
    assert valid(f"{REPO}:pr.merge.release/2.0")
    assert not any("ephemeral" in t for t in topics), "the source branch is not a topic"


def test_closed_without_merging_is_not_a_merge():
    """Different facts. A subscriber to `pr.close` wants the abandoned ones,
    and one to `pr.merge` must not be woken by them."""
    topics = topics_for("pull_request", pr_event("closed", merged=False))
    assert topics == {f"{REPO}:pr", f"{REPO}:pr.close"}
    assert f"{REPO}:pr.merge" not in topics


def test_a_comment_on_a_pull_request_is_pr_conversation():
    """GitHub sends `issue_comment` for pull requests too and tells them apart
    only by this key. A `pr` subscriber that missed it would be missing the
    common case -- it is how review conversation arrives."""
    topics = topics_for("issue_comment", {
        "action": "created", "repository": {"full_name": REPO},
        "issue": {"number": 181, "pull_request": {"url": "..."}}})
    assert topics == {f"{REPO}:pr", f"{REPO}:pr.comment"}
    assert f"{REPO}:issue/181" not in topics


def test_a_comment_on_a_real_issue_is_that_thread():
    topics = topics_for("issue_comment", {
        "action": "created", "repository": {"full_name": REPO},
        "issue": {"number": 242}})
    assert topics == {f"{REPO}:issue/242"}


@pytest.mark.parametrize("event,payload", [
    ("pull_request", pr_event("synchronize")),
    ("push", {"repository": {"full_name": REPO}}),
    ("pull_request", {"action": "closed", "pull_request": {"merged": True}}),
])
def test_an_event_nothing_subscribes_to_matches_nothing(event, payload):
    """Empty is the common case, not a failure. #59 accepts that most of the
    firehose is discarded here; a caller logging every miss would be logging
    the design. The third case has no repository, which is the only one that
    is genuinely malformed -- and it is still silence rather than a raise."""
    assert topics_for(event, payload) == set()


@pytest.mark.parametrize("topic", [
    f"{REPO}:pr", f"{REPO}:pr.merge", f"{REPO}:pr.merge.main", f"{REPO}:issue/242"])
def test_the_grammar_accepts_what_the_matcher_produces(topic):
    """The two halves have to agree: a topic an event can match must be one an
    agent is allowed to subscribe to."""
    assert valid(topic)


@pytest.mark.parametrize("topic", [
    "", "danbarua/agent-bus", "pr.merge", f"{REPO}#pr.merge",
    f"{REPO}:pr merge", f"{REPO}:pr.merge extra"])
def test_the_grammar_refuses_what_is_not_a_topic(topic):
    """`#` in particular: `owner/repo#pr.merge` collides with GitHub's autolink
    wherever a topic appears in an issue or commit message, and these strings
    are echoed back into chat logs."""
    assert not valid(topic)
