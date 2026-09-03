"""`webhooks.about()` against real GitHub deliveries, not shapes we guessed at.

Captured live from staging on 2026-09-03 -- real `pull_request`, `check_run`,
`push` and `issue_comment` payloads that actually reached the ingress, saved
before Firestore's TTL cleared them. `tests/test_webhook_ingress.py` already
covers the synthetic cases and the HTTP plumbing; this file exists so a future
GitHub payload-shape change (a renamed field, a new nesting) is caught against
what GitHub actually sends, not against what we assumed it sends when we wrote
the parser.

`MANIFEST.json` names the samples; `webhooks.about()` is the only thing under
test here, because it is the only place in this codebase that reads the shape
of the payload rather than treating it as an opaque blob.
"""

from __future__ import annotations

import json
import os

import pytest
import webhooks

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "github_webhooks")

with open(os.path.join(FIXTURES, "MANIFEST.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


def _load(entry):
    with open(os.path.join(FIXTURES, entry["file"]), "rb") as f:
        return f.read()


@pytest.mark.parametrize("entry", MANIFEST, ids=[m["file"] for m in MANIFEST])
def test_about_reads_every_real_delivery_without_raising(entry):
    """The one thing that must never happen: a real payload from a repository
    we do not control taking the ingress down. `about()` is read after the
    signature has already verified, so this is untrusted shape, not untrusted
    trust -- and it must degrade to an empty or partial dict, never an
    exception."""
    webhooks.about(_load(entry))


@pytest.mark.parametrize("entry", MANIFEST, ids=[m["file"] for m in MANIFEST])
def test_about_names_the_repository_on_every_real_delivery(entry):
    """`repository.full_name` is present on every event type GitHub sends --
    it is the one field this codebase's logging leans on hardest."""
    result = webhooks.about(_load(entry))
    assert result.get("repo") == entry["repo"], (entry["file"], result)


@pytest.mark.parametrize("entry", [m for m in MANIFEST if m["event"] == "pull_request"],
                         ids=lambda m: m["file"])
def test_about_names_the_pr_number_and_target_branch(entry):
    result = webhooks.about(_load(entry))
    assert result.get("action") == entry["action"]
    assert result.get("number") is not None
    assert result.get("base") is not None


def test_check_run_and_push_are_not_mistaken_for_pull_requests():
    """The two event types this codebase does not yet special-case (#issue
    tracked separately -- `topics_for` matches neither). `about()` must still
    answer with what it has -- repo and action -- rather than reading a
    `pull_request` or `issue` key that a `push`/`check_run` payload does not
    carry, which would be silently wrong rather than silently absent."""
    push = next(m for m in MANIFEST if m["event"] == "push")
    result = webhooks.about(_load(push))
    assert "number" not in result
    assert "base" not in result
    assert result.get("repo") == push["repo"]

    check_run = next(m for m in MANIFEST if m["event"] == "check_run")
    result = webhooks.about(_load(check_run))
    assert "number" not in result
    assert result.get("action") == check_run["action"]
