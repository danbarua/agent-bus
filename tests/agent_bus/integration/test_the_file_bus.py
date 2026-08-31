"""The bus carries mail between two agents, with no harness involved.

Everything else in this directory needs a coding agent or a Claude session.
This does not, so it costs nothing and runs in every sweep -- and when it
fails, nothing further in here is worth reading: the thing they all stand on
is broken.

Driven through the CLI, because that is what an agent has.

A real sequence diagram from this test is in docs/e2e-scenarios.md.
"""

import json

from agent_names import mint_agent_name
from busctl import bus, inbox, register


def test_a_registered_agent_is_listed_and_has_an_empty_inbox(bus_home):
    name = mint_agent_name()
    register(bus_home, name, "other")

    listed = json.loads(bus(bus_home, "list", "--json").stdout)
    assert any(a["name"] == name for a in listed), listed

    assert inbox(bus_home, name) == []


def test_a_message_reaches_the_recipient_unread_and_attributed(bus_home):
    """Unread and attributed are the two halves that make it a message.

    Delivered-but-read would be indistinguishable from never arriving, and
    delivered-but-anonymous cannot be replied to.
    """
    sender, recipient = mint_agent_name(), mint_agent_name()
    register(bus_home, sender, "other")
    register(bus_home, recipient, "other")

    r = bus(bus_home, "send", recipient, "-m", "ping", "--from-name", sender)
    assert r.returncode == 0, r.stderr

    msgs = inbox(bus_home, recipient)
    assert len(msgs) == 1, msgs
    assert msgs[0]["text"] == "ping"
    assert msgs[0]["from"]["name"] == sender
    assert msgs[0]["read"] is False
