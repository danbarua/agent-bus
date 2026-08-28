"""The spool is keyed on the address, and nothing else may assume otherwise.

`SpoolClient` is a real, inspectable stand-in for the cloud, which means its
layout is an interface: the e2e suite writes replies into it by hand, and an
operator reads mail out of it when a bridge is misconfigured. Both need to know
where a message for `desktop:claude` goes.

Until this existed, nothing checked. The round trip was covered only by the
spendy e2e tests, which CI never runs, so when #70 made a bridge an address the
two sides drifted apart and stayed apart for five merges -- the tests wrote to
`claude/` while the bridge polled `desktop:claude/`, and an empty queue and an
unread one are the same directory listing.

Cheap, offline, and it fails the moment the layout moves under someone.
"""

from __future__ import annotations

import json
import os

from agent_bridge.bridge import SpoolClient

ADDRESS = "desktop:claude"


def test_a_message_is_spooled_under_the_whole_address(tmp_path):
    """`<kind>:<name>`, not the name. The kind is half the address, and a spool
    keyed on the name alone would put two different peers in one queue."""
    SpoolClient(str(tmp_path)).push(ADDRESS, {"id": "m1", "text": "hello"})

    path = tmp_path / ADDRESS / "outbound" / "m1.json"
    assert path.is_file(), (
        f"nothing at {path}; the tree holds {sorted(os.listdir(tmp_path))}"
    )
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["text"] == "hello"


def test_a_reply_dropped_in_by_hand_is_pulled_back(tmp_path):
    """The half an operator and the e2e suite use. A reply is a file you drop in
    the inbound directory, and `id` falls back to the filename so a human does
    not have to write one."""
    client = SpoolClient(str(tmp_path))
    inbound = tmp_path / ADDRESS / "inbound"
    inbound.mkdir(parents=True)
    (inbound / "r1.json").write_text(json.dumps({"to": "someone", "text": "ok"}))

    pulled = client.pull(ADDRESS)
    assert [(r["id"], r["text"]) for r in pulled] == [("r1", "ok")]

    client.ack(ADDRESS, ["r1"])
    assert client.pull(ADDRESS) == []


def test_two_addresses_are_two_queues(tmp_path):
    """One bridge per address, so one queue per address -- and ids are the
    cloud's, so two addresses can hold the same one. Sharing a directory would
    hand a webhook's mail to a desktop peer, and quietly overwrite it first."""
    client = SpoolClient(str(tmp_path))
    client.push(ADDRESS, {"id": "m1", "text": "for the desktop"})
    client.push("webhook:github", {"id": "m1", "text": "for the webhook"})

    def spooled(address: str) -> str:
        with open(tmp_path / address / "outbound" / "m1.json", encoding="utf-8") as f:
            return json.load(f)["text"]

    assert spooled(ADDRESS) == "for the desktop"
    assert spooled("webhook:github") == "for the webhook"
