"""Adapter tests use synthetic fixtures, never live files."""
import json
import os

import pytest

from agent_bus.adapters.discovery import claude, omp

CLAUDE_FIXTURE = {
    "pid": 12345,
    "sessionId": "sess-abc-123",
    "cwd": "/tmp/test",
    "startedAt": 1724000000000,
    "procStart": "Fri Aug 21 12:00:00 2026",
    "version": "2.1.239",
    "peerProtocol": 1,
    "peerFeatures": ["notify_idle"],
    "kind": "interactive",
    "entrypoint": "cli",
    "messagingSocketPath": "/tmp/cc-socks/12345.sock",
    "name": "test-claude",
    "nameSince": 1724000000000,
    "updatedAt": 1724000001000,
    "status": "idle",
    "statusUpdatedAt": 1724000001000,
}

GROK_FIXTURE_ACTIVE = [
    {"session_id": "g-uuid-1", "pid": 23456, "cwd": "/tmp/g", "opened_at": "2026-..."}
]

OMP_CLIENT_FIXTURE = {"pid": 34567, "id": "omp-daemon-1", "projectDir": "/tmp/omp"}

CODEX_PM_FIXTURE = {"processes": [{"pid": 45678, "cwd": "/tmp/cx"}]}


def test_claude_adapter(tmp_path, monkeypatch):
    sdir = str(tmp_path / "claude-sess")
    os.makedirs(sdir)
    # write a fixture but with our real current pid so is_pid_alive passes
    live_pid = os.getpid()
    data = dict(CLAUDE_FIXTURE)
    data["pid"] = live_pid
    data["name"] = "live-claude"
    with open(os.path.join(sdir, f"{live_pid}.json"), "w") as f:
        json.dump(data, f)

    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", sdir)
    found = claude.discover()
    assert len(found) == 1
    a = found[0]
    assert a["kind"] == "claude"
    assert a["name"] == "live-claude"
    assert a["pid"] == live_pid
    assert "claude:" in a["id"]
    assert a["native"]["messagingSocketPath"]


def _write_omp_daemon_client_and_session(base, pid, session_id, source, title):
    """The on-disk shape discover() reads: a live daemon client record plus
    the session file that names it -- shared by the tests below so each one
    only has to say what varies (the title's source, mainly)."""
    cdir = base / "run" / "daemons" / "d1" / "clients"
    sdir = base / "agent" / "sessions" / "cwd_encoded"
    cdir.mkdir(parents=True)
    sdir.mkdir(parents=True)
    (cdir / "c1.json").write_text(
        json.dumps({"pid": pid, "id": session_id, "projectDir": "/p"})
    )
    (sdir / f"{session_id}.jsonl").write_text(
        json.dumps({"type": "title",
                    "v": "1",
                    "source": source,
                    "updatedAt": "2026-09-01T00:00:00Z",
                    "title": title})
    )


def test_omp_adapter(tmp_path, monkeypatch):
    """A daemon client record carries a pid, and a user-assigned session
    title supplies its name -- together they become exactly one row."""
    session_id = "__OMP_SESSION_ID__"
    session_name = "__OMP_SESSION_NAME__"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source="user", title=session_name
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    found = omp.discover()
    assert [(a["kind"], a["pid"], a["name"]) for a in found] == [
        ("omp", live_pid, session_name)
    ]


@pytest.mark.parametrize("source", ["system", "auto", "assistant", ""])
def test_omp_adapter_only_discovers_user_assigned_names(tmp_path, monkeypatch, source):
    """Complements test_omp_adapter_uses_user_assigned_session_title: same
    shape, source flipped. A title OMP assigned itself (or any source other
    than "user") must not surface a roster row -- discover() has no way to
    tell a real handle from session-log noise, so it skips the session
    entirely rather than register it under a name nobody chose."""
    session_id = "__OMP_SESSION_ID__"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source=source, title="__OMP_AUTO_TITLE__"
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    assert omp.discover() == []


def test_omp_adapter_uses_user_assigned_session_title(tmp_path, monkeypatch):
    """When a session file carries a user-assigned title, use it as the name."""
    session_id = "__OMP_SESSION_ID__"
    session_name = "__OMP_SESSION_NAME__"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source="user", title=session_name
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    found = omp.discover()
    assert [(a["kind"], a["pid"], a["name"]) for a in found] == [
        ("omp", live_pid, session_name)
    ]


def test_a_terminal_session_file_is_not_an_agent(tmp_path, monkeypatch):
    """An omp agent is discovered from a daemon client record, which has a pid.

    Terminal-session files have none -- they hold a working directory and a
    path to a session log -- and a roster row without a live process is an
    address with nobody behind it. So these yield nothing.

    Real glob against a real file: the rule is about what a filename means,
    which is not something a stubbed glob can get wrong.
    """
    base = tmp_path / "omp"
    ts = base / "agent" / "terminal-sessions"
    ts.mkdir(parents=True)
    # The shape found on disk: a working directory and a session log. No pid.
    (ts / "ttys001").write_text(
        "/Users/someone/Code/project\n"
        "/Users/someone/.omp/agent/sessions/-Code-project/2026-08-17T14-35-16Z.jsonl\n"
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    assert omp.discover() == []
