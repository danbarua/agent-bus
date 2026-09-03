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


def test_the_sender_is_not_something_the_caller_is_asked_for():
    """The predecessor inferred the sender once and attributed a message to
    the wrong party, so this contract made `from` required and described it as
    "never inferred". The worry was right and the remedy was not: a required
    field is a question, and the thing answering is a model.

    It answered "Claude Desktop (bonsai-2026)" -- fluent, plausible, and an
    address for nobody (#242). Meanwhile the token had said `desktop:claude`
    all along, and the bridge endpoint on this same server already refuses to
    let a caller override it.

    The credential is neither inferring nor asking, which is the third option
    that was available the whole time.
    """
    write = next(t for t in contract.TOOLS if t["name"] == "send_message")
    assert set(write["inputSchema"]["required"]) == {"to", "text"}
    assert "from" not in write["inputSchema"]["properties"]


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
        ("send_message", ["summary", "text", "to"], ["text", "to"]),
    ]
