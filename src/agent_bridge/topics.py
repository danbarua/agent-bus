"""What a GitHub event is *about*, as a set of `Topic`s.

The whole of the bridge's understanding of GitHub lives here, and it is one
pure function: an event goes in, the topics it matches come out. Filtering is
then set membership against what agents have subscribed to, which costs the
same whether one agent is subscribed or fifty.

**Local, deliberately.** #59 puts filtering here rather than in the cloud and
names the cost: every event for the repo crosses the network and most are
discarded. Bought with it -- these rules change without a deploy, and the
ingress stays a dumb door with one job that must be there.

## The grammar

    owner/repo/pulls                 every PR event this grammar recognizes
    owner/repo/pulls:opened
    owner/repo/pulls:merged
    owner/repo/pulls:merged:<branch> merged into that branch
    owner/repo/pulls:closed          closed without merging
    owner/repo/pulls:comment         PR conversation (issue_comment on a PR)
    owner/repo/pulls:synchronized    PR branch updated -- re-review signal
    owner/repo/pull/<n>              one PR thread, same coverage as bare
    owner/repo/pull/<n>:<subfilter>  same subfilters as bare, narrowed to one PR
    owner/repo/issues                every issue event (opened, edited, commented, sub-issue linked)
    owner/repo/issues/<n>            one issue thread, same scope, no subfilters

`owner/repo/pulls` matches GitHub's own list-page URL; `owner/repo/pull/<n>`
(singular) matches GitHub's own PR permalink. Issues stay plural both ways,
matching GitHub's own issue URLs -- and carry no subfilters, since no
granular issue selector has ever existed here.

A subfilter narrows down; it is never required for complete coverage.
`check_run` (only `action: completed`) and `synchronize` both feed the bare
`pulls`/`pull/<n>` topics the same as `opened`/`closed`/`merged` -- a
subscriber to bare `pulls` gets every PR event this grammar recognizes
without needing to know any subfilter exists.

**`:` and not `#`.** `owner/repo#pulls:merged` collides with GitHub's own
autolink wherever a topic appears in an issue, a PR comment, or a chat log --
these strings are echoed back to a subscriber and read in prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

Kind: TypeAlias = Literal["pulls", "issues"]
Subfilter: TypeAlias = Literal["opened", "closed", "comment", "synchronized", "merged"]

_SUBFILTERS: frozenset[str] = frozenset(
    ("opened", "closed", "comment", "synchronized", "merged")
)

_SLUG = r"[\w.-]+"
_PULLS_BARE = re.compile(rf"^({_SLUG})/({_SLUG})/pulls$")
_PULL_NUM = re.compile(rf"^({_SLUG})/({_SLUG})/pull/(\d+)$")
_ISSUES_BARE = re.compile(rf"^({_SLUG})/({_SLUG})/issues$")
_ISSUES_NUM = re.compile(rf"^({_SLUG})/({_SLUG})/issues/(\d+)$")


@dataclass(frozen=True)
class Topic:
    """A subscription topic. `parse`/`__str__` are the only two places a raw
    string and a `Topic` convert between each other -- every other consumer
    works with the fields directly."""
    owner: str
    repo: str
    kind: Kind
    number: int | None = None
    subfilter: Subfilter | None = None
    branch: str | None = None

    def __str__(self) -> str:
        if self.kind == "pulls":
            path = (f"{self.owner}/{self.repo}/pull/{self.number}"
                    if self.number is not None else f"{self.owner}/{self.repo}/pulls")
        else:
            path = (f"{self.owner}/{self.repo}/issues/{self.number}"
                    if self.number is not None else f"{self.owner}/{self.repo}/issues")
        if self.subfilter is None:
            return path
        if self.subfilter == "merged" and self.branch:
            return f"{path}:merged:{self.branch}"
        return f"{path}:{self.subfilter}"

    @classmethod
    def parse(cls, raw: str) -> Topic | None:
        path, sep, rest = (raw or "").strip().partition(":")

        subfilter: Subfilter | None = None
        branch: str | None = None
        if sep:
            parts = rest.split(":")
            head = parts[0]
            if head not in _SUBFILTERS:
                return None
            subfilter = head  # type: ignore[assignment]
            if subfilter == "merged":
                if len(parts) == 2 and parts[1]:
                    branch = parts[1]
                elif len(parts) > 2 or (len(parts) == 2 and not parts[1]):
                    return None
            elif len(parts) != 1:
                return None

        if m := _PULLS_BARE.match(path):
            owner, repo = m.groups()
            return cls(owner, repo, "pulls", None, subfilter, branch)
        if m := _PULL_NUM.match(path):
            owner, repo, number = m.groups()
            return cls(owner, repo, "pulls", int(number), subfilter, branch)
        if subfilter is not None:
            return None
        if m := _ISSUES_BARE.match(path):
            owner, repo = m.groups()
            return cls(owner, repo, "issues")
        if m := _ISSUES_NUM.match(path):
            owner, repo, number = m.groups()
            return cls(owner, repo, "issues", int(number))
        return None


def _repo(payload: dict[str, Any]) -> str:
    return ((payload.get("repository") or {}).get("full_name") or "").strip()


def _pulls_topics(owner_repo: str, number: int | None, subfilter: Subfilter,
                  branch: str = "") -> set[Topic]:
    """Bare `pulls`, the subfilter with no branch, and -- when a branch is
    known -- the branch-specific subfilter too. A subscriber to `:merged`
    (any branch) and one to `:merged:main` (that branch only) both need to
    be woken by the same merge; the event carries one branch, not a choice
    between the two topics."""
    owner, _, repo = owner_repo.partition("/")
    out = {Topic(owner, repo, "pulls"), Topic(owner, repo, "pulls", None, subfilter)}
    if branch:
        out.add(Topic(owner, repo, "pulls", None, subfilter, branch))
    if number is not None:
        out.add(Topic(owner, repo, "pulls", number))
        out.add(Topic(owner, repo, "pulls", number, subfilter))
        if branch:
            out.add(Topic(owner, repo, "pulls", number, subfilter, branch))
    return out


def _issues_topics(owner_repo: str, number: int | None) -> set[Topic]:
    owner, _, repo = owner_repo.partition("/")
    out = {Topic(owner, repo, "issues")}
    if number is not None:
        out.add(Topic(owner, repo, "issues", number))
    return out


def topics_for(event: str, payload: dict[str, Any]) -> set[Topic]:
    """Every topic this delivery matches. Empty when nothing does.

    Empty is the common case and not a failure -- #59 accepts that most of the
    firehose is discarded here, and a caller that logged every miss would be
    logging the design.
    """
    repo = _repo(payload)
    if not repo:
        return set()
    out: set[Topic] = set()

    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        action = payload.get("action")
        number = pr.get("number")
        if action == "opened":
            out |= _pulls_topics(repo, number, "opened")
        elif action == "synchronize":
            out |= _pulls_topics(repo, number, "synchronized")
        elif action == "closed":
            if pr.get("merged"):
                base = ((pr.get("base") or {}).get("ref") or "").strip()
                out |= _pulls_topics(repo, number, "merged", base)
            else:
                out |= _pulls_topics(repo, number, "closed")

    elif event == "issue_comment":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        if issue.get("pull_request") is not None:
            out |= _pulls_topics(repo, number, "comment")
        elif number is not None:
            out |= _issues_topics(repo, number)

    elif event == "issues":
        out |= _issues_topics(repo, (payload.get("issue") or {}).get("number"))

    elif event == "sub_issues":
        for key in ("sub_issue", "parent_issue"):
            number = (payload.get(key) or {}).get("number")
            if number is not None:
                out |= _issues_topics(repo, number)

    elif event == "check_run":
        if payload.get("action") == "completed":
            for pr in (payload.get("check_run") or {}).get("pull_requests") or []:
                number = pr.get("number")
                if number is not None:
                    owner, _, name = repo.partition("/")
                    out |= {Topic(owner, name, "pulls"), Topic(owner, name, "pulls", number)}

    return out
