"""Every tool declares an output shape, and every path it can take conforms.

The spec is unambiguous once a schema is declared: "If an output schema is
provided, servers MUST provide structured results that conform to this schema."
Not the happy path -- every path. The empty inbox, the id that matched nothing,
the refused send.

Declared at all because ChatGPT will not work with tools that omit it, and that
is the expensive kind of failure on this surface: a connector caches a failed
discovery, and retries produce no server traffic at all, so there is nothing to
watch while you guess.

Validated with `jsonschema` rather than by asserting fields. Checking our own
schemas with our own checker would mark our own homework, and the whole point
of the declaration is that somebody else's validator will read it.
"""

from __future__ import annotations

import jsonschema
import pytest
import rpc
from contract import TOOLS
from store import Rejected

SCHEMAS = {t["name"]: t.get("outputSchema") for t in TOOLS}


class Store:
    """Enough to drive every branch, and nothing more."""

    def __init__(self, *, agents=(), messages=(), refuse=False):
        self.agents = list(agents)
        self.messages = list(messages)
        self.refuse = refuse
        self.written = []

    def roster(self, address):
        return self.agents

    def read(self, q, unread_only=True):
        return self.messages

    def read_one(self, q, message_id):
        return next((m for m in self.messages if m["id"] == message_id), None)

    def ack(self, q, ids):
        return len(ids)

    def write(self, q, message):
        if self.refuse:
            raise Rejected("inbox full")
        self.written.append(message)
        return "minted"


MSG = {"id": "m1", "from": "labkit-review", "summary": "s", "text": "body",
       "to": "desktop:claude", "read": False, "ts": 1.0, "expireAt": 2.0}

# (label, tool, args, store) -- one row per branch `call_tool` can take.
PATHS = [
    ("list_agents: nobody home", "list_agents", {}, Store()),
    ("list_agents: a roster", "list_agents", {},
     Store(agents=[{"name": "labkit-dev", "kind": "other"}])),
    # A roster row the bridge published without a kind: the schema requires
    # one, so the server supplies it rather than forwarding the gap.
    ("list_agents: a row missing its kind", "list_agents", {},
     Store(agents=[{"name": "half-published"}])),
    ("get_inbox: empty", "get_inbox", {}, Store()),
    ("get_inbox: one waiting", "get_inbox", {}, Store(messages=[MSG])),
    ("read_message: no id given", "read_message", {}, Store()),
    ("read_message: id matched nothing", "read_message", {"message_id": "nope"},
     Store(messages=[MSG])),
    ("read_message: found", "read_message", {"message_id": "m1"}, Store(messages=[MSG])),
    ("ack_message: no ids given", "ack_message", {}, Store()),
    ("ack_message: acked", "ack_message", {"ids": ["m1", "m2"]}, Store()),
    ("send_message: refused", "send_message", {"to": "x", "text": "t"},
     Store(refuse=True)),
    ("send_message: queued", "send_message", {"to": "x", "text": "t"}, Store()),
]


@pytest.mark.parametrize("label,tool,args,store",
                         PATHS, ids=[p[0] for p in PATHS])
def test_every_path_conforms_to_its_declared_output_schema(label, tool, args, store):
    out = rpc.call_tool(tool, args, store, "desktop", "claude")
    schema = SCHEMAS[tool]
    assert schema is not None, f"{tool} declares no outputSchema"
    assert "structuredContent" in out, (
        f"{label}: MUST provide structured results, and this path provides none")
    jsonschema.validate(out["structuredContent"], schema)


def test_every_tool_declares_an_output_schema():
    """A new tool without one is the failure this file exists to prevent, and
    it fails at the connector rather than here unless something checks."""
    missing = [name for name, schema in SCHEMAS.items() if schema is None]
    assert not missing, f"{missing} would be offered to ChatGPT without an output shape"


def test_the_declared_schemas_are_themselves_valid():
    """A malformed schema is accepted by us and rejected by the client that
    reads it -- which is the failure that produces no traffic to look at."""
    for name, schema in SCHEMAS.items():
        assert schema is not None, f"{name} declares no outputSchema"
        jsonschema.Draft202012Validator.check_schema(schema)  # raises if not
        assert schema["type"] == "object", f"{name}: a tool result is an object"


def test_a_refusal_and_a_bad_argument_are_marked_as_errors():
    """`isError` is how a tool reports its own failure, as distinct from a
    protocol error. Without it a refusal reads as a successful send whose prose
    happens to say otherwise."""
    refused = rpc.call_tool("send_message", {"to": "x", "text": "t"},
                            Store(refuse=True), "desktop", "claude")
    assert refused.get("isError") is True
    assert refused["structuredContent"]["queued"] is False

    ok = rpc.call_tool("send_message", {"to": "x", "text": "t"},
                       Store(), "desktop", "claude")
    assert "isError" not in ok
    assert ok["structuredContent"] == {"queued": True, "id": "minted"}
