"""CLI smoke tests (no click, use main() + subprocess for integration)."""
import json
import os
import subprocess
import sys

import pytest

from agent_bus.cli import main


def test_cli_list_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    # argparse prints to stderr/stdout then exits 0 for --help? actually 0
    assert exc.value.code == 0


def test_cli_register_and_list(tmp_path, capsys, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)

    rc = main(["register", "--name", "cli-test", "--kind", "other", "--pid", str(os.getpid())])
    assert rc == 0
    out, _err = capsys.readouterr()
    assert "registered" in out

    rc = main(["list", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert any(a.get("name") == "cli-test" for a in data)


def test_cli_send_inbox(tmp_path, capsys, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    child = subprocess.Popen(["sleep", "60"])
    try:
        main(["register", "--name", "s1", "--kind", "other", "--pid", str(os.getpid())])
        main(["register", "--name", "t1", "--kind", "other", "--pid", str(child.pid)])
        rc = main(["send", "t1", "-m", "test msg from cli", "--from-name", "s1"])
        assert rc == 0
        out, _ = capsys.readouterr()
        # send now reports the channel it chose, not just an id
        assert "sent via filebus" in out and "id=" in out

        rc = main(["inbox", "--name", "t1", "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["text"] == "test msg from cli"
    finally:
        child.kill()
        child.wait()


def test_cli_hook_session_start_and_end(tmp_path, capsys, monkeypatch):
    home = str(tmp_path / "bus")
    gdir = tmp_path / "grok"
    gdir.mkdir()
    (gdir / "active_sessions.json").write_text(
        json.dumps([{"session_id": "hook-sess", "pid": os.getpid(), "cwd": str(tmp_path)}])
    )
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(gdir))
    monkeypatch.setenv("GROK_SESSION_ID", "hook-sess")
    monkeypatch.setenv("GROK_PLUGIN_ROOT", "/tmp/gp")
    monkeypatch.setenv("GROK_WORKSPACE_ROOT", str(tmp_path))
    rc = main(["hook", "session-start"])
    assert rc == 0
    out, err = capsys.readouterr()
    combined = out + err
    assert "grok-hook-ses" in combined or "registered" in combined

    rc = main(["list", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert any(a.get("name") == "grok-hook-ses" for a in data)

    rc = main(["hook", "session-end"])
    assert rc == 0
    out, _ = capsys.readouterr()
    rc = main(["list", "--json"])
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert not any(a.get("name") == "grok-hook-ses" for a in data)


def test_cli_subprocess_smoke(tmp_path):
    """End to end via installed script or -m , using temp home."""
    home = str(tmp_path / "bus2")
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = home
    # compute src from test location (tests/.. /src )
    test_dir = os.path.dirname(__file__)
    src_dir = os.path.abspath(os.path.join(test_dir, "..", "src"))
    env["PYTHONPATH"] = src_dir

    # use python -m
    base = [sys.executable, "-m", "agent_bus"]
    cur_pid = str(os.getpid())

    # register under *live* pid (test proc) so list sees it (prune drops only dead)
    r = subprocess.run(
        [*base, "register", "--name", "sub", "--kind", "omp", "--pid", cur_pid],
        env=env, capture_output=True, text=True, cwd=os.path.dirname(test_dir),
    )
    assert r.returncode == 0, f"register failed: {r.stderr}"
    assert "registered" in r.stdout

    # list json (entry still live under test pid)
    r = subprocess.run(
        [*base, "list", "--json"],
        env=env, capture_output=True, text=True, cwd=os.path.dirname(test_dir),
    )
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert any(a.get("name") == "sub" for a in data)
    # self omitted: the pid of the sub-run is not the cur_pid we registered
    # under. register + list via -m is the smoke test.
