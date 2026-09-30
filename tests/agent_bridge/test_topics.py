"""Which topics an event matches -- the whole of the bridge's GitHub knowledge.

One pure function, so these are the cases that decide behaviour rather than
plumbing. #59 puts this local rather than in the cloud, which means the rules
change without a deploy and this file is where a change is argued.
"""

from __future__ import annotations

import pytest

from agent_bridge.topics import Topic, examples, topics_for

REPO = "danbarua/agent-bus"
OWNER, NAME = REPO.split("/")


def pr_event(action, *, merged=False, labels=(), label=None):
    event = {"action": action, "repository": {"full_name": REPO},
             "pull_request": {"number": 181, "merged": merged, "base": {"ref": "main"},
                              "labels": [{"name": n} for n in labels]}}
    if label:
        event["label"] = {"name": label}
    return event


PULL_181 = {Topic(OWNER, NAME, "pulls"), Topic(OWNER, NAME, "pulls", 181)}


@pytest.mark.parametrize("action,merged", [
    ("opened", False), ("synchronize", False), ("closed", False), ("closed", True),
])
def test_a_pr_event_wakes_the_bare_and_numbered_topics(action, merged):
    assert topics_for("pull_request", pr_event(action, merged=merged)) == PULL_181


def test_a_labelled_pr_also_wakes_each_of_its_labels():
    topics = topics_for("pull_request", pr_event("synchronize", labels=("area:web", "bug")))
    assert topics == PULL_181 | {Topic(OWNER, NAME, "labels", label="area:web"),
                                 Topic(OWNER, NAME, "labels", label="bug")}


def test_labelling_a_pr_wakes_only_that_label():
    """Adding a label is how a pull request enters an area, so it reaches
    that label's subscribers -- and nobody else, for whom it is noise."""
    event = pr_event("labeled", labels=("area:web", "area:agent"), label="area:agent")
    assert topics_for("pull_request", event) == {
        Topic(OWNER, NAME, "labels", label="area:agent")}


def test_unlabelling_a_pr_wakes_only_that_label():
    event = pr_event("unlabeled", labels=(), label="area:agent")
    assert topics_for("pull_request", event) == {
        Topic(OWNER, NAME, "labels", label="area:agent")}


def test_a_pr_event_takes_its_labels_from_the_lookup_when_it_answers():
    """The bridge remembers a later `labeled` in the same poll, so an `opened`
    delivered with no labels is matched with the ones it ended up with."""
    topics = topics_for("pull_request", pr_event("opened"), lambda repo, n: ("area:web",))
    assert Topic(OWNER, NAME, "labels", label="area:web") in topics


def test_a_comment_on_a_pull_request_is_pr_conversation():
    """GitHub sends `issue_comment` for pull requests too and tells them apart
    only by this key. A `pulls` subscriber that missed it would be missing
    the common case -- it is how review conversation arrives."""
    topics = topics_for("issue_comment", {
        "action": "created", "repository": {"full_name": REPO},
        "issue": {"number": 181, "pull_request": {"url": "..."},
                  "labels": [{"name": "area:web"}]}})
    assert topics == PULL_181 | {Topic(OWNER, NAME, "labels", label="area:web")}
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


@pytest.mark.parametrize("conclusion", ["neutral", "skipped", "stale"])
def test_a_completed_check_run_with_a_non_actionable_conclusion_matches_nothing(conclusion):
    """#314: a trigger that only runs for some changes reports `skipped` on
    every other PR, every time -- 100% predictable, zero-information noise,
    not a result anyone would poll for. `neutral` and `stale` are the same
    shape: neither a pass nor a fail."""
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_run": {"conclusion": conclusion, "pull_requests": [{"number": 181}]}}
    assert topics_for("check_run", payload) == set()


@pytest.mark.parametrize("conclusion", ["success", "cancelled"])
def test_a_check_run_that_passed_or_was_cancelled_matches_nothing(conclusion):
    """#350: a sharded build passing is one `check_suite` notification, not
    one per shard. A cancelled run is a fail-fast matrix stopping the other
    shards after the failure that was already delivered."""
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_run": {"conclusion": conclusion, "pull_requests": [{"number": 181}]}}
    assert topics_for("check_run", payload) == set()


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "action_required"])
def test_a_check_run_that_did_not_pass_wakes_the_pr_immediately(conclusion):
    """A red result is never held for the suite to finish."""
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_run": {"conclusion": conclusion, "pull_requests": [{"number": 181}]}}
    assert topics_for("check_run", payload) == {
        Topic(OWNER, NAME, "pulls"), Topic(OWNER, NAME, "pulls", 181)}


def test_a_completed_check_run_for_the_prs_current_head_wakes_it():
    """Baseline: the common case, where the check ran on exactly what the
    PR still points at, must keep working once superseded-push filtering
    is added below."""
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_run": {"conclusion": "failure", "head_sha": "abc123",
                             "pull_requests": [{"number": 181, "head": {"sha": "abc123"}}]}}
    assert topics_for("check_run", payload) == {
        Topic(OWNER, NAME, "pulls"), Topic(OWNER, NAME, "pulls", 181)}


def test_a_completed_check_run_for_a_superseded_commit_matches_nothing():
    """Live case, not hypothetical: a `pull_request synchronize` (new push)
    landed, then a `check_run` result arrived for the *previous* commit --
    real GitHub traffic on a real PR, same poll window. `head_sha` (what
    this run checked) disagrees with `pull_requests[0].head.sha` (what the
    PR points at now), so a fresh check for the current head is already
    running or about to be -- this one is not worth waking anyone for."""
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_run": {"conclusion": "failure", "head_sha": "1f06b459",
                             "pull_requests": [{"number": 341, "head": {"sha": "c067c50f"}}]}}
    assert topics_for("check_run", payload) == set()


