"""Stdlib MCP server: file-bus tools over JSON-RPC."""
import json
import os

from agent_bus.mcp_server import TOOLS, handle_rpc
from agent_bus.plugin_host import detect_kind
from agent_bus.store import register, send_message


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
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test"}},
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
            "params": {"name": "ack_message", "arguments": {"message_id": msgs[0]["id"], "name": "target"}},
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
