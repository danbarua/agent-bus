"""The connector surface, pinned: its names, and the two shapes that are lessons.

Not a style test. A tool name outside MCP's pattern is never offered rather than
rejected, and a schema that drifts from what a client cached is a connector that
stopped working with no error anywhere.
"""

import contract


def test_the_surface_speaks_the_bus_vocabulary():
    """`AGENTS.md` fixes CLI `inbox`/`read` as MCP `get_inbox`/`read_message`.
    This surface spelled four of them its own way until #204."""
    assert contract.TOOL_NAMES == (
        "list_agents", "get_inbox", "read_message", "ack_message", "send_message")


def test_every_tool_name_is_one_a_connector_will_accept():
    """`^[a-zA-Z0-9_-]{1,64}$`. A name outside it is not rejected at call time --
    the tool is silently never offered, which looks like a broken server."""
    for name in contract.TOOL_NAMES:
        assert contract.TOOL_NAME.match(name), name


def test_write_requires_a_sender_it_cannot_infer():
    """The predecessor inferred it once and attributed a message to the wrong
    party. A caller that cannot say who it is has nothing useful to say."""
    write = next(t for t in contract.TOOLS if t["name"] == "send_message")
    assert set(write["inputSchema"]["required"]) == {"to", "text", "from"}


def test_ack_has_no_everything_mode():
    """Its `archive` defaulted to consuming everything, addressed or not, so
    "I forgot to pass it" and "not deployed here" were indistinguishable
    afterwards -- and a message for another session was destroyed."""
    ack = next(t for t in contract.TOOLS if t["name"] == "ack_message")
    assert ack["inputSchema"]["required"] == ["ids"]
    assert "all" not in ack["inputSchema"]["properties"]


def test_the_shapes_are_a_snapshot():
    """A snapshot, so changing the contract is a decision someone takes rather
    than a diff that slips past."""
    assert [(t["name"], sorted(t["inputSchema"]["properties"]),
             sorted(t["inputSchema"]["required"])) for t in contract.TOOLS] == [
        ("list_agents", [], []),
        ("get_inbox", ["unread_only"], []),
        ("read_message", ["message_id"], ["message_id"]),
        ("ack_message", ["ids"], ["ids"]),
        ("send_message", ["from", "summary", "text", "to"], ["from", "text", "to"]),
    ]
