"""The contract is frozen, so an edit to it must be a loud diff in review.

Not a style test. The tool schema is pinned per client at connection time and
ChatGPT caches discovery, so a change here reaches sessions that will never
re-read it -- and OpenAI's filtering is provoked by shipping changes. #64's
CloudClient and this server have to agree on these shapes forever.
"""

import contract


def test_the_surface_is_exactly_four_tools():
    assert contract.TOOL_NAMES == ("list-agents", "read", "ack", "write")


def test_every_tool_name_is_one_a_connector_will_accept():
    """`^[a-zA-Z0-9_-]{1,64}$`. A name outside it is not rejected at call time --
    the tool is silently never offered, which looks like a broken server."""
    for name in contract.TOOL_NAMES:
        assert contract.TOOL_NAME.match(name), name


def test_write_requires_a_sender_it_cannot_infer():
    """The predecessor inferred it once and attributed a message to the wrong
    party. A caller that cannot say who it is has nothing useful to say."""
    write = next(t for t in contract.TOOLS if t["name"] == "write")
    assert set(write["inputSchema"]["required"]) == {"to", "text", "from"}


def test_ack_has_no_everything_mode():
    """Its `archive` defaulted to consuming everything, addressed or not, so
    "I forgot to pass it" and "not deployed here" were indistinguishable
    afterwards -- and a message for another session was destroyed."""
    ack = next(t for t in contract.TOOLS if t["name"] == "ack")
    assert ack["inputSchema"]["required"] == ["ids"]
    assert "all" not in ack["inputSchema"]["properties"]


def test_the_shapes_are_frozen():
    """A snapshot, so changing the contract is a decision someone takes rather
    than a diff that slips past."""
    assert [(t["name"], sorted(t["inputSchema"]["properties"]),
             sorted(t["inputSchema"]["required"])) for t in contract.TOOLS] == [
        ("list-agents", [], []),
        ("read", ["unread_only"], []),
        ("ack", ["ids"], ["ids"]),
        ("write", ["from", "summary", "text", "to"], ["from", "text", "to"]),
    ]
