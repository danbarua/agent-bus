"""Stdlib MCP server: file-bus tools over JSON-RPC."""
import json
import os

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
        assert "id" in sent_obj

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
