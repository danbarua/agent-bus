"""An explicit sender name reaches the wire, instead of being replaced by ours.

The bridge relays for cloud agents and passes `from_name=<their name>`
(`agent_bridge/bridge.py`). Both native transports accepted that argument and
dropped it, so `send_peer_message` always stamped `_advertised_name(our_sock)`
-- the sending *process* -- and a recipient saw every relayed message as being
from the bridge, with no way to tell who actually spoke.

Compounding, not parallel, with #182: when that fallback resolves the bridge's
socket, the frame's `from` AND its `from-name` are both the bridge's.

`from` deliberately stays ours. It is the return address the recipient dials
back, so it has to be a socket we own; only the display name changes.
"""

import re

from agent_bus import uds


def test_an_explicit_from_name_is_used_verbatim(monkeypatch):
    """The bridge's case: the name passed in is the name on the frame."""
    captured = {}

    def fake_advertised(sock, default="agent-bus"):
        captured["fell_back"] = True
        return "the-bridge"

    monkeypatch.setattr(uds, "_advertised_name", fake_advertised)
    inner = uds._envelope("/tmp/s/1.sock", "hello", from_name="cloud-agent-7")
    assert 'from-name="cloud-agent-7"' in inner
    assert "fell_back" not in captured, "an explicit name must not consult our own"
    assert 'from="uds:/tmp/s/1.sock"' in inner, "the return address stays ours"


def test_no_explicit_name_falls_back_to_our_advertised_one(monkeypatch):
    """The ordinary case, where the sending process IS the sender."""
    monkeypatch.setattr(uds, "_advertised_name", lambda sock, default="agent-bus": "me")
    inner = uds._envelope("/tmp/s/1.sock", "hello", from_name=None)
    assert 'from-name="me"' in inner


def test_the_text_is_carried_not_modified(monkeypatch):
    """Attribution goes in the envelope, never into the message body."""
    monkeypatch.setattr(uds, "_advertised_name", lambda sock, default="agent-bus": "me")
    inner = uds._envelope("/tmp/s/1.sock", "the exact text", from_name="someone")
    body = re.search(r">\n(.*)\n</cross-session-message>", inner, re.S)
    assert body and body.group(1) == "the exact text"


def test_the_claude_transport_passes_from_name_to_the_wire(monkeypatch):
    """The layer the bug was actually in.

    `_envelope` honouring an explicit name is necessary and not sufficient:
    the original defect was `transport/claude.py` accepting `from_name` and
    calling `send_peer_message(sock, text)` without it, so the envelope never
    saw a name to honour. A test of the envelope alone passes with that bug
    fully restored -- verified by mutation, which is why this exists.
    """
    from agent_bus import uds
    from agent_bus.adapters.transport import claude

    seen = {}

    def fake_send(sock, text, from_name=None):
        seen["from_name"] = from_name
        return True

    monkeypatch.setattr(uds, "send_peer_message", fake_send)
    monkeypatch.setattr(claude, "socket_for", lambda entry: "/tmp/s/9.sock")
    claude.send({"name": "them", "kind": "claude"}, "hi", from_name="cloud-agent-7")
    assert seen["from_name"] == "cloud-agent-7", (
        "transport/claude.send dropped from_name before it reached the wire"
    )
