"""Stdlib MCP server: file-bus tools over JSON-RPC."""
import json
import os

import pytest

from agent_bus.lifecycle import detect_kind
from agent_bus.mcp_server import TOOLS, handle_rpc
from agent_bus.store import register


def test_detect_kind_grok_plugin_root_beats_claude_alias(monkeypatch):
    monkeypatch.setenv("GROK_PLUGIN_ROOT", "/plugin")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin")
    monkeypatch.delenv("GROK_SESSION_ID", raising=False)
    monkeypatch.delenv("GROK_HOOK_EVENT", raising=False)
    assert detect_kind() == "grok"


def test_initialize_and_tools_list():
    init = handle_rpc({
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
    listed = handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {t["name"] for t in TOOLS}


def test_tools_list_send_inbox_ack(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    import subprocess

    child = subprocess.Popen(["sleep", "30"])
    try:
        register("sender", "other", pid=os.getpid(), home=home)
        register("target", "other", pid=child.pid, home=home)
        listed = handle_rpc({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_agents", "arguments": {}},
        })
        text = listed["result"]["content"][0]["text"]
        agents = json.loads(text)
        assert any(a["name"] == "target" for a in agents)

        sent = handle_rpc({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "send_message",
                "arguments": {"to": "target", "text": "hello via mcp", "summary": "hi"},
            },
        })
        sent_obj = json.loads(sent["result"]["content"][0]["text"])
        # The reply says who it went to and whether an answer can be waited
        # for. It used to carry the internal message id and the transport
        # name -- and, for a Claude target, the socket path it used.
        assert sent_obj == {"to": "target", "delivery": "now"}

        inbox = handle_rpc({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "get_inbox", "arguments": {"name": "target", "unread_only": True}},
        })
        msgs = json.loads(inbox["result"]["content"][0]["text"])
        assert msgs[0]["text"] == "hello via mcp"

        acked = handle_rpc({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "ack_message",
                "arguments": {"message_id": msgs[0]["id"], "name": "target"},
            },
        })
        assert json.loads(acked["result"]["content"][0]["text"])["acked"] is True
    finally:
        child.kill()
        child.wait()


def test_unknown_tool_is_error():
    resp = handle_rpc({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    })
    assert "error" in resp


def test_the_handshake_reports_the_real_package_version():
    """It said 0.1.0 while the package on disk was 0.1.4.

    The version in `serverInfo` is the one number a client has to trust, and it
    was a string literal repeated in two places rather than the distribution's
    own. hatch-vcs derives it from the git tag, so there is nothing to bump.
    """
    from agent_bus import __version__

    init = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
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
    reply = handle_rpc({"jsonrpc": "2.0", "id": 7, "method": method})
    assert "error" not in reply, reply
    assert reply["result"] == {key: []}


def test_the_capabilities_admit_what_is_answered():
    """A client that *does* read capabilities must see the same three the
    dispatcher will answer. Declaring one and refusing the other is worse than
    declaring neither: it invites exactly the call that fails."""
    init = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"clientInfo": {"name": "test"}}})
    assert set(init["result"]["capabilities"]) == {"tools", "resources", "prompts"}


def test_a_genuinely_unknown_method_is_still_an_error():
    """The empties are three named methods, not a blanket "yes" -- a server
    that answered everything would hide a real client-side typo."""
    reply = handle_rpc({"jsonrpc": "2.0", "id": 8, "method": "resources/read"})
    assert reply["error"]["code"] == -32601
