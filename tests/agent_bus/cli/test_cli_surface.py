"""Every CLI verb runs, and the --json surface keeps its shape.

Two claims:

**Every verb starts.** A verb whose module-level code is fine but whose body
raises on the first line looks healthy to every unit test in this repo -- the
CLI imports lazily inside each command function, so nothing resolves those
imports until the command runs. This invokes each one as a subprocess and fails
on a Python traceback.

**The --json surface is a contract.** Agents parse `list --json`, `self --json`
and `inbox --json`. If a key an agent needs disappears, or an internal one
appears, that is a break in the public shape whatever the unit tests say.

These live outside the opt-in suites because they need no credentials, no model
and no network. Gating them behind AGENT_BUS_RUN_SPENDY_E2E_TESTS would mean the build
never ran them, which is the opposite of the point.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Args that make each verb do something real. A verb that errors is fine -- the
# claim is that it runs, not that it succeeds with no bus to talk to.
TERMINATING: list[tuple[str, list[str]]] = [
    ("list", []),
    ("list", ["--json"]),
    ("list", ["--kind", "all"]),
    ("send", ["nobody-at-all", "-m", "hello"]),
    ("inbox", ["--address", "nobody-at-all"]),
    ("inbox", ["--json"]),
    ("ack", ["not-a-real-message-id"]),
    ("register", ["--name", "verb-probe", "--kind", "other"]),
    ("self", []),
    ("self", ["--json"]),
    ("status", ["idle"]),
    ("unregister", ["--name", "verb-probe"]),
    ("reap", []),
    ("reap", ["--older-than", "7200"]),
    ("orphans", []),
    ("grok-status", []),
    ("hook", ["session-start"]),
    ("hook", ["session-end"]),
]

# Verbs that run until killed. Starting is the whole assertion: these are the
# ones no other test invokes, and where a broken import stays hidden.
BLOCKING: list[tuple[str, list[str]]] = [
    ("listen", ["--name", "verb-probe"]),
    ("watch", ["--address", "verb-probe"]),
    ("mcp", []),
]

TRACEBACK = "Traceback (most recent call last)"


@pytest.fixture
def env(tmp_path, short_sock_dir):
    """A bus, a sessions dir and a socket dir of this test's own.

    listen and mcp both publish a session file and bind a socket. Without
    the overrides they would write into the developer's real ~/.claude/sessions
    and /tmp/cc-socks, and then discover their own handiwork.
    """
    return {
        **os.environ,
        "AGENT_BUS_HOME": str(tmp_path / "bus"),
        "AGENT_BUS_SESSIONS_DIR": str(tmp_path / "sessions"),
        "AGENT_BUS_SOCK_DIR": short_sock_dir,
        "AGENT_BUS_SPOOL_DIR": str(tmp_path / "spool"),
    }


def _run(env, *args, stdin: str = "", timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agent_bus", *args],
        cwd=REPO, env=env, input=stdin,
        capture_output=True, text=True, timeout=timeout,
    )


# --------------------------------------------------------------- every verb


@pytest.mark.parametrize(
    ("verb", "args"), TERMINATING, ids=[f"{v} {' '.join(a)}".strip() for v, a in TERMINATING]
)
def test_a_terminating_verb_runs(env, verb, args):
    """Exit codes vary legitimately -- `ack` of an unknown id returns 1, `self`
    returns 1 when nothing is registered. A traceback never does."""
    r = _run(env, verb, *args)
    assert TRACEBACK not in r.stderr, f"{verb} crashed:\n{r.stderr[-1500:]}"


@pytest.mark.parametrize(
    ("verb", "args"), BLOCKING, ids=[f"{v} {' '.join(a)}".strip() for v, a in BLOCKING]
)
def test_a_blocking_verb_starts(env, verb, args):
    """Start it, give it a moment, then stop it and read what it managed to say.

    A verb that dies on an import raises within milliseconds, so anything still
    running has got past its own startup. One that exits on its own must still
    not have left a traceback.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_bus", verb, *args],
        cwd=REPO, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        # A broken import raises during startup, well inside this window; a
        # healthy one is still running when it closes. Kept short because a
        # healthy verb waits it out, five times over.
        deadline = time.time() + 1.5
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.1)
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            out = proc.communicate(timeout=10)[0] or ""
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0] or ""
    assert TRACEBACK not in out, f"{verb} crashed on startup:\n{out[-1500:]}"


# ----------------------------------------------------------- a real lifecycle


