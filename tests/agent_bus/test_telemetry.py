"""What agents asked the server to do, recorded so silence can be read.

A client that connects and then calls nothing produces the same traffic as one
that never connected: none. These tests pin the three properties that make the
log able to tell those apart, and the one that stops it becoming a liability.
"""

from __future__ import annotations

import pytest

from agent_bus import mcp_server, telemetry


@pytest.fixture
def bus(tmp_path, monkeypatch, short_sock_dir):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path / "bus"))
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    return str(tmp_path / "bus")


def _call(tool, args=None, mid=1):
    return mcp_server.handle_rpc({
        "jsonrpc": "2.0", "id": mid, "method": "tools/call",
        "params": {"name": tool, "arguments": args or {}},
    })


# ------------------------------------------------------------ what is kept

def test_a_successful_call_is_recorded(bus):
    """The property the whole thing rests on. Logging only failures leaves
    "connected but never called anything" indistinguishable from "never
    connected", which is the case that actually needs diagnosing."""
    mcp_server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    lines = telemetry.read()
    assert [entry["method"] for entry in lines] == ["tools/list"]
    assert lines[0]["ok"] is True


def test_a_failed_call_is_recorded_with_its_code(bus):
    _call("no_such_tool")
    entry = telemetry.read()[0]
    assert entry["ok"] is False
    assert entry["code"] == -32601
    assert "no_such_tool" in entry["error"]


def test_the_handshake_records_who_is_calling(bus):
    """Every other line says what happened. Without this none says who did it,
    and the answer differs per harness."""
    mcp_server.handle_rpc({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"clientInfo": {"name": "codex-mcp-client", "version": "1"}},
    })
    assert telemetry.read()[0]["client"] == "codex-mcp-client"


def test_every_call_is_timed(bus):
    _call("self")
    assert isinstance(telemetry.read()[0]["ms"], int)


# --------------------------------------------------------- what is not kept

def test_message_bodies_are_measured_not_copied(bus):
    """Addressing is recorded; content is not. A log that copied message text
    would duplicate every inbox into a file with a different lifetime and no
    TTL, which is a worse leak than the diagnosis is worth."""
    _call("send_message", {"to": "nobody", "text": "the merger closes friday",
                           "summary": "confidential"})

    entry = telemetry.read()[0]
    assert entry["args"]["to"] == "nobody", "addressing is what you need to read it back"
    assert entry["args"]["text_len"] == len("the merger closes friday")
    assert entry["args"]["summary_len"] == len("confidential")

    serialized = str(telemetry.read())
    assert "merger closes friday" not in serialized
    assert "confidential" not in serialized


def test_describe_args_keeps_scalars_and_names_other_types():
    got = telemetry.describe_args({"to": "x", "unread_only": True, "blob": {"a": 1}})
    assert got == {"to": "x", "unread_only": True, "blob": "dict"}


# ------------------------------------------------------- what it must not do

def test_a_broken_log_never_breaks_the_server(bus, monkeypatch):
    """A server that fell over because it could not write its own diagnostics
    would be worse than one with none."""
    def boom(*a, **kw):
        raise OSError("disk gone")

    monkeypatch.setattr(telemetry, "record", boom)
    resp = mcp_server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp["result"]["tools"], "the call still answered"


def test_writing_stops_at_the_cap_rather_than_growing_forever(bus, monkeypatch):
    """Bounded, because a long-lived server writes to one file. Stops rather
    than rotating: losing the newest lines is worse than keeping none of the
    newest, and silent discarding is worse than either."""
    monkeypatch.setattr(telemetry, "MAX_BYTES", 200)
    for i in range(200):
        telemetry.record({"n": i})

    import os
    assert os.path.getsize(telemetry.log_path()) < 2000
    assert telemetry.read(), "it still wrote something before stopping"


def test_reading_a_log_that_is_not_there_is_empty_not_an_error(bus):
    assert telemetry.read(pid=999999) == []
