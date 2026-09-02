"""Stdlib MCP server: file-bus tools over JSON-RPC."""
import json
import os

import pytest

from agent_bus import store
from agent_bus.lifecycle import detect_kind
from agent_bus.mcp_server import TOOLS, handle_rpc
from agent_bus.protocol import AgentTarget
from agent_bus.store import register


def _rpc(msg):
    """A reply, never a notification.

    `handle_rpc` returns None for a `notifications/` method and every caller
    here indexes straight into the result, so the assertion belongs once --
    rather than as a `["result"]` that raises TypeError somewhere with no clue
    which request produced it. Same fix as cloud/tests took in #233.
    """
    reply = handle_rpc(msg)
    assert reply is not None, f"{msg.get('method')} answered nothing"
    return reply



def test_detect_kind_grok_plugin_root_beats_claude_alias(monkeypatch):
    monkeypatch.setenv("GROK_PLUGIN_ROOT", "/plugin")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin")
    monkeypatch.delenv("GROK_SESSION_ID", raising=False)
    monkeypatch.delenv("GROK_HOOK_EVENT", raising=False)
    assert detect_kind() == "grok"


def test_initialize_and_tools_list():
    init = _rpc({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test"},
        },
    })
    assert init["result"]["serverInfo"]["name"] == "agent-bus"
    assert "tools" in init["result"]["capabilities"]
    listed = _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {t["name"] for t in TOOLS}


def test_tools_list_send_inbox_ack(tmp_path, monkeypatch):
    """The core send -> inbox -> ack round trip over MCP.

    `send_message` still addresses another agent by name -- that is what `to`
    is for. `get_inbox`/`ack_message` no longer take one at all: they only
    ever answer for the calling process's own identity (retired alongside
    #156's `from_name` fix -- see
    test_get_inbox_and_ack_cannot_target_another_agents_mailbox for the
    negative case this enables). So delivery to a separate peer is checked
    by reading that peer's mailbox directly, not through MCP -- this session
    is no longer entitled to -- and the read/ack half is proven on mail
    addressed to THIS session instead, which is the only mailbox its own MCP
    calls can ever reach.
    """
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    import subprocess

    child = subprocess.Popen(["sleep", "30"])
    try:
        register("caller", "other", pid=os.getpid(), home=home)
        register("target", "other", pid=child.pid, home=home)
        listed = _rpc({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_agents", "arguments": {}},
        })
        text = listed["result"]["content"][0]["text"]
        agents = json.loads(text)
        assert any(a["name"] == "target" for a in agents)

        sent = _rpc({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "send_message",
                "arguments": {"to": "target", "text": "hello via mcp", "summary": "hi"},
            },
        })
        sent_obj = json.loads(sent["result"]["content"][0]["text"])
        # The reply says who it went to, whether an answer can be waited for,
        # and the id of the message -- and nothing else. It used to also carry
        # the transport name and, for a Claude target, the socket path: a
        # mechanism the caller cannot use, and a path into another process.
        #
        # The id is not in that category and came back in #108. `ack_message`
        # takes it and `get_inbox` returns it, so it was already public on the
        # receiving side; withholding it from the sender was asymmetric.
        assert set(sent_obj) == {"to", "delivery", "id"}, sent_obj
        assert sent_obj["to"] == "target"
        assert sent_obj["delivery"] == "now"
        assert sent_obj["id"], "a sender must be able to reference what it sent"

        # Delivery landed -- checked directly, since this session's own MCP
        # calls can no longer read "target"'s mailbox to confirm it.
        delivered = store.get_inbox(target=AgentTarget("target"), home=home)
        assert delivered and delivered[0]["text"] == "hello via mcp"

        # Now the read/ack half, on mail addressed to THIS session.
        store.send_message(to=AgentTarget("caller"), text="for you", summary="", home=home)
        inbox = _rpc({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "get_inbox", "arguments": {"unread_only": True}},
        })
        msgs = json.loads(inbox["result"]["content"][0]["text"])
        assert msgs[0]["text"] == "for you"

        acked = _rpc({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "ack_message", "arguments": {"message_id": msgs[0]["id"]}},
        })
        assert json.loads(acked["result"]["content"][0]["text"])["acked"] is True
    finally:
        child.kill()
        child.wait()


