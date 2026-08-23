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

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


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
