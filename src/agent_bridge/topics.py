"""What a GitHub event is *about*, as a set of topic strings.

The whole of the bridge's understanding of GitHub lives here, and it is one
pure function: an event goes in, the topics it matches come out. Filtering is
then set membership against what agents have subscribed to, which costs the
same whether one agent is subscribed or fifty.

**Local, deliberately.** #59 puts filtering here rather than in the cloud and
names the cost: every event for the repo crosses the network and most are
discarded. Bought with it -- these rules change without a deploy, and the
ingress stays a dumb door with one job that must be there.

## The grammar

    owner/repo:pr                 opened, commented, merged, closed
    owner/repo:pr.open
    owner/repo:pr.comment
    owner/repo:pr.merge
    owner/repo:pr.merge.<branch>  merged into that branch
    owner/repo:pr.close           closed without merging
    owner/repo:pr/<n>             one PR thread, same shape
    owner/repo:issue              opened, edited, milestoned, commented, sub-issue linked
    owner/repo:issue/<n>          one issue thread, same shape

**`:` and not `#`.** `owner/repo#pr.merge` collides with GitHub's own autolink
wherever a topic appears in an issue or a commit message, and these strings
live in prose -- they are echoed back to a subscriber and read in chat logs.

**The branch segment is the *target*.** "Merged into main" is what changes an
agent's next action; a source branch exists for six hours and subscribing to
one is subscribing to something already gone. Named here rather than left to
be inferred, which is what #67 asked for.
"""

from __future__ import annotations

import re
from typing import Any

# `owner/repo` then `:` then a dotted or slashed selector. Deliberately strict:
# a topic is echoed back to the subscriber and used as the key it later
# unsubscribes with, so it has to be an exact literal rather than something
# that might normalise (#67).
# The selector may carry `/` because branch names do -- `release/2.0` is a
# target like any other, and a grammar that refused it would accept a
# subscription the matcher can never satisfy. Found by a test asserting the
# two halves agree, which they did not.
TOPIC = re.compile(r"^[\w.-]+/[\w.-]+:[\w.-]+(?:/[\w.-]+)*$")


def valid(topic: str) -> bool:
    """Whether this is a topic at all. Not whether anything will ever match it:
    a subscription to a repo that never fires is a quiet subscription, not an
    error, and refusing it would mean this file knowing which repos exist."""
    return bool(TOPIC.match(topic or ""))


def _repo(payload: dict[str, Any]) -> str:
    return ((payload.get("repository") or {}).get("full_name") or "").strip()


def topics_for(event: str, payload: dict[str, Any]) -> set[str]:
    """Every topic this delivery matches. Empty when nothing does.

    Empty is the common case and not a failure -- #59 accepts that most of the
    firehose is discarded here, and a caller that logged every miss would be
    logging the design.
    """
    repo = _repo(payload)
    if not repo:
        return set()
    out: set[str] = set()

    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        action = payload.get("action")
        number = pr.get("number")
        pr_topic = {f"{repo}:pr/{number}"} if number is not None else set()
        if action == "opened":
            out |= pr_topic | {f"{repo}:pr", f"{repo}:pr.open"}
        elif action == "closed":
            # Merged and closed are different facts and a subscriber to
            # `pr.close` does not want merges: "closed without merging" is the
            # one that means the work was abandoned.
            if pr.get("merged"):
                base = ((pr.get("base") or {}).get("ref") or "").strip()
                out |= pr_topic | {f"{repo}:pr", f"{repo}:pr.merge"}
                if base:
                    out.add(f"{repo}:pr.merge.{base}")
            else:
                out |= pr_topic | {f"{repo}:pr", f"{repo}:pr.close"}

    elif event == "issue_comment":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        # GitHub sends `issue_comment` for pull requests too, and tells them
        # apart only by this key. A comment on a PR is PR conversation, and a
        # subscriber to `pr` that missed it would be missing the common case.
        if issue.get("pull_request") is not None:
            # Symmetric with issue/<n> below: bare `pr` already covers a
            # comment on any PR, `pr/<n>` narrows to one thread the same way
            # `issue/<n>` does for issues.
            pr_topic = {f"{repo}:pr/{number}"} if number is not None else set()
            out |= pr_topic | {f"{repo}:pr", f"{repo}:pr.comment"}
        elif number is not None:
            # Symmetric with the PR branch above: `pr` (bare) already covers a
            # comment on a PR, so `issue` (bare) has to cover a comment on an
            # issue the same way, or "subscribe to all issue events" is false
            # advertising -- it would silently exclude the commonest one.
            out |= {f"{repo}:issue", f"{repo}:issue/{number}"}

    elif event == "issues":
        number = (payload.get("issue") or {}).get("number")
        if number is not None:
            out |= {f"{repo}:issue", f"{repo}:issue/{number}"}

    elif event == "sub_issues":
        # Both halves of one linking action carry both numbers regardless of
        # which side fired (#265) -- `sub_issue_added` lands on the parent's
        # own delivery, `parent_issue_added` on the child's, but each payload
        # already has `sub_issue.number` and `parent_issue.number` both. A
        # subscriber to either thread should hear about the link either way.
        for key in ("sub_issue", "parent_issue"):
            number = (payload.get(key) or {}).get("number")
            if number is not None:
                out |= {f"{repo}:issue", f"{repo}:issue/{number}"}

    return out