def test_get_inbox_and_ack_cannot_target_another_agents_mailbox(tmp_path, monkeypatch):
    """`get_inbox`/`ack_message` used to take a `name` -- addressing ANY
    registered agent's mailbox, not just this session's own. Any MCP client
    could read or ack mail that was never addressed to it, just by naming
    the real recipient: the read-side sibling of #156's `from_name` spoof.
    Retired entirely -- an old-style call that still sends `name` is
    answered as if it had not been: self, always."""
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    import subprocess

    child = subprocess.Popen(["sleep", "30"])
    try:
        register("caller", "other", pid=os.getpid(), home=home)
        register("victim", "other", pid=child.pid, home=home)
        store.send_message(to=AgentTarget("victim"), text="private", summary="", home=home)

        inbox = _rpc({
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {"name": "get_inbox", "arguments": {"name": "victim"}},
        })
        assert json.loads(inbox["result"]["content"][0]["text"]) == [], (
            "an MCP call must never read another agent's mailbox"
        )

        # An attacker who already has the id -- leaked via a watch line
        # elsewhere, say -- tries to ack it anyway.
        victims_mail = store.get_inbox(target=AgentTarget("victim"), home=home)
        acked = _rpc({
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "ack_message",
                "arguments": {"message_id": victims_mail[0]["id"], "name": "victim"},
            },
        })
        assert json.loads(acked["result"]["content"][0]["text"])["acked"] is False
        assert not store.get_inbox(target=AgentTarget("victim"), home=home)[0]["read"], (
            "victim's real mail must be untouched"
        )
    finally:
        child.kill()
        child.wait()


def test_send_message_ignores_an_asserted_from_name(tmp_path, monkeypatch):
    """#156: the schema never advertised `from_name`, but `_call_send` used to
    read it from the call anyway -- so any MCP client could claim to be any
    name at all, and the inbox recorded exactly that claim. Verified
    directly, and fixed by never reading it: the real identity is whichever
    registered entry this process is."""
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    import subprocess

    child = subprocess.Popen(["sleep", "30"])
    try:
        register("real-sender", "other", pid=os.getpid(), home=home)
        register("target", "other", pid=child.pid, home=home)

        _rpc({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "send_message",
                "arguments": {
                    "to": "target",
                    "text": "hello via mcp",
                    "from_name": "someone-else-entirely",
                },
            },
        })

        # Checked directly: this session's own get_inbox can no longer read
        # "target"'s mailbox to confirm it (see the test above).
        msgs = store.get_inbox(target=AgentTarget("target"), home=home)
        assert msgs[0]["from_"].name == "real-sender"
        assert msgs[0]["from_"].name != "someone-else-entirely"
    finally:
        child.kill()
        child.wait()


def test_watch_then_read_message_is_reachable_from_mcp_alone(tmp_path, monkeypatch):
    """#152: an MCP-native agent parked on `watch` had no way to fetch a body.

    `watch` gives only an id and a summary (by design -- see its docstring),
    so the only MCP-reachable "get the whole message" tool was `get_inbox`,
    which bulk-fetches the mailbox rather than the one message a notice named.
    This drives the actual sequence such an agent uses: watch emits an id,
    `read_message` fetches that id, and the full body -- not the summary --
    comes back.

    One identity, not two: the real scenario is the watching session reading
    its OWN notice, so it IS the recipient rather than a name it separately
    asserts to `read_message`.
    """
    import io

    from agent_bus import watch as watch_mod

    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)

    register("target", "other", pid=os.getpid(), home=home)

    body = "the whole point is this sentence, not the summary"
    store.send_message(to=AgentTarget("target"), text=body, summary="short", home=home)

    out = io.StringIO()
    watch_mod.watch(target=AgentTarget("target"), home=home, from_start=True, once=True, out=out)
    line = out.getvalue().strip()
    assert "summary=short" in line
    assert body not in line, "watch must not leak the body -- that is the bug"
    notice_id = line.split("id=", 1)[1].split(" ", 1)[0]

    read = _rpc({
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {"name": "read_message", "arguments": {"message_id": notice_id}},
    })
    msg = json.loads(read["result"]["content"][0]["text"])
    assert msg["text"] == body


