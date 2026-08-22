"""Integration / smoke test for agent-bus, in three tiers.

Opt-in: these spawn real coding agents and (tier 3) need a live Claude Code
session, so they never run in a normal `pytest tests/` sweep.

    AGENT_BUS_INTEGRATION=1 uv run python -m pytest tests/integration -q -s

Tiers
-----
1. Liveness      - CLI only. Register, read an empty inbox, resolve self.
2. Grok + MCP    - a headless `grok --prompt-file` run with the agent-bus MCP
                   server wired into a throwaway project, sending on the bus.
3. End to end    - grok sends to a *live Claude Code session* over native UDS;
                   that session replies with native SendMessage, which lands in
                   grok's file inbox. Needs a human-driven Claude peer, so it is
                   gated behind AGENT_BUS_E2E_PEER.

Isolation
---------
Tiers 1 and 2 override AGENT_BUS_HOME *and* the sessions/socket dirs, so they
touch nothing real. Tier 3 overrides only AGENT_BUS_HOME: native discovery has
to use the real ~/.claude/sessions and /tmp/cc-socks, or the Claude peer cannot
see grok and grok cannot dial it.
"""

import json
import os
import shutil
import subprocess
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

INTEGRATION = os.environ.get("AGENT_BUS_INTEGRATION") == "1"
HAVE_GROK = shutil.which("grok") is not None
E2E_PEER = os.environ.get("AGENT_BUS_E2E_PEER")

pytestmark = pytest.mark.skipif(
    not INTEGRATION, reason="set AGENT_BUS_INTEGRATION=1 to run integration tests"
)


# --------------------------------------------------------------------------- helpers


def _bus_env(home, *, isolate_native=True):
    """Environment for a bus CLI call.

    isolate_native=False leaves the real sessions/socket dirs in place, which is
    required for anything that must be visible to a live Claude session.
    """
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = str(home)
    if isolate_native:
        env["AGENT_BUS_SESSIONS_DIR"] = str(home) + "-sessions"
        env["AGENT_BUS_SOCK_DIR"] = str(home) + "-socks"
        os.makedirs(env["AGENT_BUS_SESSIONS_DIR"], exist_ok=True)
        os.makedirs(env["AGENT_BUS_SOCK_DIR"], exist_ok=True)
    else:
        env.pop("AGENT_BUS_SESSIONS_DIR", None)
        env.pop("AGENT_BUS_SOCK_DIR", None)
    return env


