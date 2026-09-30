"""What a GitHub event is *about*, as a set of `Topic`s.

The whole of the bridge's understanding of GitHub lives here, and it is one
pure function: an event goes in, the topics it matches come out. Filtering is
then set membership against what agents have subscribed to, which costs the
same whether one agent is subscribed or fifty.

**Local, deliberately.** #59 puts filtering here rather than in the cloud and
names the cost: every event for the repo crosses the network and most are
discarded. Bought with it -- these rules change without a deploy, and the
ingress stays a dumb door with one job that must be there.

**The grammar is `topics.txt`, next to this file**: every accepted form with
what it delivers, and the forms that are refused. The tests parse every line
of it and the bridge quotes it when it refuses a topic, so it cannot drift
from `Topic.parse` below.

A label topic matches a pull request or an issue carrying that label, and the
`labeled`/`unlabeled` delivery that adds or removes it -- the one moment an
item enters or leaves an area. A CI result names its pull request but not the
pull request's labels, so `topics_for` takes a lookup for those; the bridge
supplies it from the `pull_request` deliveries it has already seen.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Literal, TypeAlias

Kind: TypeAlias = Literal["pulls", "issues", "labels"]

#: The labels a pull request carries, by repository (`owner/repo`) and number.
PrLabels: TypeAlias = Callable[[str, int], Iterable[str]]

_SLUG = r"[\w.-]+"
_PULLS_BARE = re.compile(rf"^({_SLUG})/({_SLUG})/pulls$")
_PULL_NUM = re.compile(rf"^({_SLUG})/({_SLUG})/pull/(\d+)$")
_ISSUES_BARE = re.compile(rf"^({_SLUG})/({_SLUG})/issues$")
_ISSUES_NUM = re.compile(rf"^({_SLUG})/({_SLUG})/issues/(\d+)$")
_LABEL = re.compile(rf"^({_SLUG})/({_SLUG})/labels/(.*\S.*)$")


@dataclass(frozen=True)
class Topic:
    """A subscription topic. `parse`/`__str__` are the only two places a raw
    string and a `Topic` convert between each other -- every other consumer
    works with the fields directly."""
    owner: str
    repo: str
    kind: Kind
    number: int | None = None
    label: str | None = None

    def __str__(self) -> str:
        base = f"{self.owner}/{self.repo}"
        if self.kind == "labels":
            return f"{base}/labels/{self.label}"
        if self.kind == "pulls":
            return f"{base}/pull/{self.number}" if self.number is not None else f"{base}/pulls"
        return f"{base}/issues/{self.number}" if self.number is not None else f"{base}/issues"

    @classmethod
    def parse(cls, raw: str) -> Topic | None:
        text = (raw or "").strip()
        if m := _PULLS_BARE.match(text):
            return cls(m[1], m[2], "pulls")
        if m := _PULL_NUM.match(text):
            return cls(m[1], m[2], "pulls", int(m[3]))
        if m := _ISSUES_BARE.match(text):
            return cls(m[1], m[2], "issues")
        if m := _ISSUES_NUM.match(text):
            return cls(m[1], m[2], "issues", int(m[3]))
        if m := _LABEL.match(text):
            return cls(m[1], m[2], "labels", label=m[3].strip())
        return None


def examples() -> list[tuple[bool, str, str]]:
    """`topics.txt` as `(accepted, topic, what it delivers)`, in file order."""
    out = []
    for line in files(__package__).joinpath("topics.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        refused = line.startswith("!")
        topic, _, meaning = line.removeprefix("!").strip().partition("  ")
        out.append((not refused, topic.strip(), meaning.strip()))
    return out


def _repo(payload: dict[str, Any]) -> str:
    return ((payload.get("repository") or {}).get("full_name") or "").strip()


def _names(labels: Any) -> list[str]:
    return [lb["name"] for lb in labels or [] if isinstance(lb, dict) and lb.get("name")]


def _pulls_topics(owner_repo: str, number: int | None) -> set[Topic]:
    owner, _, repo = owner_repo.partition("/")
    out = {Topic(owner, repo, "pulls")}
    if number is not None:
        out.add(Topic(owner, repo, "pulls", number))
    return out


def _issues_topics(owner_repo: str, number: int | None) -> set[Topic]:
    owner, _, repo = owner_repo.partition("/")
    out = {Topic(owner, repo, "issues")}
    if number is not None:
        out.add(Topic(owner, repo, "issues", number))
    return out


def _label_topics(owner_repo: str, labels: Iterable[str]) -> set[Topic]:
    owner, _, repo = owner_repo.partition("/")
    return {Topic(owner, repo, "labels", label=name) for name in labels}


def _changed_label(payload: dict[str, Any]) -> list[str]:
    """The one label a `labeled`/`unlabeled` delivery added or removed."""
    return _names([payload.get("label")])


# #314: neutral/skipped/stale are not a pass or a fail -- they answer nothing
# a subscriber would poll for (a trigger that only runs for some changes
# reporting "skipped" on every other PR is 100% predictable noise, not a
# result). Every other terminal conclusion is a real result.
_NOT_A_RESULT = frozenset({"neutral", "skipped", "stale"})

# #350: a check run that passed, or was cancelled, wakes nobody. Its check
# suite's own `completed` delivery reports the outcome once every run in the
# suite has finished, so a five-shard build is one notification, not five. A
# cancelled run is almost always a fail-fast matrix stopping the other shards
# after one failed, and that failure was already delivered on its own. The
# conclusions left -- failure, timed_out, action_required -- still wake a
# subscriber the moment they arrive, without waiting for the suite.
_CHECK_RUN_SILENT = _NOT_A_RESULT | {"success", "cancelled"}


def _ci_topics(owner_repo: str, checked: dict[str, Any], pr_labels: PrLabels) -> set[Topic]:
    """The topics a completed `check_run` or `check_suite` wakes. Both objects
    carry the same `head_sha` and `pull_requests[]` fields, and neither carries
    labels -- those come from `pr_labels`.

    Superseded, not "stale" (that word already names GitHub's own conclusion
    value): a push can land on a PR before an in-flight check for the
    *previous* commit finishes, and that result arrives after the PR has
    already moved on. `pull_requests[].head.sha` is the PR's head as GitHub
    resolved it when building this event -- when it disagrees with the sha
    this check actually ran against, a fresh check for the current head is
    already running or about to be, so this one is not worth waking anyone
    for. Same payload GitHub already sends, no extra API call.

    Not verified: whether `pull_requests[].head.sha` itself can ever lag the
    PR's true current head (a push landing in the narrow window while GitHub
    is constructing this specific event) -- GitHub's own webhook docs make no
    freshness claim either way. If it can, this could over-suppress a
    genuinely current result rather than deliver a stale one. Every real case
    behind this rule so far has been a much wider window (a full CI run's
    length), not that narrow race, so it stays documented uncertainty rather
    than a live API call inside what is otherwise a pure, network-free
    function."""
    checked_sha = checked.get("head_sha")
    out: set[Topic] = set()
    for pr in checked.get("pull_requests") or []:
        number = pr.get("number")
        current_head = (pr.get("head") or {}).get("sha")
        if checked_sha and current_head and checked_sha != current_head:
            continue
        if number is not None:
            out |= _pulls_topics(owner_repo, number)
            out |= _label_topics(owner_repo, pr_labels(owner_repo, number))
    return out


def _no_labels(_owner_repo: str, _number: int) -> Iterable[str]:
    return ()


def topics_for(event: str, payload: dict[str, Any],
               pr_labels: PrLabels = _no_labels) -> set[Topic]:
    """Every topic this delivery matches. Empty when nothing does.

    Empty is the common case and not a failure -- #59 accepts that most of the
    firehose is discarded here, and a caller that logged every miss would be
    logging the design.

    `pr_labels` answers for a pull request whose labels the payload does not
    carry, which is every CI result. A `pull_request` delivery uses it too, so
    a bridge that has already seen a later `labeled` in the same poll labels
    the earlier `opened` with it.
    """
    repo = _repo(payload)
    if not repo:
        return set()
    out: set[Topic] = set()
    action = payload.get("action")

    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        number = pr.get("number")
        if action in ("labeled", "unlabeled"):
            out |= _label_topics(repo, _changed_label(payload))
        elif action in ("opened", "synchronize", "closed"):
            out |= _pulls_topics(repo, number)
            labels = pr_labels(repo, number) if number is not None else ()
            out |= _label_topics(repo, labels or _names(pr.get("labels")))

    elif event == "issue_comment":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        if issue.get("pull_request") is not None:
            out |= _pulls_topics(repo, number)
        elif number is not None:
            out |= _issues_topics(repo, number)
        out |= _label_topics(repo, _names(issue.get("labels")))

    elif event == "issues":
        issue = payload.get("issue") or {}
        out |= _issues_topics(repo, issue.get("number"))
        out |= _label_topics(repo, _changed_label(payload) if action in ("labeled", "unlabeled")
                             else _names(issue.get("labels")))

    elif event == "sub_issues":
        for key in ("sub_issue", "parent_issue"):
            linked = payload.get(key) or {}
            if linked.get("number") is not None:
                out |= _issues_topics(repo, linked["number"])
                out |= _label_topics(repo, _names(linked.get("labels")))

    elif event == "check_run":
        check_run = payload.get("check_run") or {}
        if action == "completed" and check_run.get("conclusion") not in _CHECK_RUN_SILENT:
            out |= _ci_topics(repo, check_run, pr_labels)

    elif event == "check_suite":
        check_suite = payload.get("check_suite") or {}
        if action == "completed" and check_suite.get("conclusion") not in _NOT_A_RESULT:
            out |= _ci_topics(repo, check_suite, pr_labels)

    return out
