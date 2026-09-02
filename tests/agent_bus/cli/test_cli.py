"""CLI smoke tests (no click, use main() + subprocess for integration)."""
import contextlib
import json
import logging
import os
import subprocess
import sys
import time

import pytest
from waiting import wait_until_gone

from agent_bus import log, store
from agent_bus.cli import main
from agent_bus.protocol import AgentTarget


def _reset_log_handlers():
    """Undo a test's own `log.configure(force=True)`, so its handler --
    pointed at that test's own tmp_path -- does not outlive it and swallow
    or misdirect records for whatever test runs next in this process."""
    for h in list(logging.getLogger(log.LOGGER_NAME).handlers):
        h.close()
        logging.getLogger(log.LOGGER_NAME).removeHandler(h)


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

        # The listener `join` spawned, read before it is asked to stop. The
        # roster listing below is a *proxy* for teardown; this is the thing
        # itself, and the name of this test is about the listener.
        pid_file = os.path.join(home, "listeners", f"{holder.pid}.pid")
        listener_pid = int(open(pid_file).read().strip())

        rc = main(["leave", "--name", "leave-test", "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        assert json.loads(out) == {"left": True}

        # Waited, because `stop_uds_listen` returns on the line after the
        # SIGTERM and the listener exits on its own schedule -- asserting
        # immediately failed a gate run under load.
        #
        # On the listener's **own** cleanup, not on the roster listing. An
        # earlier attempt waited for the name to leave `list`, and that passed
        # with the SIGTERM removed entirely: given ten seconds something else
        # tidied the entry, so the wait masked the defect the test exists for.
        #
        # Not `os.kill(pid, 0)` either -- the listener is a child of this
        # process, so after SIGTERM it is a zombie until reaped and signal 0
        # still succeeds against it.
        #
        # `uds.py`'s `_atexit` unlinks this socket, so it goes when the
        # listener genuinely exits and stays forever when nothing signals it.
        sock = os.path.join(short_sock_dir, f"{listener_pid}.sock")
        wait_until_gone(lambda: os.path.exists(sock),
                        f"listener {listener_pid} to remove {sock}")

        main(["list", "--json"])
        listed, _ = capsys.readouterr()
        assert not any(a["name"] == "leave-test" for a in json.loads(listed))
    finally:
        holder.kill()
        holder.wait()


def test_cli_leave_resolves_the_listener_pid_from_the_roster_not_the_flag(
    short_sock_dir, capsys, monkeypatch, tmp_path
):
    """A `--pid` that doesn't match what `join` actually registered under
    must not leave that listener running -- `leave` looks the real pid up
    from the roster entry itself rather than trusting the flag.

    Checked via the socket, not the pid: `stop_uds_listen` unlinks the
    `listeners/<host>.pid` file itself regardless of whether the signal
    actually reached anything, and `os.kill(pid, 0)` reports a reaped
    zombie as alive for as long as this test process outlives it without
    ever calling wait() on it -- neither is evidence the listener process
    is gone. The socket is: the listener's own signal handler removes it,
    and it is the one thing a real sender's `send` would actually depend
    on if this bug reopened.

    This test is also what found a second, unrelated bug in `run_listen`
    while it was flaking on the wrong theory: the signal handler used to
    install *after* the adopt-wait loop and registration, seconds after
    `bind()` -- but `bind()` is what `join`'s `_wait_until_reachable` polls
    for, so `reachable: True` could come back for a socket a SIGTERM would
    still kill under Python's default disposition, no cleanup, socket left
    on disk. `join` immediately followed by `leave` hit that window close
    to every time (~60-70% locally). Fixed in `uds.py` by installing the
    handler right after `bind()`/`listen()` instead.
    """
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    holder = subprocess.Popen(["sleep", "30"])
    try:
        main(["join", "--name", "wrong-pid-test", "--kind", "omp", "--pid", str(holder.pid)])
        capsys.readouterr()

        # start_uds_listen names this file for the HOST pid we joined with,
        # not the listener's own -- the listener's pid is its contents, and
        # the listener publishes its socket under that same pid.
        pid_path = os.path.join(home, "listeners", f"{holder.pid}.pid")
        listener_pid = int(open(pid_path).read().strip())
        sock_path = os.path.join(short_sock_dir, f"{listener_pid}.sock")
        assert os.path.exists(sock_path), "join did not actually bind a socket"

        rc = main(["leave", "--name", "wrong-pid-test", "--pid", "999999", "--json"])
        assert rc == 0

        for _ in range(50):
            if not os.path.exists(sock_path):
                break
            time.sleep(0.1)
        assert not os.path.exists(sock_path), (
            f"{sock_path} still exists after leave with the wrong --pid -- "
            "the roster's own pid should have been used instead"
        )
    finally:
        holder.kill()
        holder.wait()


def test_cli_leave_stops_a_hand_started_listener_by_its_correct_host_pid(
    short_sock_dir, monkeypatch, tmp_path
):
    """A hand-started `agent-bus listen --pid HOST`, with nothing registered
    for HOST yet, is a different shape from `join`: `run_listen`'s adopt
    loop finds no existing entry (`--adopt` is internal-only, never passed
    here) and registers fresh under its OWN pid -- not the host's. So the
    roster entry's pid is the listener's, while the `.pid` file `leave`
    needs is still keyed by the host pid the caller actually knows.

    Trusting the roster's pid alone here (an earlier version of this fix)
    reintroduced the exact bug it was meant to close: `stop_uds_listen`
    would look for a pid file keyed by the roster's pid, find nothing, and
    the caller's correct `--pid` -- the one that would have worked -- was
    never tried. `leave` must fall back to `host_pid` whenever stopping by
    the roster's pid found nothing to stop, not only when the roster has no
    pid at all -- and it must not warn when `host_pid` was the pid that
    actually worked.
    """
    home = str(tmp_path / "bus")
    log_file = str(tmp_path / "agent-bus.jsonl")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", log_file)
    monkeypatch.delenv("AGENT_BUS_LOG_LEVEL", raising=False)
    # main() calls log.configure() without force=True, so an earlier test in
    # this same process already locked in a handler pointed elsewhere.
    log.configure(force=True)
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = home
    env["AGENT_BUS_SOCK_DIR"] = short_sock_dir
    env["AGENT_BUS_SESSIONS_DIR"] = str(tmp_path / "sessions")
    env["AGENT_BUS_LOG_FILE"] = log_file

    host = subprocess.Popen(["sleep", "30"])
    listener = subprocess.Popen(
        [sys.executable, "-m", "agent_bus", "listen", "--name", "handrun",
         "--pid", str(host.pid)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        pid_path = os.path.join(home, "listeners", f"{host.pid}.pid")
        for _ in range(50):
            if os.path.exists(pid_path):
                break
            time.sleep(0.1)
        assert os.path.exists(pid_path), "listener never registered under its host pid"
        listener_pid = int(open(pid_path).read().strip())
        sock_path = os.path.join(short_sock_dir, f"{listener_pid}.sock")
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.1)
        assert os.path.exists(sock_path), "listener never bound a socket"

        rc = main(["leave", "--name", "handrun", "--pid", str(host.pid), "--json"])
        assert rc == 0

        for _ in range(50):
            if not os.path.exists(sock_path):
                break
            time.sleep(0.1)
        assert not os.path.exists(sock_path), (
            f"{sock_path} still exists after leave with the CORRECT host "
            "pid -- the roster's own (listener) pid must not have been "
            "tried first and left uncorrected"
        )

        recs = [json.loads(ln) for ln in open(log_file) if ln.strip()]
        warnings = [r for r in recs if r.get("message", "").startswith("leave:")]
        assert warnings == [], (
            "a correct --pid must not warn just because it differs from "
            "the roster's (listener) pid"
        )
    finally:
        with contextlib.suppress(Exception):
            listener.kill()
            listener.wait(timeout=5)
        host.kill()
        host.wait()
        _reset_log_handlers()


def test_cli_leave_stops_a_hand_started_listener_with_no_pid_at_all(
    short_sock_dir, monkeypatch, tmp_path
):
    """The sibling of the test above, and the case it does not cover.

    Falling back to `host_pid` fixes the hand-started shape only for a caller
    who already knows the host pid -- and that caller did not need the help.
    `agent-bus leave --name X`, the form the CLI documents and the ordinary
    one to type, supplies no pid at all: the roster's pid is the listener's,
    stops nothing, and there is no second candidate to try. It unregistered
    the name and reported success while the listener kept its socket bound.

    Nothing has to be written to close that. `listeners/<host_pid>.pid` is
    NAMED by the host pid and CONTAINS the listener pid, so the roster's one
    fact recovers the caller's missing one.
    """
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = home
    env["AGENT_BUS_SOCK_DIR"] = short_sock_dir
    env["AGENT_BUS_SESSIONS_DIR"] = str(tmp_path / "sessions")

    host = subprocess.Popen(["sleep", "30"])
    listener = subprocess.Popen(
        [sys.executable, "-m", "agent_bus", "listen", "--name", "nopid",
         "--pid", str(host.pid)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        pid_path = os.path.join(home, "listeners", f"{host.pid}.pid")
        for _ in range(50):
            if os.path.exists(pid_path):
                break
            time.sleep(0.1)
        assert os.path.exists(pid_path), "listener never registered under its host pid"
        listener_pid = int(open(pid_path).read().strip())
        # The divergence this test exists for. If these were equal the roster's
        # pid would already work and the case would not arise.
        assert listener_pid != host.pid
        sock_path = os.path.join(short_sock_dir, f"{listener_pid}.sock")
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.1)
        assert os.path.exists(sock_path), "listener never bound a socket"

        # `run_listen` writes the pid file and binds the socket BEFORE it
        # registers, so a socket on disk does not yet mean a roster entry. Act
        # on the state under test, not on the one that arrives first.
        for _ in range(80):
            if (e := store.find_entry(AgentTarget("nopid"))) is not None and e.pid:
                break
            time.sleep(0.1)
        assert e is not None and e.pid, "listener never registered"
        assert e.pid == listener_pid, (
            f"roster pid {e.pid} is not the listener's {listener_pid}; the "
            "divergence this test exists for is not present"
        )

        rc = main(["leave", "--name", "nopid", "--json"])
        assert rc == 0

        for _ in range(50):
            if not os.path.exists(sock_path):
                break
            time.sleep(0.1)
        assert not os.path.exists(sock_path), (
            f"{sock_path} still exists after `leave --name nopid` with no "
            "--pid. The name is gone from the roster and the process still "
            "holds the socket: a peer that is bound, unaddressable, and "
            "invisible to the listing that would have found it."
        )
    finally:
        with contextlib.suppress(Exception):
            listener.kill()
            listener.wait(timeout=5)
        host.kill()
        host.wait()


def test_cli_leave_with_a_wrong_pid_logs_a_warning(short_sock_dir, capsys, monkeypatch, tmp_path):
    """The system corrects a mismatched `--pid` rather than failing on it
    (previous test), but the mismatch itself is a symptom worth a record --
    it did not exist anywhere before this. Silent for the ordinary CLI case
    (no `--pid` given at all): `cmd_leave` used to fill that in with its own
    pid before calling `agents.leave`, which made every plain `agent-bus
    leave` -- a fresh process leaving on behalf of whatever `join`ed earlier
    -- look exactly like a caller passing a wrong pid on purpose.
    """
    home = str(tmp_path / "bus")
    log_file = str(tmp_path / "agent-bus.jsonl")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", log_file)
    monkeypatch.delenv("AGENT_BUS_LOG_LEVEL", raising=False)
    # main() calls log.configure() without force=True, so an earlier test in
    # this same process already locked in a handler pointed elsewhere.
    log.configure(force=True)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        main(["join", "--name", "warn-test", "--kind", "omp", "--pid", str(holder.pid)])
        capsys.readouterr()

        main(["leave", "--name", "warn-test", "--pid", "999999", "--json"])

        recs = [json.loads(ln) for ln in open(log_file) if ln.strip()]
        warnings = [r for r in recs if r.get("message", "").startswith("leave:")]
        assert len(warnings) == 1, warnings
        assert warnings[0]["severity"] == "WARNING"
        assert warnings[0]["host_pid"] == 999999
        assert warnings[0]["roster_pid"] == holder.pid
    finally:
        holder.kill()
        holder.wait()
        _reset_log_handlers()


def test_cli_leave_with_no_pid_flag_logs_nothing(short_sock_dir, capsys, monkeypatch, tmp_path):
    """The ordinary case -- explicitly, since it is the one the bug above
    broke: a fresh `agent-bus leave` with no `--pid` at all must not warn
    just because this process's own pid differs from the one `join`
    registered under."""
    home = str(tmp_path / "bus")
    log_file = str(tmp_path / "agent-bus.jsonl")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", short_sock_dir)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", log_file)
    monkeypatch.delenv("AGENT_BUS_LOG_LEVEL", raising=False)
    log.configure(force=True)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        main(["join", "--name", "quiet-test", "--kind", "omp", "--pid", str(holder.pid)])
        capsys.readouterr()

        main(["leave", "--name", "quiet-test", "--json"])

        recs = []
        if os.path.exists(log_file):
            recs = [json.loads(ln) for ln in open(log_file) if ln.strip()]
        warnings = [r for r in recs if r.get("message", "").startswith("leave:")]
        assert warnings == []
    finally:
        holder.kill()
        holder.wait()
        _reset_log_handlers()


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
        main(["inbox", "--target", "ack-target", "--json"])
        out, _ = capsys.readouterr()
        msg_id = json.loads(out)[0]["id"]

        rc = main(["ack", msg_id, "--target", "ack-target", "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        assert json.loads(out) == {"acked": True}

        rc = main(["ack", "not-a-real-id", "--target", "ack-target", "--json"])
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

        rc = main(["inbox", "--target", "t1", "--json"])
        assert rc == 0
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert len(data) == 1
        assert data[0]["text"] == "test msg from cli"

        # #152: `read` is the CLI half of the same gap MCP had -- one message,
        # whole, by the id a notice gave. An 8-char prefix is what `watch`
        # actually hands a reader, so that is what gets exercised here.
        full_id = data[0]["id"]
        rc = main(["read", full_id[:8], "--target", "t1"])
        assert rc == 0
        out, _ = capsys.readouterr()
        assert "test msg from cli" in out

        rc = main(["read", "not-a-real-id", "--target", "t1"])
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
    src_dir = os.path.abspath(os.path.join(test_dir, "../..", "src"))
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
