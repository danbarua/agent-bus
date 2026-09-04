#!/usr/bin/env python3
"""What a webhook notification actually looks like, against real deliveries.

Previewing this used to mean adding a stray `print(got)` to the end of a test
in `test_webhook_bridge.py`, then remembering to take it back out -- and it
only ever showed whatever one payload that test happened to build by hand.
This renders `notify.py` against every real delivery already captured in
`cloud/tests/fixtures/github_webhooks/` (the same fixtures the test suite
runs against), no test file touched, nothing left behind.

    scripts/preview_notifications.py
    scripts/preview_notifications.py --event pull_request
    scripts/preview_notifications.py --digest

`--digest` renders the batched form instead -- everything that shares a topic,
collapsed into one message the way `_fan_out_batch` collapses one poll's
worth (#106) -- rather than one notification per delivery.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(REPO, "cloud", "tests", "fixtures", "github_webhooks")

sys.path.insert(0, os.path.join(REPO, "src"))

from agent_bridge import notify, topics  # noqa: E402


def _load(entry: dict) -> dict:
    with open(os.path.join(FIXTURES, entry["file"]), encoding="utf-8") as f:
        return json.load(f)


def _manifest() -> list[dict]:
    with open(os.path.join(FIXTURES, "MANIFEST.json"), encoding="utf-8") as f:
        return json.load(f)


def _print(heading: str, summary: str, text: str) -> None:
    print(f"--- {heading} ---")
    print(f"summary: {summary}")
    print(text)
    print()


def preview_single(entries: list[dict]) -> int:
    """One notification per delivery -- what a subscriber sees for one event."""
    shown = 0
    for entry in entries:
        payload = _load(entry)
        matched = topics.topics_for(entry["event"], payload)
        if not matched:
            continue
        parsed = notify.parse_event(entry["event"], payload, entry["delivery_id"])
        notif = notify.notification(matched, parsed)
        _print(f"{entry['file']} ({entry['action']})", notif.summary, notif.text)
        shown += 1
    return shown


def preview_digest(entries: list[dict]) -> int:
    """Everything that shares a topic, batched -- what a subscriber sees
    when several matching events land in the same poll (#106)."""
    by_topic: dict[str, list[notify.GitHubEvent]] = {}
    for entry in entries:
        payload = _load(entry)
        parsed = notify.parse_event(entry["event"], payload, entry["delivery_id"])
        for topic in topics.topics_for(entry["event"], payload):
            by_topic.setdefault(topic, []).append(parsed)

    shown = 0
    for topic, events in sorted(by_topic.items()):
        if len(events) < 2:
            continue
        notif = notify.digest(topic, events)
        _print(f"{topic} ({len(events)} events)", notif.summary, notif.text)
        shown += 1
    return shown


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render notify.py against real captured webhook deliveries.")
    parser.add_argument("--event", help="only preview this event type, e.g. pull_request")
    parser.add_argument("--digest", action="store_true",
                        help="render the batched digest form instead of one-per-delivery")
    args = parser.parse_args()

    entries = [e for e in _manifest() if not args.event or e["event"] == args.event]
    shown = preview_digest(entries) if args.digest else preview_single(entries)

    if shown == 0:
        scope = f" for --event {args.event}" if args.event else ""
        mode = "shares a topic with anything else" if args.digest else "matches a topic"
        print(f"nothing in the fixture set{scope} {mode}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
