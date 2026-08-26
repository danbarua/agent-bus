"""Adapter tests use synthetic fixtures, never live files."""
import json
import os

from agent_bus.adapters.discovery import claude, grok, omp

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


def test_grok_adapter(tmp_path, monkeypatch):
    gdir = str(tmp_path / "grok")
    os.makedirs(gdir)
    live_pid = os.getpid()
    active = [{"session_id": "g1", "pid": live_pid, "cwd": "/tmp"}]
    with open(os.path.join(gdir, "active_sessions.json"), "w") as f:
        json.dump(active, f)

    # patch _grok_dir
    def fake(): return gdir
    monkeypatch.setattr(grok, "_grok_dir", fake)

    found = grok.discover()
    assert len(found) == 1
    assert found[0]["kind"] == "grok"
    assert found[0]["pid"] == live_pid
    assert found[0]["name"] == f"grok-{live_pid}"


def test_grok_adapter_uses_session_title(tmp_path, monkeypatch):
    from urllib.parse import quote

    gdir = str(tmp_path / "grok")
    os.makedirs(gdir)
    live_pid = os.getpid()
    cwd = "/Users/dan/Code/agents/exo-ledger"
    sid = "01a02536-fd0c-7781-8ca0-f9ed67563714"
    active = [{"session_id": sid, "pid": live_pid, "cwd": cwd}]
    with open(os.path.join(gdir, "active_sessions.json"), "w") as f:
        json.dump(active, f)
    summary_dir = os.path.join(gdir, "sessions", quote(cwd, safe=""), sid)
    os.makedirs(summary_dir)
    with open(os.path.join(summary_dir, "summary.json"), "w") as f:
        json.dump({
            "generated_title": "exo-grok",
            "title_is_manual": True,
            "agent_name": "grok-build-plan",
        }, f)

    monkeypatch.setattr(grok, "_grok_dir", lambda: gdir)
    found = grok.discover()
    assert len(found) == 1
    assert found[0]["name"] == "exo-grok"


def test_omp_adapter(tmp_path, monkeypatch):
    """A daemon client record carries a pid, so it becomes exactly one row."""
    base = tmp_path / "omp"
    cdir = base / "run" / "daemons" / "d1" / "clients"
    cdir.mkdir(parents=True)
    live_pid = os.getpid()
    (cdir / "c1.json").write_text(
        json.dumps({"pid": live_pid, "id": "omp1", "projectDir": "/p"})
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    found = omp.discover()
    assert [(a["kind"], a["pid"], a["name"]) for a in found] == [
        ("omp", live_pid, "omp1")
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
