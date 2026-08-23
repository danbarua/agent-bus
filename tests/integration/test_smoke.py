"""Integration / smoke test for agent-bus, in tiers.

Opt-in: these spawn a real coding agent and (tier 3) need a live Claude Code
session, so they never run in a normal `pytest tests/` sweep.

    AGENT_BUS_INTEGRATION=1 uv run python -m pytest tests/integration -q -s

Tiers
-----
1. Liveness   - CLI only. Register, read an empty inbox, full file-bus round trip.
2. Peer -> Claude (UDS) - a headless omp run plugs in as a native Claude peer and
                messages a live Claude session over UDS.
3. Round trip (UDS) - omp says hello, the Claude session replies, omp sees it.

Tiers 2 and 3 test UDS, because that is the product: a peer that appears in
Claude's native ListAgents and can be messaged like any Claude session. They
assert nothing about inbox files -- to the calling agent there is only the MCP
facade, and to Claude there is only the socket.

Nothing is built or asserted on the Claude side. Its harness delivers the peer's
message and it answers with its native SendMessage. That absence of Claude-side
code is the feature, so a test that needs Claude to poll, read an inbox or look
up a socket is testing the wrong thing.

Why omp and not grok
--------------------
grok refuses to start project-scoped MCP servers in an untrusted folder ("folder
untrusted (repo-local (project-scoped) server not started...)"), and a throwaway
directory is untrusted by definition, so the point of a clean sandbox is lost.
omp reads a project-local .mcp.json with no such gate, keeping the wireup to one
file in the tmpdir and touching no global config. Grok tiers to follow.

Identity
--------
An MCP-only peer has no session-start hook, so nothing registers it and it has
no name. omp inherits exactly one identifying variable from its parent
(PI_NO_TITLE=1), so its identity cannot be inferred either. It must call the
`register` tool to become addressable -- which is what tier 2 asserts, by
checking the *sender* on the delivered message rather than only that something
arrived.
"""

import json
import os
import shutil
import subprocess
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

INTEGRATION = os.environ.get("AGENT_BUS_INTEGRATION") == "1"
HAVE_OMP = shutil.which("omp") is not None
E2E_PEER = os.environ.get("AGENT_BUS_E2E_PEER")
OMP_MODEL = os.environ.get("AGENT_BUS_OMP_MODEL", "xai-oauth/grok-4.6")

pytestmark = pytest.mark.skipif(
    not INTEGRATION, reason="set AGENT_BUS_INTEGRATION=1 to run integration tests"
)


# --------------------------------------------------------------------------- helpers


def _bus_env(home, *, isolate_native=True):
    """Environment for a bus CLI call.

    isolate_native=False leaves the real sessions/socket dirs in place, which is
    required for anything that must see, or be seen by, a live Claude session.
    """
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = str(home)
    if isolate_native:
        for var, sub in (("AGENT_BUS_SESSIONS_DIR", "-sessions"),
                         ("AGENT_BUS_SOCK_DIR", "-socks")):
            env[var] = str(home) + sub
            os.makedirs(env[var], exist_ok=True)
    else:
        env.pop("AGENT_BUS_SESSIONS_DIR", None)
        env.pop("AGENT_BUS_SOCK_DIR", None)
    return env


