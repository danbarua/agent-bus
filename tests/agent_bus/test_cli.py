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


def test_cli_help_verb_prints_root_help(capsys):
    assert main(["help"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "usage: agent-bus" in out


def test_cli_help_verb_prints_subcommand_help(capsys):
    assert main(["help", "send"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "usage: agent-bus send" in out
    assert "--message" in out


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


def test_cli_join_publishes_a_reachable_listener(short_sock_dir, capsys, monkeypatch, tmp_path):
    """#159/#160: a CLI-only harness (omp, pi) needs `register` plus a bound
    listener, blocking until it's actually reachable -- `join` (commands/
    agents.py) already does both; this is it reaching a shell caller."""
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    holder = subprocess.Popen(["sleep", "30"])
    try:
        rc = main(["join", "--name", "join-test", "--kind", "omp",
                   "--pid", str(holder.pid), "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        entry = json.loads(out)
        assert entry["reachable"] is True
        assert entry["kind"] == "omp"
        assert os.path.exists(os.path.join(short_sock_dir, f"{holder.pid}.sock")) or any(
            f.endswith(".sock") for f in os.listdir(short_sock_dir)
        ), "join reported reachable but no socket was ever bound"

        rc = main(["list", "--json"])
        out, _ = capsys.readouterr()
        assert any(a["name"] == "join-test" for a in json.loads(out))
    finally:
        holder.kill()
        holder.wait()


def test_cli_join_refuses_when_no_session_pid_can_be_resolved(tmp_path, capsys, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    # Isolated from real discovery too -- resolve_host_pid's last resort before
    # refusing is asking the harness what session this process runs inside,
    # and this test process really does run inside one. Pointed at a directory
    # with nothing published in it, that path finds nothing either, and the
    # refusal this test is about is reached honestly.
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    rc = main(["join", "--name", "orphan-join", "--kind", "other"])
    assert rc == 1
    _out, err = capsys.readouterr()
    assert "cannot tell which process is the session" in err


def test_cli_leave_tears_down_the_listener_started_by_join(
    short_sock_dir, capsys, monkeypatch, tmp_path
):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    holder = subprocess.Popen(["sleep", "30"])
    try:
        main(["join", "--name", "leave-test", "--kind", "omp", "--pid", str(holder.pid)])
        capsys.readouterr()

        rc = main(["leave", "--name", "leave-test", "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        assert json.loads(out) == {"left": True}

        rc = main(["list", "--json"])
        out, _ = capsys.readouterr()
        assert not any(a["name"] == "leave-test" for a in json.loads(out))
    finally:
        holder.kill()
        holder.wait()


def test_cli_send_json_reports_delivery_and_id(tmp_path, capsys, monkeypatch):
    """#112: a script needs `delivery`/`id`, not just "sent to X" prose."""
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    child = subprocess.Popen(["sleep", "30"])
    try:
        main(["register", "--name", "json-target", "--kind", "other", "--pid", str(child.pid)])
        capsys.readouterr()
        rc = main(["send", "json-target", "-m", "hi", "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert set(data) == {"to", "delivery", "id"}
        assert data["to"] == "json-target"
        assert data["id"]
    finally:
        child.kill()
        child.wait()


def test_cli_register_json_reports_the_name_actually_claimed(tmp_path, capsys, monkeypatch):
    """register() renames on collision (`name` -> `name-2`); a scripted caller
    reading only exit code + stdout prose had no way to learn that happened."""
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    holder = subprocess.Popen(["sleep", "30"])
    other = subprocess.Popen(["sleep", "30"])
    try:
        main(["register", "--name", "taken", "--kind", "other", "--pid", str(holder.pid)])
        capsys.readouterr()
        rc = main(["register", "--name", "taken", "--kind", "other", "--pid",
                   str(other.pid), "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert data["name"] == "taken-2", data
        assert data["registered"] is True
    finally:
        holder.kill()
        holder.wait()
        other.kill()
        other.wait()


def test_cli_ack_json_reports_acked(tmp_path, capsys, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    child = subprocess.Popen(["sleep", "30"])
    try:
        main(["register", "--name", "ack-target", "--kind", "other", "--pid", str(child.pid)])
        main(["send", "ack-target", "-m", "ack me"])
        capsys.readouterr()
        main(["inbox", "--name", "ack-target", "--json"])
        out, _ = capsys.readouterr()
        msg_id = json.loads(out)[0]["id"]

        rc = main(["ack", msg_id, "--name", "ack-target", "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        assert json.loads(out) == {"acked": True}

        rc = main(["ack", "not-a-real-id", "--name", "ack-target", "--json"])
        assert rc == 1
        out, _ = capsys.readouterr()
        assert json.loads(out) == {"acked": False}
    finally:
        child.kill()
        child.wait()


def test_cli_orphans_json_is_the_lossless_list(tmp_path, capsys, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    from agent_bus import store
    from agent_bus.protocol import now_iso

    store.ensure_dirs(home)
    entry_id = "claude:orphan-json-test"
    path = store._inbox_path_for(entry_id, home)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": "m-1", "ts": now_iso(),
            "from": {"id": "s", "name": "sender", "kind": "other"},
            "to": {"id": entry_id, "name": "orphan-json-test"},
            "summary": "", "text": "stranded", "replyTo": None, "read": False,
        }) + "\n")

    rc = main(["orphans", "--json"])
    assert rc == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert any(o["id"] == entry_id and o["unread"] == 1 and o["total"] == 1 for o in data), data


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
        # The text form is for a reader: it says it went and to whom. The
        # transport and the message id live in --json, where a caller that
        # actually wants the mechanism can ask for them.
        assert "sent to" in out

        rc = main(["inbox", "--name", "t1", "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["text"] == "test msg from cli"

        # #152: `read` is the CLI half of the same gap MCP had -- one message,
        # whole, by the id a notice gave. An 8-char prefix is what `watch`
        # actually hands a reader, so that is what gets exercised here.
        full_id = data[0]["id"]
        rc = main(["read", full_id[:8], "--name", "t1"])
        assert rc == 0
        out, _ = capsys.readouterr()
        assert "test msg from cli" in out

        rc = main(["read", "not-a-real-id", "--name", "t1"])
        assert rc == 1
        out, err = capsys.readouterr()
        assert "no such message" in err
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