def test_read_message_is_null_for_an_unknown_id(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    register("solo", "other", pid=os.getpid(), home=home)
    resp = _rpc({
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {"name": "read_message", "arguments": {"message_id": "nope"}},
    })
    assert json.loads(resp["result"]["content"][0]["text"]) is None


def test_unknown_tool_is_error():
    resp = _rpc({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    })
    assert "error" in resp


def test_a_missing_required_field_is_a_clean_error_not_a_keyerror():
    """`inputSchema.required` was purely decorative -- `_dispatch` called the
    handler straight through, so a genuinely missing field surfaced as a raw
    `KeyError` wrapped in a -32000, and a present-but-empty string satisfied
    "required" trivially (JSON Schema's own `required` is presence-only).
    Measured: `send_message` with no `text` at all produced the error text
    `'text'`. Now checked generically, off each tool's own schema, before the
    handler runs at all."""
    resp = _rpc({
        "jsonrpc": "2.0",
        "id": 13,
        "method": "tools/call",
        "params": {"name": "send_message", "arguments": {"to": "someone"}},
    })
    assert resp["error"]["code"] == -32602
    assert "text" in resp["error"]["message"]
    assert resp["error"]["message"] != "'text'", "must not be a bare KeyError repr"


def test_an_empty_required_field_is_rejected_same_as_a_missing_one():
    """A present `text: ""` satisfies JSON Schema's `required` -- it only
    checks the key exists -- but is not what a caller meant to send. #156's
    audit found this is how an "empty message" got through at all."""
    resp = _rpc({
        "jsonrpc": "2.0",
        "id": 14,
        "method": "tools/call",
        "params": {"name": "send_message", "arguments": {"to": "someone", "text": ""}},
    })
    assert resp["error"]["code"] == -32602
    assert "text" in resp["error"]["message"]


def test_the_handshake_reports_the_real_package_version():
    """It said 0.1.0 while the package on disk was 0.1.4.

    The version in `serverInfo` is the one number a client has to trust, and it
    was a string literal repeated in two places rather than the distribution's
    own. hatch-vcs derives it from the git tag, so there is nothing to bump.
    """
    from agent_bus import __version__

    init = _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05",
                                  "capabilities": {}, "clientInfo": {"name": "x"}}})
    reported = init["result"]["serverInfo"]["version"]
    assert reported == __version__
    assert reported != "0.1.0", "the hardcoded literal is back"


def test_the_codex_client_identifies_with_the_same_version():
    from agent_bus import __version__
    from agent_bus.adapters.transport.codex import CLIENT_VERSION

    assert __version__ == CLIENT_VERSION


# ---------------------------------------------------- eager discovery (#71)

@pytest.mark.parametrize("method, key", [
    ("resources/list", "resources"),
    ("resources/templates/list", "resourceTemplates"),
    ("prompts/list", "prompts"),
])
def test_eager_discovery_gets_an_empty_result_not_method_not_found(method, key):
    """Some clients call these unconditionally, before reading capabilities.

    Found in the predecessor by debugging a real ChatGPT connector: a hard
    `Method not found` there did not make resources unavailable, it **broke
    discovery entirely** -- the client showed no tools at all. The failure
    reads as "this server has nothing", which is the last place anyone looks
    for a missing resources handler.

    None of the five coding harnesses does this today -- measured from a full
    container run: initialize, notifications/initialized, tools/list,
    tools/call, and nothing else. This is for the first MCP client we do not
    control, which is exactly what `agent-bus mcp` being installable invites.
    """
    reply = _rpc({"jsonrpc": "2.0", "id": 7, "method": method})
    assert "error" not in reply, reply
    assert reply["result"] == {key: []}


def test_the_capabilities_admit_what_is_answered():
    """A client that *does* read capabilities must see the same three the
    dispatcher will answer. Declaring one and refusing the other is worse than
    declaring neither: it invites exactly the call that fails."""
    init = _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"clientInfo": {"name": "test"}}})
    assert set(init["result"]["capabilities"]) == {"tools", "resources", "prompts"}


def test_a_genuinely_unknown_method_is_still_an_error():
    """The empties are three named methods, not a blanket "yes" -- a server
    that answered everything would hide a real client-side typo."""
    reply = _rpc({"jsonrpc": "2.0", "id": 8, "method": "resources/read"})
    assert reply["error"]["code"] == -32601
