"""A harness we have never heard of must be able to name itself.

The Kind enum was a closed Literal restated in two CLI checks, an argparse
choices list and two MCP schemas, so `register --kind whatever` was rejected
outright. That is the opposite of the point: this bus exists so an unfamiliar
harness can join it.
"""

import json
import os
import subprocess
import sys

from agent_bus.protocol import FALLBACK_KIND, KNOWN_KINDS, normalize_kind
from agent_bus.store import find_entry, list_agents, register

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def _bus(home, *args):
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = str(home)
    env["PYTHONPATH"] = os.path.join(REPO, "src")
    return subprocess.run(
        [sys.executable, "-m", "agent_bus", *args],
        env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
    )


def test_an_unknown_kind_can_register(tmp_path):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        entry = register("newcomer", "aider", pid=holder.pid, home=str(tmp_path))
        assert entry.kind == "aider"
        assert find_entry("newcomer", home=str(tmp_path)).kind == "aider"
    finally:
        holder.kill()
        holder.wait()


def test_cli_accepts_an_unknown_kind(tmp_path):
    r = _bus(tmp_path, "register", "--name", "stranger", "--kind", "cursor",
             "--pid", str(os.getpid()))
    assert r.returncode == 0, r.stderr
    listed = json.loads(_bus(tmp_path, "list", "--json").stdout)
    assert any(a["name"] == "stranger" and a["kind"] == "cursor" for a in listed), listed


def test_filtering_by_an_unknown_kind_returns_nothing_not_everything(tmp_path):
    """A filter for a harness we do not know must not silently degrade to
    'no filter' and return the whole roster."""
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("a", "claude", pid=holder.pid, home=str(tmp_path))
        assert list_agents(kind="nosuchharness", home=str(tmp_path)) == []
    finally:
        holder.kill()
        holder.wait()


def test_normalize_is_case_and_space_insensitive():
    assert normalize_kind("  Grok ") == "grok"
    assert normalize_kind("AIDER") == "aider"


def test_normalize_falls_back_on_empty():
    assert normalize_kind(None) == FALLBACK_KIND
    assert normalize_kind("   ") == FALLBACK_KIND


def test_known_kinds_are_a_hint_not_a_gate():
    assert "claude" in KNOWN_KINDS
    assert normalize_kind("definitely-not-in-known-kinds") == "definitely-not-in-known-kinds"


def test_mcp_list_agents_normalizes_kind(tmp_path, monkeypatch):
    """mcp_server already normalizes for register; list_agents did not, so
    {"kind": "Claude"} silently returned [] once the schema enum was gone."""
    import subprocess

    from agent_bus.mcp_server import _CALLS

    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path))
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("cased", "claude", pid=holder.pid, home=str(tmp_path))
        got = _CALLS["list_agents"]({"kind": "Claude"})
        assert any(a["name"] == "cased" for a in got), got
    finally:
        holder.kill()
        holder.wait()


def test_shim_published_peer_keeps_its_kind(tmp_path, monkeypatch):
    """A peer discovered through its shim listener must keep its own kind.

    Kind became a plain `str` when the enum was opened, so the membership
    test `k not in get_args(Kind)` compared against an empty tuple and forced
    every agentBus-published peer to "other". A grok peer was then invisible
    to `list --kind grok` -- in the one view whose whole job is to make the
    harnesses look alike.
    """
    import json
    import subprocess

    from agent_bus.adapters.discovery import claude as claude_adapter

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sessions))
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (sessions / f"{holder.pid}.json").write_text(json.dumps({
            "pid": holder.pid,
            "sessionId": "shim-1",
            "name": "grok-peer",
            "agentBus": True,
            "agent": "grok",
        }))
        found = {a["name"]: a["kind"] for a in claude_adapter.discover()}
        assert found.get("grok-peer") == "grok", found
    finally:
        holder.kill()
        holder.wait()


def test_shim_peer_without_a_declared_kind_falls_back(tmp_path, monkeypatch):
    import json
    import subprocess

    from agent_bus.adapters.discovery import claude as claude_adapter

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sessions))
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (sessions / f"{holder.pid}.json").write_text(json.dumps({
            "pid": holder.pid, "sessionId": "shim-2",
            "name": "nameless", "agentBus": True,
        }))
        found = {a["name"]: a["kind"] for a in claude_adapter.discover()}
        assert found.get("nameless") == "other", found
    finally:
        holder.kill()
        holder.wait()


def test_a_pending_peer_is_still_addressable(tmp_path):
    """Rule #1: not having spoken yet must never make a peer unreachable.

    `pending` exists to say the bus has not been told what this agent is.
    That is a statement about our knowledge, not about the agent's reach, and
    the moment it starts gating delivery it has broken the thing it was added
    to describe. A peer is addressable because a live process registered it.
    """
    import subprocess
    import sys as _sys

    from agent_bus.adapters import transport
    from agent_bus.commands import messages
    from agent_bus.protocol import PENDING_KIND, delivery_expectation

    home = str(tmp_path / "bus")
    # Its own live process: registering a second name against one pid renames
    # the first, which would quietly leave a single agent talking to itself.
    holder = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        register("unspoken", PENDING_KIND, pid=holder.pid, home=home)
        messages.send(to="unspoken", text="hello", summary="s",
                      from_name="somebody", home=home)
        texts = [m["text"] for m in messages.inbox(address="unspoken", home=home)]
        assert "hello" in texts, texts
    finally:
        holder.kill()
        holder.wait()

    # Routed like any unrecognised kind -- to the file bus -- rather than
    # falling off a table that only knows the named harnesses.
    assert transport.for_kind(PENDING_KIND) is None
    # And expected to be read now: there is no human in this loop.
    assert delivery_expectation(PENDING_KIND) == delivery_expectation(FALLBACK_KIND)
