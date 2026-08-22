"""Integration / smoke test for agent-bus, in tiers.

Opt-in: these spawn a real coding agent and (tier 3) need a live Claude Code
session, so they never run in a normal `pytest tests/` sweep.

    AGENT_BUS_INTEGRATION=1 uv run python -m pytest tests/integration -q -s

Tiers
-----
1. Liveness   - CLI only. Register, read an empty inbox, full file-bus round trip.
2. omp + MCP  - a headless `omp -p` run, wired to the bus by a project-local
                .mcp.json, that claims a name and sends under it.
3. End to end - omp sends to a *live Claude Code session*, which replies; the
                reply must land in omp's inbox. Gated on AGENT_BUS_E2E_PEER.
                The Claude side is driven by hand for now; a headless Claude
                peer can replace that once this is verified.

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


def _run_omp(project_dir, prompt, *, timeout=420):
    """Headless omp.

    stdin MUST be closed: omp probes stdin during startup, and an inherited pipe
    that never sends EOF wedges it in readPipedInput before the model is called.
    """
    return subprocess.run(
        [
            "omp", "-p", "--no-session", "--no-title", "--auto-approve",
            "--model", OMP_MODEL,
            "--cwd", str(project_dir),
            "--max-time", "5m",
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


# ------------------------------------------------------------------- tier 2: omp + MCP


@pytest.mark.skipif(not HAVE_OMP, reason="omp not on PATH")
def test_tier2_omp_registers_and_sends_under_its_own_name(tmp_path):
    """A headless omp run claims a name and sends under it.

    Asserting the *sender* matters as much as the delivery: before send_message
    resolved get_self(), messages arrived as "anonymous" with a random id --
    delivered but unaddressable, which a delivery-only assertion would pass.
    """
    home = tmp_path / "bus"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    _write_mcp_config(project, home)

    # a recipient with its own live pid, so it cannot collide with the peer's
    holder = subprocess.Popen(["sleep", "180"])
    try:
        _register(home, "claude-target", "claude", pid=holder.pid)

        token = "tier2-omp-token"
        r = _run_omp(
            project,
            "You have an MCP server named agent-bus. Do exactly this and nothing else: "
            '(1) call register with name="omp-peer" and kind="omp"; '
            f'(2) call send_message with to="claude-target", text="{token}", '
            'summary="smoke". '
            'Then print ONE line of JSON only: {"registered":true,"sent":true}. '
            "Do not ask questions.",
        )
        assert r.returncode == 0, f"omp exited {r.returncode}: {r.stderr[-2000:]}"

        msgs = _inbox(home, "claude-target")
        assert msgs, f"nothing delivered.\nomp stdout:\n{r.stdout[-2000:]}"

        mine = [m for m in msgs if token in m["text"]]
        assert mine, msgs
        sender = mine[0]["from"]
        assert sender["name"] == "omp-peer", (
            f"sender is {sender['name']!r}; the peer registered a name but the "
            "message did not carry it"
        )
        assert sender["kind"] == "omp", sender
    finally:
        holder.kill()


# ------------------------------------------------------------------------ tier 3: e2e


@pytest.mark.skipif(not HAVE_OMP, reason="omp not on PATH")
@pytest.mark.skipif(
    not E2E_PEER,
    reason="set AGENT_BUS_E2E_PEER=<live Claude session name> for the e2e tier",
)
def test_tier3_end_to_end_reply_from_claude(tmp_path):
    """omp -> live Claude Code session -> reply back into omp's inbox.

    omp must stay alive for the whole round trip. A single-turn peer that exits
    after sending is pruned from the roster -- name and mailbox both -- so the
    reply fails with "no such agent". The prompt therefore has omp poll its own
    inbox until the ACK lands, keeping its registration live.

    The reply travels over the FILE bus, not UDS: omp publishes no UDS listener,
    so a native SendMessage has nowhere to land. Native discovery is left
    un-isolated so the Claude peer is resolvable by name.

    Drive the Claude side against the AGENT_BUS_HOME printed below:

        AGENT_BUS_HOME=<home> agent-bus inbox --name <peer>
        AGENT_BUS_HOME=<home> agent-bus send omp-peer -m ACK --from-name <peer>
    """
    home = tmp_path / "bus"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    _write_mcp_config(project, home)

    marker = f"E2E-{os.getpid()}"
    print(f"\n[tier3] AGENT_BUS_HOME={home}")
    print(f"[tier3] {E2E_PEER}: reply to 'omp-peer' with ACK")

    r = _run_omp(
        project,
        "You have an MCP server named agent-bus. Do exactly this and nothing else:\n"
        '1. Call register with name="omp-peer" and kind="omp".\n'
        f'2. Call send_message with to="{E2E_PEER}", '
        f'text="{marker} please reply to omp-peer with the word ACK", summary="e2e".\n'
        "3. Now WAIT for a reply. Repeat this loop until you see it: run the bash "
        "command `sleep 10`, then call get_inbox with name=\"omp-peer\". Keep "
        "looping until a message whose text contains ACK appears. Do not stop "
        "before you see it; do not give up early.\n"
        '4. When the ACK arrives, print ONE line of JSON only: {"ack":true}.\n'
        "Do not ask questions.",
        timeout=int(os.environ.get("AGENT_BUS_E2E_TIMEOUT", "420")) + 120,
    )
    assert r.returncode == 0, f"omp exited {r.returncode}: {r.stderr[-2000:]}"

    # omp saw the ACK while still registered; confirm it is really on the bus
    msgs = _inbox(home, "omp-peer", isolate_native=False)
    assert any("ACK" in m["text"].upper() for m in msgs), (
        f"omp reported: {r.stdout[-500:]}\ninbox now: {msgs}"
    )