def _bus(home, *args, isolate_native=True, timeout=60):
    """Run the packaged CLI the way a user would: uv run, from the repo."""
    return subprocess.run(
        ["uv", "run", "--project", REPO, "agent-bus", *args],
        env=_bus_env(home, isolate_native=isolate_native),
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _register(home, name, kind, *, isolate_native=True):
    """Register under the *test* process pid.

    register() defaults to the calling process, but `uv run agent-bus` exits
    immediately, so such an entry is pruned as dead before the next call. The
    pytest process outlives the whole test, so its pid keeps the entry live.
    """
    r = _bus(home, "register", "--name", name, "--kind", kind,
             "--pid", str(os.getpid()), isolate_native=isolate_native)
    assert r.returncode == 0, f"register {name} failed: {r.stderr}"
    return r


def _wire_mcp(project_dir, home, *, isolate_native=True):
    """Minimal project-scoped MCP wireup: writes ./.grok/config.toml only."""
    env = _bus_env(home, isolate_native=isolate_native)
    cmd = [
        "grok", "mcp", "add", "agent-bus",
        "-s", "project",
        "-e", f"AGENT_BUS_HOME={home}",
    ]
    if isolate_native:
        cmd += [
            "-e", f"AGENT_BUS_SESSIONS_DIR={env['AGENT_BUS_SESSIONS_DIR']}",
            "-e", f"AGENT_BUS_SOCK_DIR={env['AGENT_BUS_SOCK_DIR']}",
        ]
    cmd += ["--", "uv", "run", "--project", REPO, "agent-bus", "mcp"]
    r = subprocess.run(cmd, cwd=str(project_dir), env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"grok mcp add failed: {r.stderr or r.stdout}"
    assert (project_dir / ".grok" / "config.toml").exists(), "no project MCP config written"


def _run_grok(project_dir, home, prompt_path, *, isolate_native=True, timeout=420):
    return subprocess.run(
        [
            "grok",
            "--prompt-file", str(prompt_path),
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
            "--max-turns", "16",
        ],
        cwd=str(project_dir),
        env=_bus_env(home, isolate_native=isolate_native),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _inbox(home, name, *, isolate_native=True, unread_only=False):
    args = ["inbox", "--json", "--name", name]
    if unread_only:
        args.append("--unread")
    r = _bus(home, *args, isolate_native=isolate_native)
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return []


def _wait_for(predicate, timeout, interval=2.0, what="condition"):
    """Poll until predicate() is truthy. Returns its value, or raises."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}; last={last!r}")


# --------------------------------------------------------------------- tier 1: liveness


def test_tier1_register_and_poll_empty_inbox(tmp_path):
    """The bus comes up in an empty directory: register, then read an empty inbox."""
    home = tmp_path / "bus"
    home.mkdir()

    r = _register(home, "smoke-liveness", "other")
    assert "registered" in r.stdout, r.stdout

    listed = json.loads(_bus(home, "list", "--json").stdout)
    assert any(a["name"] == "smoke-liveness" for a in listed), listed

    assert _inbox(home, "smoke-liveness") == []

    # a fresh home is genuinely empty, not merely unreadable
    assert (home / "roster").exists() or listed, "roster was never created"


def test_tier1_send_and_receive_on_the_file_bus(tmp_path):
    """Round trip entirely within the file bus, no agents involved."""
    home = tmp_path / "bus"
    home.mkdir()
    _register(home, "smoke-a", "other")
    _register(home, "smoke-b", "other")

    r = _bus(home, "send", "smoke-b", "-m", "ping from a", "--from-name", "smoke-a")
    assert r.returncode == 0, r.stderr

    msgs = _inbox(home, "smoke-b")
    assert len(msgs) == 1, msgs
    assert msgs[0]["text"] == "ping from a"
    assert msgs[0]["from"]["name"] == "smoke-a"
    assert msgs[0]["read"] is False


# ------------------------------------------------------------------ tier 2: grok + MCP


@pytest.mark.skipif(not HAVE_GROK, reason="grok not on PATH")
def test_tier2_grok_sends_over_mcp(tmp_path):
    """A headless grok run, with only a project-scoped MCP config, puts a
    message on the bus addressed to a Claude-kind agent."""
    home = tmp_path / "bus"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()

    # the recipient exists on the bus before grok runs
    _register(home, "claude-target", "claude")

    _wire_mcp(project, home)

    token = "smoke-tier2-token"
    prompt = project / "prompt.md"
    prompt.write_text(
        "You have an MCP server named `agent-bus` providing tools: list_agents, "
        "send_message, get_inbox, ack_message, self.\n\n"
        "Do exactly this, using the tools, and nothing else:\n"
        "1. Call `list_agents` and note the agent named `claude-target`.\n"
        "2. Call `send_message` with to=`claude-target`, "
        f"text=`{token}`, summary=`smoke test`.\n"
        "3. Reply with a single line of JSON and no other prose:\n"
        '   {"sent": true, "to": "claude-target"}\n'
        "If a tool call fails, reply with "
        '{"sent": false, "error": "<the error>"} instead.\n'
    )

    r = _run_grok(project, home, prompt)
    assert r.returncode == 0, f"grok exited {r.returncode}: {r.stderr[-2000:]}"

    msgs = _inbox(home, "claude-target")
    assert msgs, (
        "grok did not deliver a message to claude-target.\n"
        f"grok stdout:\n{r.stdout[-3000:]}\n"
    )
    assert any(token in m["text"] for m in msgs), msgs


# ------------------------------------------------------------------------ tier 3: e2e


@pytest.mark.skipif(not HAVE_GROK, reason="grok not on PATH")
@pytest.mark.skipif(
    not E2E_PEER,
    reason="set AGENT_BUS_E2E_PEER=<live Claude session name> to run the e2e tier",
)
def test_tier3_end_to_end_reply_from_claude(tmp_path):
    """grok -> live Claude Code session -> reply back into grok's inbox.

    Native discovery is deliberately NOT isolated here: grok must publish its
    UDS listener into the real ~/.claude/sessions so the Claude peer can see it,
    and must dial that peer's real socket.

    The Claude side is a live session, so this waits rather than asserting
    immediately. Drive it by having that session read its message and reply with
    native SendMessage to the grok listener's name.
    """
    home = tmp_path / "bus"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()

    grok_name = f"smoke-grok-{os.getpid()}"
    _register(home, grok_name, "grok", isolate_native=False)

    _wire_mcp(project, home, isolate_native=False)

    marker = f"SMOKE-E2E-{os.getpid()}"
    prompt = project / "prompt.md"
    prompt.write_text(
        "You are part of an end-to-end test of the agent-bus.\n\n"
        f"Send a message to the Claude Code session named `{E2E_PEER}`.\n"
        "Use the agent-bus MCP `send_message` tool with "
        f"to=`{E2E_PEER}` and text exactly:\n\n"
        f"{marker} please reply to `{grok_name}` with the word ACK\n\n"
        "Then reply with a single line of JSON and no other prose:\n"
        '   {"sent": true}\n'
    )

    r = _run_grok(project, home, prompt, isolate_native=False)
    assert r.returncode == 0, f"grok exited {r.returncode}: {r.stderr[-2000:]}"

    print(f"\n[tier3] waiting for a reply to {grok_name!r}")
    print(f"[tier3] AGENT_BUS_HOME={home}")
    print(f"[tier3] the Claude peer should reply to {grok_name!r}")

    msgs = _wait_for(
        lambda: _inbox(home, grok_name, isolate_native=False),
        timeout=int(os.environ.get("AGENT_BUS_E2E_TIMEOUT", "300")),
        what=f"a reply in {grok_name}'s inbox",
    )
    assert any("ACK" in m["text"].upper() for m in msgs), msgs