def test_a_superseded_check_run_is_scoped_per_pr_not_globally():
    """Two PRs on one check_run delivery (rare, but the field is a list):
    one current, one superseded. Only the current one should wake -- this
    is not a single stale/not-stale flag for the whole event."""
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_run": {"conclusion": "failure", "head_sha": "abc123",
                             "pull_requests": [
                                 {"number": 181, "head": {"sha": "abc123"}},
                                 {"number": 182, "head": {"sha": "zzz999"}},
                             ]}}
    assert topics_for("check_run", payload) == {
        Topic(OWNER, NAME, "pulls"), Topic(OWNER, NAME, "pulls", 181)}


def test_a_check_run_missing_head_info_is_not_silently_suppressed():
    """Absence of the comparison data must never look like "superseded" --
    that would turn a payload shape this matcher does not fully understand
    into silent, unexplained noise-eating. Missing head_sha, or a PR entry
    with no head object at all, wakes the PR exactly as it always did."""
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_run": {"conclusion": "failure",
                             "pull_requests": [{"number": 181}]}}
    assert topics_for("check_run", payload) == {
        Topic(OWNER, NAME, "pulls"), Topic(OWNER, NAME, "pulls", 181)}


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", "timed_out"])
def test_a_completed_check_suite_wakes_the_linked_pr(conclusion):
    """#350: the suite is the one "CI finished" result, pass or fail -- a
    failed suite is still delivered after its failing run was, because it
    says no more results are coming."""
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_suite": {"conclusion": conclusion, "head_sha": "abc123",
                               "pull_requests": [{"number": 181, "head": {"sha": "abc123"}}]}}
    assert topics_for("check_suite", payload) == {
        Topic(OWNER, NAME, "pulls"), Topic(OWNER, NAME, "pulls", 181)}


@pytest.mark.parametrize("conclusion", ["neutral", "skipped", "stale"])
def test_a_check_suite_with_a_non_actionable_conclusion_matches_nothing(conclusion):
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_suite": {"conclusion": conclusion, "pull_requests": [{"number": 181}]}}
    assert topics_for("check_suite", payload) == set()


def test_a_check_suite_for_a_superseded_commit_matches_nothing():
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_suite": {"conclusion": "success", "head_sha": "1f06b459",
                               "pull_requests": [{"number": 341, "head": {"sha": "c067c50f"}}]}}
    assert topics_for("check_suite", payload) == set()


def test_a_check_suite_not_yet_completed_matches_nothing():
    payload = {"action": "requested", "repository": {"full_name": REPO},
               "check_suite": {"conclusion": None, "pull_requests": [{"number": 181}]}}
    assert topics_for("check_suite", payload) == set()


@pytest.mark.parametrize("event,payload", [
    ("push", {"repository": {"full_name": REPO}}),
    ("pull_request", {"action": "closed", "pull_request": {"merged": True}}),
])
def test_an_event_nothing_subscribes_to_matches_nothing(event, payload):
    """Empty is the common case, not a failure. The second case has no
    repository, which is still silence rather than a raise."""
    assert topics_for(event, payload) == set()


EXAMPLES = examples()


def test_topics_txt_has_accepted_and_refused_examples():
    assert any(ok for ok, _, _ in EXAMPLES) and any(not ok for ok, _, _ in EXAMPLES)
    assert all(meaning for _, _, meaning in EXAMPLES), "every line says what it delivers"


@pytest.mark.parametrize("topic", [t for ok, t, _ in EXAMPLES if ok])
def test_every_accepted_example_parses_and_round_trips(topic):
    assert str(Topic.parse(topic)) == topic


@pytest.mark.parametrize("topic", [t for ok, t, _ in EXAMPLES if not ok])
def test_every_refused_example_is_refused(topic):
    assert Topic.parse(topic) is None


def test_a_label_may_carry_a_colon_and_spaces():
    assert Topic.parse(f"{REPO}/labels/area:web") == Topic(OWNER, NAME, "labels", label="area:web")
    assert Topic.parse(f"{REPO}/labels/good first issue").label == "good first issue"


def test_an_issue_event_wakes_its_labels_and_labelling_wakes_only_the_new_one():
    issue = {"number": 242, "labels": [{"name": "area:record"}, {"name": "area:web"}]}
    edited = {"action": "edited", "repository": {"full_name": REPO}, "issue": issue}
    labeled = {**edited, "action": "labeled", "label": {"name": "area:web"}}
    issue_topics = {Topic(OWNER, NAME, "issues"), Topic(OWNER, NAME, "issues", 242)}

    assert topics_for("issues", edited) == issue_topics | {
        Topic(OWNER, NAME, "labels", label="area:record"),
        Topic(OWNER, NAME, "labels", label="area:web")}
    assert topics_for("issues", labeled) == issue_topics | {
        Topic(OWNER, NAME, "labels", label="area:web")}


def test_a_ci_result_takes_its_pr_labels_from_the_lookup():
    """A check suite names its pull request and nothing about it; the bridge
    answers for the labels."""
    payload = {"action": "completed", "repository": {"full_name": REPO},
               "check_suite": {"conclusion": "success", "head_sha": "abc123",
                               "pull_requests": [{"number": 181, "head": {"sha": "abc123"}}]}}
    asked = []

    def lookup(repo, number):
        asked.append((repo, number))
        return ("area:agent",)

    assert topics_for("check_suite", payload, lookup) == PULL_181 | {
        Topic(OWNER, NAME, "labels", label="area:agent")}
    assert asked == [(REPO, 181)]
