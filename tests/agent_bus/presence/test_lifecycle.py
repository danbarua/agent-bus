"""Plugin host: kind/pid/name resolution and session hook register/unregister."""
import json
import os

from agent_bus.lifecycle import (
    derive_name,
    detect_kind,
    host_pid,
    session_end,
    session_start,
)
from agent_bus.store import get_live_roster, register


def test_detect_kind_prefers_grok_when_both_envs_set(monkeypatch):
    monkeypatch.setenv("GROK_PLUGIN_ROOT", "/plugin")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin")
    assert detect_kind() == "grok"


def test_detect_kind_claude(monkeypatch):
    monkeypatch.delenv("GROK_SESSION_ID", raising=False)
    monkeypatch.delenv("GROK_HOOK_EVENT", raising=False)
    monkeypatch.delenv("GROK_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin")
    assert detect_kind() == "claude"


def test_derive_name_from_session_and_pid():
    assert derive_name("grok", "abcdef1234567890") == "grok-abcdef12"
    assert derive_name("claude", None, pid=4321) == "claude-4321"


def test_host_pid_from_grok_active_sessions(tmp_path, monkeypatch):
    gdir = tmp_path / "grok"
    gdir.mkdir()
    live = os.getpid()
    (gdir / "active_sessions.json").write_text(
        json.dumps([{"session_id": "sess-1", "pid": live, "cwd": "/tmp"}])
    )
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(gdir))
    assert host_pid("grok", session_id="sess-1") == live


def test_host_pid_from_claude_sessions(tmp_path, monkeypatch):
    sdir = tmp_path / "sessions"
    sdir.mkdir()
    live = os.getpid()
    (sdir / f"{live}.json").write_text(
        json.dumps({"pid": live, "sessionId": "cl-1", "name": "my-claude"})
    )
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sdir))
    assert host_pid("claude", session_id="cl-1") == live


def test_session_start_registers_host_pid_not_hook_pid(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    gdir = tmp_path / "grok"
    gdir.mkdir()
    live = os.getpid()
    (gdir / "active_sessions.json").write_text(
        json.dumps([{"session_id": "g-sess", "pid": live, "cwd": str(tmp_path)}])
    )
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(gdir))
    monkeypatch.setenv("GROK_SESSION_ID", "g-sess")
    monkeypatch.setenv("GROK_PLUGIN_ROOT", "/grok/plugin")
    monkeypatch.setenv("GROK_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr("agent_bus.lifecycle.start_uds_listen", lambda *a, **k: None)

    entry = session_start()
    assert entry.kind == "grok"
    assert entry.pid == live
    assert entry.name == "grok-g-sess"
    again = session_start()
    assert again.id == entry.id

def test_session_start_does_not_clobber_a_name_already_claimed_for_this_pid(
    tmp_path, monkeypatch
):
    """#322: an omp session was explicitly registered as `labkit-omp-claude`,
    then its MCP connection respawned (same underlying harness pid, a brand
    new `agent-bus mcp` child) -- and `session_start()`, run fresh on every
    such respawn with no memory of the prior connection, silently reverted
    it to a pid-derived default. `_adopt_identity_from_client` and
    `_adopt_root` (mcp_server.py) both already refuse to touch a name that
    is not still their own derived default; `session_start` was the one
    adoption path missing that guard, and it runs first, before either of
    the other two gets a chance to matter.
    """
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.delenv("GROK_SESSION_ID", raising=False)
    monkeypatch.delenv("GROK_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr("agent_bus.lifecycle.start_uds_listen", lambda *a, **k: None)

    first = session_start()
    assert first.kind == "other", "no adapter recognizes this environment"
    # No adapter for "other" -- describe() falls back to getppid(), not
    # os.getpid(); whatever it resolved is the pid every later call must
    # agree with, not this process's own.
    live = first.pid

    # Explicit claim -- what omp's own MCP client does on connect, and what
    # a human's `agent-bus register` call does too. Either way, session_start
    # must never have the last word over it.
    claimed = register("labkit-omp-claude", "omp", pid=live, home=home)
    assert claimed.id == first.id, "same pid -- a rename in place, not a new entry"

    respawned = session_start()
    assert respawned.id == first.id
    assert respawned.name == "labkit-omp-claude"
    assert respawned.kind == "omp"


def test_session_end_unregisters(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    gdir = tmp_path / "grok"
    gdir.mkdir()
    live = os.getpid()
    (gdir / "active_sessions.json").write_text(
        json.dumps([{"session_id": "g-sess", "pid": live, "cwd": str(tmp_path)}])
    )
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(gdir))
    monkeypatch.setenv("GROK_SESSION_ID", "g-sess")
    monkeypatch.setenv("GROK_PLUGIN_ROOT", "/grok/plugin")
    register("grok-g-sess", "grok", pid=live, home=home)
    assert any(e.name == "grok-g-sess" for e in get_live_roster(home=home))
    assert session_end() is True
    assert not any(e.name == "grok-g-sess" for e in get_live_roster(home=home))




def test_grok_session_start_starts_uds_listen_with_title(tmp_path, monkeypatch):
    from urllib.parse import quote

    home = str(tmp_path / "bus")
    gdir = tmp_path / "grok"
    gdir.mkdir()
    live = os.getpid()
    cwd = str(tmp_path)
    sid = "g-sess"
    (gdir / "active_sessions.json").write_text(
        json.dumps([{"session_id": sid, "pid": live, "cwd": cwd}])
    )
    summary_dir = gdir / "sessions" / quote(cwd, safe="") / sid
    summary_dir.mkdir(parents=True)
    (summary_dir / "summary.json").write_text(
        json.dumps({"generated_title": "exo-grok", "agent_name": "grok-build-plan"})
    )
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(gdir))
    monkeypatch.setenv("GROK_SESSION_ID", sid)
    monkeypatch.setenv("GROK_PLUGIN_ROOT", "/grok/plugin")
    monkeypatch.setenv("GROK_WORKSPACE_ROOT", cwd)
    called = {}

    def fake_start(name, host_pid, **kwargs):
        called["name"] = name
        called["pid"] = host_pid

    monkeypatch.setattr("agent_bus.lifecycle.start_uds_listen", fake_start)
    entry = session_start()
    assert entry.name == "exo-grok"
    assert called == {"name": "exo-grok", "pid": live}


def test_grok_session_end_stops_uds_listen(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("GROK_SESSION_ID", "g-sess")
    monkeypatch.setenv("GROK_PLUGIN_ROOT", "/grok/plugin")
    live = os.getpid()
    gdir = tmp_path / "grok"
    gdir.mkdir()
    (gdir / "active_sessions.json").write_text(
        json.dumps([{"session_id": "g-sess", "pid": live, "cwd": str(tmp_path)}])
    )
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(gdir))
    register("grok-g-sess", "grok", pid=live, home=home)
    stopped = {}

    def fake_stop(host_pid, **kwargs):
        stopped["pid"] = host_pid
        return True

    monkeypatch.setattr("agent_bus.lifecycle.stop_uds_listen", fake_stop)
    assert session_end() is True
    assert stopped.get("pid") == live