def test_the_verbs_work_together_on_one_bus(env, tmp_path):
    """Register, send, read, ack -- the sequence an agent actually performs.

    Each verb passing alone does not mean they agree about the bus between
    them: a name claimed by one has to be addressable by the next.
    """
    alice_proc = subprocess.Popen(["sleep", "30"])
    bob_proc = subprocess.Popen(["sleep", "30"])
    try:
        r = _run(env, "register", "--name", "alice", "--kind", "other",
                 "--pid", str(alice_proc.pid))
        assert r.returncode == 0, r.stderr
        assert "alice" in r.stdout

        assert _run(env, "register", "--name", "bob", "--kind", "other",
                    "--pid", str(bob_proc.pid)).returncode == 0

        r = _run(env, "send", "bob", "-m", "the patch is ready", "--from-name", "alice")
        assert r.returncode == 0, r.stderr
        assert "bob" in r.stdout

        msgs = json.loads(_run(env, "inbox", "--address", "bob", "--json").stdout)
        assert [m["text"] for m in msgs] == ["the patch is ready"]
        assert msgs[0]["from"]["name"] == "alice"
        assert msgs[0]["read"] is False

        r = _run(env, "ack", msgs[0]["id"], "--address", "bob")
        assert r.returncode == 0, r.stderr

        after = json.loads(_run(env, "inbox", "--address", "bob", "--json").stdout)
        assert after[0]["read"] is True, "ack did not stick across processes"

        unread = _run(env, "inbox", "--address", "bob", "--unread", "--json").stdout
        assert unread.strip() in ("[]", "")
    finally:
        for p in (alice_proc, bob_proc):
            p.kill()
            p.wait()


def test_sending_to_nobody_fails_loudly(env):
    """Silence would be worse than an error: a sender told nothing would assume
    it went."""
    r = _run(env, "send", "ghost", "-m", "hello")
    assert r.returncode != 0
    assert "ghost" in r.stderr


# ------------------------------------------------------------ the --json shape


# What an agent needs in order to address another agent, and what it must never
# be handed: paths into the bus's own storage and the internal liveness guard.
ROSTER_REQUIRED = {"id", "name", "kind", "status", "aliases"}
INTERNAL = {"inbox", "native", "procStart", "transport", "socket"}


def test_list_json_gives_an_address_and_no_internals(env):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        _run(env, "register", "--name", "alice", "--kind", "other", "--pid", str(holder.pid))
        rows = json.loads(_run(env, "list", "--json").stdout)
        alice = next(a for a in rows if a["name"] == "alice")

        assert set(alice) >= ROSTER_REQUIRED, (
            f"cannot address this agent with {sorted(alice)}"
        )
        assert not set(alice) & INTERNAL, (
            f"list --json exposes {sorted(set(alice) & INTERNAL)}"
        )
    finally:
        holder.kill()
        holder.wait()


def test_self_json_matches_the_list_row(env):
    """One agent described by two surfaces, which must agree.

    Registered under this test's own pid: `self` finds an agent by walking the
    ancestors of the process asking, so a subprocess only sees the entry if the
    pid it was registered under is one of its parents.
    """
    _run(env, "register", "--name", "alice", "--kind", "other", "--pid", str(os.getpid()))

    me = json.loads(_run(env, "self", "--json").stdout)
    assert me["name"] == "alice"
    assert not set(me) & INTERNAL, f"self --json exposes {sorted(set(me) & INTERNAL)}"

    rows = json.loads(_run(env, "list", "--json").stdout)
    alice = next(a for a in rows if a["name"] == "alice")
    shared = (set(me) & set(alice)) - {"registered"}
    assert {k: me[k] for k in shared} == {k: alice[k] for k in shared}


def test_inbox_json_carries_a_reply_address(env):
    """A message you cannot answer is half a message: the sender has to be
    addressable from what arrives with it."""
    alice_proc = subprocess.Popen(["sleep", "30"])
    bob_proc = subprocess.Popen(["sleep", "30"])
    try:
        _run(env, "register", "--name", "alice", "--kind", "other",
             "--pid", str(alice_proc.pid))
        _run(env, "register", "--name", "bob", "--kind", "other",
             "--pid", str(bob_proc.pid))
        _run(env, "send", "bob", "-m", "hello", "--from-name", "alice")

        msg = json.loads(_run(env, "inbox", "--address", "bob", "--json").stdout)[0]
        assert {"id", "ts", "from", "to", "text", "read"} <= set(msg)
        assert {"id", "name"} <= set(msg["from"])

        back = _run(env, "send", msg["from"]["name"], "-m", "got it", "--from-name", "bob")
        assert back.returncode == 0, f"could not reply to the sender: {back.stderr}"
    finally:
        for p in (alice_proc, bob_proc):
            p.kill()
            p.wait()
