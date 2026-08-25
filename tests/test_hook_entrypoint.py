"""The hook entrypoint, under the assumption that it runs in a harness nobody
deliberately installed it into.

Two invariants, both of which the shipped shim broke:

1. Never claim an identity you cannot prove.
2. Never fail or stall the host -- exit 0, diagnostics to stderr, no blocking read.

The bash shims that violated the first are gone; the MCP server does this
in-process and needs none of it. What remains is the CLI escape hatch for a
harness that has hooks and no MCP, and it has to be safe.
"""
import json
import os
import subprocess
import sys
import time

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _run(args, env_extra, stdin=subprocess.DEVNULL, timeout=15):
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC
    for k in ("GROK_PLUGIN_ROOT", "GROK_SESSION_ID", "GROK_HOOK_EVENT",
              "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "agent_bus", *args],
        env=env, stdin=stdin, capture_output=True, text=True, timeout=timeout,
    )


@pytest.fixture
def envs(tmp_path, short_sock_dir):
    return {
        "AGENT_BUS_HOME": str(tmp_path / "bus"),
        "AGENT_BUS_SESSIONS_DIR": str(tmp_path / "sessions"),
        # Short, not tmp_path: see the short_sock_dir fixture.
        "AGENT_BUS_SOCK_DIR": short_sock_dir,
    }


# --- invariant 1: never claim an identity you cannot prove ----------------

def test_a_bare_invocation_is_not_grok(envs, tmp_path):
    """The shim exported GROK_PLUGIN_ROOT on the reasoning that "these hooks
    ship only in the Grok plugin". Inverted: a bare invocation is the one case
    we can be sure is *not* a deliberate Grok install. Verified before the fix:
    an environment with no GROK_* at all registered kind=grok."""
    r = _run(["hook", "session-start"], envs)
    assert r.returncode == 0, r.stderr

    sys.path.insert(0, SRC)
    from agent_bus.store import load_roster

    kinds = {e.kind for e in load_roster(envs["AGENT_BUS_HOME"])}
    assert kinds and "grok" not in kinds, kinds
    assert kinds == {"other"}, kinds


def test_a_real_grok_environment_is_still_grok(envs, tmp_path):
    """Detection, not assumption -- positive evidence still works."""
    gdir = tmp_path / "grok"
    gdir.mkdir()
    (gdir / "active_sessions.json").write_text(json.dumps(
        [{"session_id": "sess-1", "pid": os.getpid(), "cwd": str(tmp_path)}]
    ))
    r = _run(["hook", "session-start"], {
        **envs,
        "AGENT_BUS_GROK_DIR": str(gdir),
        "GROK_PLUGIN_ROOT": str(tmp_path),
        "GROK_SESSION_ID": "sess-1",
        "GROK_WORKSPACE_ROOT": str(tmp_path),
    })
    assert r.returncode == 0, r.stderr

    sys.path.insert(0, SRC)
    from agent_bus.store import load_roster

    assert {e.kind for e in load_roster(envs["AGENT_BUS_HOME"])} == {"grok"}


# --- invariant 2: never stall the host ------------------------------------

def test_a_pipe_nobody_closes_does_not_hang(envs, tmp_path):
    """Grok pipes hook stdin (xai-grok-hooks/src/runner/command.rs:188). A
    plain read of a pipe nobody closes never returns -- the old code sat in it
    until killed, `timeout 6` giving 124."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    # The redirect must happen in the child: evaluating open(fifo, "w") in this
    # process would block before Popen ever ran, deadlocking the test itself.
    writer = subprocess.Popen(["sh", "-c", f"sleep 30 > '{fifo}'"])
    try:
        started = time.monotonic()
        r_fd = os.open(fifo, os.O_RDONLY)
        try:
            r = _run(["hook", "session-start"], envs, stdin=r_fd, timeout=20)
        finally:
            os.close(r_fd)
        elapsed = time.monotonic() - started
        assert r.returncode == 0, r.stderr
        assert elapsed < 10, f"hook blocked on an open pipe ({elapsed:.1f}s)"
    finally:
        writer.kill()
        writer.wait()


def test_a_payload_that_is_written_and_closed_is_still_read(envs):
    """Bounded, not deaf: a harness that actually sends one is honoured."""
    payload = json.dumps({"sessionId": "from-stdin"}).encode()
    r = subprocess.run(
        [sys.executable, "-m", "agent_bus", "hook", "session-start"],
        env={**os.environ, "PYTHONPATH": SRC, **envs},
        input=payload, capture_output=True, timeout=15,
    )
    assert r.returncode == 0


# --- invariant 2: never fail the host -------------------------------------

@pytest.mark.parametrize("event", ["session-start", "session-end"])
def test_the_hook_exits_zero_even_when_core_raises(envs, event, monkeypatch):
    """A messaging bus must never be able to stop a session starting. We do not
    know what an unknown harness does with a non-zero hook exit; in some it is
    a control signal."""
    r = _run(["hook", event], {**envs, "AGENT_BUS_HOME": "/dev/null/impossible"})
    assert r.returncode == 0, (r.returncode, r.stderr)


@pytest.mark.parametrize("event", ["session-start", "session-end"])
def test_the_hook_writes_nothing_to_stdout(envs, event):
    """stdout used to carry Claude's hookSpecificOutput envelope *and* a
    duplicate top-level additionalContext -- a shotgun fired at two schemas. An
    unknown harness may inject stdout verbatim into a model's context."""
    r = _run(["hook", event], envs)
    assert r.stdout == "", r.stdout
    assert "agent-bus" in r.stderr


def test_session_end_on_an_unknown_session_is_not_an_error(envs):
    r = _run(["hook", "session-end"], envs)
    assert r.returncode == 0
    assert "no match" in r.stderr or "unregistered" in r.stderr
