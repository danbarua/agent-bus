"""The leader client, against a stub speaking grok's real wire protocol.

Four details here are not guessable and each was found by probing a live
leader (grok 1.0.5). The stub reproduces all four, so a regression on any of
them fails a test rather than silently reporting an empty roster.
"""
import json
import os
import pathlib
import shutil
import socket
import struct
import tempfile
import threading

import pytest
from stub_leader import StubLeader, entry

from agent_bus.grok_leader import (
    CHANGED_METHOD,
    LIST_METHOD,
    LeaderClient,
    LeaderError,
    activity_to_status,
    leader_available,
    session_status,
)


@pytest.fixture
def sock_path():
    """A *short* path. AF_UNIX caps at ~104 bytes on macOS and pytest's
    tmp_path is comfortably longer than that, so binding there fails with a
    bare OSError that looks like anything but a length limit."""
    d = tempfile.mkdtemp(prefix="abl", dir="/tmp")
    try:
        yield pathlib.Path(d) / "l.sock"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- the mapping ----------------------------------------------------------

@pytest.mark.parametrize("activity,expected", [
    ("working", "busy"),
    ("idle", "idle"),
    ("needs_input", "waiting"),
    # Not statuses: these mean the session is not running at all.
    ("dormant", None),
    ("completed", None),
    ("dead", None),
    ("something-new", None),
    (None, None),
])
def test_activity_maps_to_status(activity, expected):
    assert activity_to_status(activity) == expected


# --- the four traps -------------------------------------------------------

def test_list_unwraps_the_doubly_nested_result(sock_path):
    """`result.result.sessions`. Grok's own pager carries a comment about this:
    the inner struct's `sessions` has a serde default, so a single unwrap
    parses *successfully* into an empty roster and reports nothing."""
    with StubLeader(sock_path, sessions=[entry("s1", "working")]):
        with LeaderClient(str(sock_path)) as c:
            got = c.list_sessions()
    assert [s["sessionId"] for s in got] == ["s1"]


def test_list_also_tolerates_a_single_envelope(sock_path):
    """The shape is not promised, so accept the bare body too."""
    with StubLeader(sock_path, sessions=[entry("s1")], list_envelope="single"):
        with LeaderClient(str(sock_path)) as c:
            assert len(c.list_sessions()) == 1


def test_the_ext_method_is_underscore_prefixed():
    """The source calls it `x.ai/sessions/list`; the wire wants
    `_x.ai/sessions/list`, and the documented name answers -32601."""
    assert LIST_METHOD == "_x.ai/sessions/list"
    assert CHANGED_METHOD == "_x.ai/sessions/changed"


def test_an_unprefixed_method_is_an_error_not_an_empty_roster(sock_path):
    with StubLeader(sock_path, sessions=[entry("s1")]), LeaderClient(str(sock_path)) as c:
        with pytest.raises(LeaderError, match="Method not found"):
            c._acp("x.ai/sessions/list")


def test_a_response_is_not_confused_with_an_interleaved_notification(sock_path):
    """The stub pushes an announcement before answering initialize, which is
    what a real leader does."""
    with StubLeader(sock_path, sessions=[entry("s1", "idle")]):
        with LeaderClient(str(sock_path)) as c:
            assert [s["sessionId"] for s in c.list_sessions()] == ["s1"]


def test_it_waits_for_leader_ready_when_not_ready_at_registration(sock_path):
    """`ready: false` means the leader is still starting and ACP traffic sent
    before `leader_ready` is documented as forbidden."""
    with StubLeader(sock_path, sessions=[entry("s1")], ready_first=False):
        with LeaderClient(str(sock_path)) as c:
            assert len(c.list_sessions()) == 1


# --- framing --------------------------------------------------------------

def test_frames_are_four_byte_big_endian_length_prefixed(sock_path):
    """Not newline-delimited. Asserted on the bytes, from the server side."""
    seen = []
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)

    def accept_one():
        conn, _ = srv.accept()
        head = conn.recv(4)
        (n,) = struct.unpack(">I", head)
        body = b""
        while len(body) < n:
            body += conn.recv(n - len(body))
        seen.append((n, json.loads(body)))
        conn.close()

    t = threading.Thread(target=accept_one, daemon=True)
    t.start()
    try:
        with pytest.raises(LeaderError), LeaderClient(str(sock_path), timeout=3):
            pass
    finally:
        t.join(timeout=3)
        srv.close()

    assert seen, "nothing was sent"
    length, msg = seen[0]
    assert msg["type"] == "register"
    assert length == len(json.dumps(msg).encode())


def test_an_oversized_frame_is_refused(sock_path):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)

    def evil():
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(struct.pack(">I", 0xFFFFFFFF))
        conn.close()

    t = threading.Thread(target=evil, daemon=True)
    t.start()
    try:
        with pytest.raises(LeaderError, match="oversized"):
            with LeaderClient(str(sock_path), timeout=3):
                pass
    finally:
        t.join(timeout=3)
        srv.close()


# --- the subscription -----------------------------------------------------

def test_watch_yields_each_broadcast(sock_path):
    deltas = [
        {"upserted": [entry("s1", "working")], "removed": []},
        {"upserted": [entry("s1", "idle")], "removed": []},
        {"upserted": [], "removed": ["s2"]},
    ]
    got = []
    with StubLeader(sock_path, sessions=[entry("s1")], deltas=deltas):
        with LeaderClient(str(sock_path)) as c:
            c.list_sessions()          # the stub emits the deltas after this
            for delta in c.watch():
                got.append(delta)
                if len(got) == len(deltas):
                    break
    assert [d["upserted"][0]["activity"] for d in got[:2]] == ["working", "idle"]
    assert got[2]["removed"] == ["s2"]


# --- degrading ------------------------------------------------------------

def test_session_status_is_empty_when_no_leader_is_running(tmp_path, monkeypatch):
    """The common case. Discovery must not care."""
    monkeypatch.setenv("GROK_LEADER_SOCKET", str(tmp_path / "nope.sock"))
    assert leader_available() is False
    assert session_status() == {}


def test_session_status_reports_only_running_sessions(sock_path, monkeypatch):
    monkeypatch.setenv("GROK_LEADER_SOCKET", str(sock_path))
    sessions = [
        entry("live-1", "working"),
        entry("live-2", "needs_input"),
        entry("gone-1", "dormant"),
        entry("gone-2", "dead"),
    ]
    with StubLeader(sock_path, sessions=sessions):
        assert session_status() == {"live-1": "busy", "live-2": "waiting"}


def test_session_status_survives_a_leader_that_dies_mid_call(sock_path, monkeypatch):
    monkeypatch.setenv("GROK_LEADER_SOCKET", str(sock_path))
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)

    def rude():
        conn, _ = srv.accept()
        conn.close()

    t = threading.Thread(target=rude, daemon=True)
    t.start()
    try:
        assert session_status() == {}
    finally:
        t.join(timeout=3)
        srv.close()


def test_the_socket_path_honours_the_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_LEADER_SOCKET", "/custom/leader.sock")
    from agent_bus.grok_leader import leader_socket
    assert leader_socket() == "/custom/leader.sock"
    monkeypatch.delenv("GROK_LEADER_SOCKET")
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(tmp_path))
    assert leader_socket() == os.path.join(str(tmp_path), "leader.sock")