def _bus(home, *args, isolate_native=True, timeout=60):
    return subprocess.run(
        ["uv", "run", "--project", REPO, "agent-bus", *args],
        env=_bus_env(home, isolate_native=isolate_native),
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _register(home, name, kind, *, pid=None, isolate_native=True):
    """Register under a pid that outlives the call.

    register() defaults to the calling process, but `uv run agent-bus` exits
    immediately and the entry is pruned as dead before the next call.
    """
    r = _bus(home, "register", "--name", name, "--kind", kind,
             "--pid", str(pid or os.getpid()), isolate_native=isolate_native)
    assert r.returncode == 0, f"register {name} failed: {r.stderr}"
    return r


def _write_mcp_config(project_dir, home):
    """The entire wireup: one project-local file, no global config touched."""
    cfg = {
        "mcpServers": {
            "agent-bus": {
                "command": "uv",
                "args": ["run", "--project", REPO, "agent-bus", "mcp"],
                "env": {"AGENT_BUS_HOME": str(home)},
            }
        }
    }
    path = project_dir / ".mcp.json"
    path.write_text(json.dumps(cfg, indent=2))
    return path


def _run_omp(project_dir, prompt, *, max_time="5m", timeout=420):
    """Headless omp.

    stdin MUST be closed: omp probes stdin during startup, and an inherited pipe
    that never sends EOF wedges it in readPipedInput before the model is called.
    """
    return subprocess.run(
        [
            "omp", "-p", "--no-session", "--no-title", "--auto-approve",
            "--model", OMP_MODEL,
            "--cwd", str(project_dir),
            "--max-time", max_time,
            "--mode", "text",
            "--", prompt,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _inbox(home, name, *, isolate_native=True):
    r = _bus(home, "inbox", "--json", "--name", name, isolate_native=isolate_native)
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return []


def _wait_for(predicate, timeout, interval=3.0, what="condition"):
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


# ------------------------------------------------------- tier 2: peer -> Claude (UDS)


@pytest.mark.skipif(not HAVE_OMP, reason="omp not on PATH")
@pytest.mark.skipif(
    not E2E_PEER,
    reason="set AGENT_BUS_E2E_PEER=<live Claude session name> to run the UDS tiers",
)
def test_tier2_peer_registers_and_messages_claude_over_uds(tmp_path):
    """The peer becomes a native Claude peer and messages a live Claude session.

    Nothing here asserts on inbox files. To the calling agent there is only the
    MCP facade; to Claude there is only the socket. The product is that a peer
    plugs in natively and a message reaches the session.

    SEND_EXIT=0 is a strong assertion: send-peer needs the peer's OWN listener,
    because the outbound frame carries its socket as the reply address. So a
    successful send proves the whole chain -- MCP server up, session_start ran,
    the peer registered, and its Claude-shaped session and socket were published.
    """
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "bus"
    home.mkdir()
    _write_mcp_config(project, home)

    # The CLI must use the SAME bus home as the MCP server. .mcp.json sets
    # AGENT_BUS_HOME for the server process only; a bash command inside omp
    # does not inherit it and would look for the listener under ~/.agent-bus.
    cli = f"AGENT_BUS_HOME={home} uv run --project {REPO} agent-bus"
    r = _run_omp(
        project,
        "Do exactly this, nothing else.\n"
        '1. Call the agent-bus MCP tool `register` with name="omp-peer" and kind="omp".\n'
        "2. Run this bash command and print its output verbatim:\n"
        f'   {cli} send-peer {E2E_PEER} -m "Hello world from omp-peer" ; echo SEND_EXIT=$?\n'
        "3. Print DONE.\n"
        "Do not ask questions.",
    )
    assert r.returncode == 0, f"omp exited {r.returncode}: {r.stderr[-2000:]}"
    assert "SEND_EXIT=0" in r.stdout, (
        "the peer could not message the Claude session over UDS.\n"
        f"omp stdout:\n{r.stdout[-3000:]}"
    )


# ------------------------------------------------------------- tier 3: round trip (UDS)


@pytest.mark.skipif(not HAVE_OMP, reason="omp not on PATH")
@pytest.mark.skipif(
    not E2E_PEER,
    reason="set AGENT_BUS_E2E_PEER=<live Claude session name> to run the UDS tiers",
)
def test_tier3_round_trip_peer_to_claude_and_back(tmp_path):
    """omp says hello over UDS; the Claude session replies; omp sees the reply.

    The Claude side does nothing and needs nothing built. Its harness delivers
    the peer's message into the conversation and it answers with its native
    SendMessage -- no plugin, no MCP, no polling. That absence is the feature,
    so this test asserts nothing about the Claude side and never inspects it.

    The peer must stay alive for the round trip: a peer that exits is pruned,
    taking its name and mailbox with it, and the reply has nowhere to land. So
    omp waits on its own inbox through the MCP facade until the reply arrives.
    """
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "bus"
    home.mkdir()
    _write_mcp_config(project, home)

    # The CLI must use the SAME bus home as the MCP server. .mcp.json sets
    # AGENT_BUS_HOME for the server process only; a bash command inside omp
    # does not inherit it and would look for the listener under ~/.agent-bus.
    cli = f"AGENT_BUS_HOME={home} uv run --project {REPO} agent-bus"
    r = _run_omp(
        project,
        "Do exactly this, nothing else.\n"
        '1. Call the agent-bus MCP tool `register` with name="omp-peer" and kind="omp".\n'
        "2. Run this bash command and print its output verbatim:\n"
        f'   {cli} send-peer {E2E_PEER} -m "Hello world from omp-peer, please reply" ;'
        " echo SEND_EXIT=$?\n"
        "3. Wait for the reply. Repeat this loop at most 20 times: run the bash\n"
        "   command `sleep 15`, then call the agent-bus MCP tool `get_inbox` with\n"
        '   name="omp-peer". Stop as soon as the inbox contains a message.\n'
        "4. Print REPLY=<the text of that message> on one line, or REPLY=NONE if\n"
        "   the loop finished with an empty inbox.\n"
        "Do not ask questions.",
        max_time="12m",
        timeout=900,
    )
    assert r.returncode == 0, f"omp exited {r.returncode}: {r.stderr[-2000:]}"
    assert "SEND_EXIT=0" in r.stdout, (
        f"the peer never reached the Claude session.\nomp stdout:\n{r.stdout[-3000:]}"
    )
    assert "REPLY=NONE" not in r.stdout, (
        f"no reply arrived from {E2E_PEER} within the wait.\n"
        f"omp stdout:\n{r.stdout[-3000:]}"
    )
    assert "REPLY=" in r.stdout, f"omp did not report a reply.\nomp stdout:\n{r.stdout[-3000:]}"
