"""The message an agent finally receives, authored from an event.

**Authoring, not couriering** -- which is the second distinction #59 draws to
keep the "not an AI secretary" rule intact. The bridge is not moving mail
between two peers here; it is writing a message from an event stream, and the
rule binds the courier role.

**The body is never copied.** A webhook carries prose written by anyone who can
comment on the repository, and that prose would land in an agent's context. The
message carries a command to run instead -- pointer discipline from the
predecessor, applied to an untrusted source. It is also why #250 (a
trusted-author allowlist) is still open: not copying the words limits the blast
radius, it does not decide whether to wake someone at all.

**What is here is what the payload has.** #223 §6 also asks for the merge
method -- squash changes the fix from `pull` to `rebase --onto` -- and GitHub's
`pull_request` payload does not carry it, nor the changed paths. Both need an
API call the bridge does not make, so they are absent rather than invented.
"""

from __future__ import annotations

from typing import Any


def _pr_line(payload: dict[str, Any]) -> tuple[str, str]:
    pr = payload.get("pull_request") or {}
    repo = (payload.get("repository") or {}).get("full_name") or "?"
    number = pr.get("number")
    title = (pr.get("title") or "").strip()
    base = (pr.get("base") or {}).get("ref") or "?"
    sha = (pr.get("merge_commit_sha") or "")[:12]
    summary = f"#{number} merged into {base}" if pr.get("merged") else f"#{number} {title}"
    body = [f"**{repo}#{number}** {title}".rstrip(),
            f"target: `{base}`" + (f"  sha: `{sha}`" if sha else "")]
    if url := pr.get("html_url"):
        body.append(url)
    body.append("")
    body.append(f"gh pr view {number} -R {repo} --comments")
    return summary, "\n".join(body)


def notification(topics: set[str], event: str, payload: dict[str, Any]) -> tuple[str, str]:
    """`(summary, text)` for one event.

    The topics that matched go in the text, so a subscriber can tell *why* it
    was woken -- an agent holding four subscriptions otherwise has to guess,
    and guessing is what the whole surface exists to remove.
    """
    if event == "pull_request":
        summary, body = _pr_line(payload)
    else:
        issue = payload.get("issue") or {}
        repo = (payload.get("repository") or {}).get("full_name") or "?"
        number = issue.get("number")
        summary = f"#{number} {event}"
        body = "\n".join([
            f"**{repo}#{number}** {(issue.get('title') or '').strip()}".rstrip(),
            issue.get("html_url") or "",
            "",
            f"gh issue view {number} -R {repo} --comments",
        ]).strip()
    matched = ", ".join(sorted(topics))
    return summary, f"{body}\n\nmatched: {matched}"
