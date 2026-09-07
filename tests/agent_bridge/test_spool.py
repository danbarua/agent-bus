"""The spool is keyed on the address, and nothing else may assume otherwise.

`SpoolClient` is a real, inspectable stand-in for the cloud, which means its
layout is an interface: the e2e suite writes replies into it by hand, and an
operator reads mail out of it when a bridge is misconfigured. Both need to know
where a message for `desktop:claude` goes.

Offline, so CI runs it. The round trip is otherwise exercised only by the
spendy e2e suite, which CI does not run -- and a layout that moves under a
reader fails silently, because an unread queue and an empty one are the same
directory listing.
"""

from __future__ import annotations

import json
import os

from agent_bridge.bridge import SpoolClient
from agent_bus.protocol import BridgeAddress

ADDRESS = BridgeAddress("desktop:claude")


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
    client.push(BridgeAddress("webhook:github"), {"id": "m1", "text": "for the webhook"})

    def spooled(address: str) -> str:
        with open(tmp_path / address / "outbound" / "m1.json", encoding="utf-8") as f:
            return json.load(f)["text"]

    assert spooled(ADDRESS) == "for the desktop"
    assert spooled("webhook:github") == "for the webhook"


# --------------------------------------------------------------- the pairing
#
# #296: two bridges, each pointed at the same spool root, stand in for the two
# machines in a `remote` relay. `pair` is honoured here the way the real cloud
# honours it -- mutually, and only mutually -- so the same test proves the
# mechanism without a Firestore emulator.

STUDIO_BRIDGE = BridgeAddress("remote:macbook-claude")  # runs on the studio
MACBOOK_BRIDGE = BridgeAddress("remote:studio-claude")  # runs on the macbook


def test_a_one_sided_pairing_still_spools_to_its_own_outbound(tmp_path):
    client = SpoolClient(str(tmp_path))
    client.pair(STUDIO_BRIDGE, MACBOOK_BRIDGE)  # the macbook has not agreed yet

    client.push(STUDIO_BRIDGE, {"id": "m1", "text": "hi"})

    assert (tmp_path / STUDIO_BRIDGE / "outbound" / "m1.json").is_file()
    assert not (tmp_path / MACBOOK_BRIDGE / "inbound" / "m1.json").exists()


def test_a_mutual_pairing_relays_to_the_peers_inbound_addressed_to_the_real_local_name(
    tmp_path,
):
    client = SpoolClient(str(tmp_path))
    client.pair(STUDIO_BRIDGE, MACBOOK_BRIDGE)
    client.pair(MACBOOK_BRIDGE, STUDIO_BRIDGE)

    client.push(STUDIO_BRIDGE, {"id": "m1", "from": "studio-claude", "text": "hi"})

    assert not (tmp_path / STUDIO_BRIDGE / "outbound" / "m1.json").exists(), (
        "relayed, not also spooled where nothing would ever drain it"
    )
    path = tmp_path / MACBOOK_BRIDGE / "inbound" / "m1.json"
    with open(path, encoding="utf-8") as f:
        relayed = json.load(f)
    assert relayed["to"] == "macbook-claude", (
        "delivered on the macbook, so `to` must name the real local peer there"
    )

    pulled = client.pull(MACBOOK_BRIDGE)
    assert [(r["id"], r["to"]) for r in pulled] == [("m1", "macbook-claude")]
