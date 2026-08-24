"""A harness that runs our MCP server can say what it is, and we listen.

session_start() registers before any client speaks, and it has nothing to go
on: probed 2026-08-24, codex hands its MCP child exactly HOME, LANG, LOGNAME,
PATH, SHELL, TERM, TMPDIR, USER and __CF_USER_TEXT_ENCODING -- no thread id,
no session id, no socket. So every MCP-only peer registered as `other-<pid>`.

`initialize` does carry an identity, and these pin what we do with it. Driven
through the real stdio subprocess rather than handle_rpc in-process, because
the upgrade spans initialize -> tools/call and the transport is where this
kind of thing has broken before.
"""
import json
import os
import subprocess
import sys

import pytest

from agent_bus.adapters.lifecycle import identify_mcp_client

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")

CODEX = {"name": "codex-mcp-client", "title": "Codex", "version": "0.149.0"}
OMP = {"name": "omp-coding-agent", "version": "1.0.0"}
GROK = {"name": "grok-shell-agent-bus", "version": "1.0.5"}


def _talk(home, frames, env_extra=None):
    env = {**os.environ, "PYTHONPATH": SRC, "AGENT_BUS_HOME": str(home)}
    for var, sub in (("AGENT_BUS_SESSIONS_DIR", "-s"), ("AGENT_BUS_SOCK_DIR", "-k"),
                     ("AGENT_BUS_GROK_DIR", "-g"), ("AGENT_BUS_OMP_DIR", "-o"),
                     ("AGENT_BUS_CODEX_DIR", "-c")):
        env[var] = str(home) + sub
        os.makedirs(env[var], exist_ok=True)
    for k in ("GROK_SESSION_ID", "GROK_HOOK_EVENT", "GROK_PLUGIN_ROOT",
              "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "agent_bus", "mcp"],
        input="".join(json.dumps(f) + "\n" for f in frames),
        env=env, capture_output=True, text=True, timeout=60,
    )


def _init(client_info, rid=1):
    return {"jsonrpc": "2.0", "id": rid, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": client_info}}


SELF_CALL = {"jsonrpc": "2.0", "id": 99, "method": "tools/call",
             "params": {"name": "self", "arguments": {}}}


def _self(result):
    """Read the `self` tool's answer out of the stdio replies.

    Asserted in-band rather than off the roster on disk: serve() calls
    session_end() when stdin closes, so by the time the subprocess has exited
    its entry is correctly gone. The question is what the entry looked like
    *while the session was live*.
    """
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        msg = json.loads(line)
        if msg.get("id") == 99:
            assert "error" not in msg, msg
            return json.loads(msg["result"]["content"][0]["text"])
    raise AssertionError(f"no self reply.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")


# --- the mapping, unit ----------------------------------------------------

@pytest.mark.parametrize("info,env,expected", [
    (CODEX, {}, ("codex", None)),
    (OMP, {}, ("omp", None)),
    (GROK, {"GROK_SESSION_ID": "sid-1"}, ("grok", "sid-1")),
    (GROK, {}, ("grok", None)),
    # grok embeds OUR server's name in its client name, so it must be a prefix
    ({"name": "grok-shell-something-else"}, {}, ("grok", None)),
    # A Claude session running our MCP server is a misconfiguration, not a kind
    ({"name": "claude-code"}, {}, (None, None)),
    ({"name": ""}, {}, (None, None)),
    (None, {}, (None, None)),
])
def test_identify_mcp_client(info, env, expected):
    assert identify_mcp_client(info, env) == expected


def test_the_session_id_is_never_read_without_a_matching_client():
    """The refusal grok.detect() was written for, preserved.

    GROK_SESSION_ID reaches anything launched from a grok shell -- including a
    Claude session, which would then adopt a grok identity and unregister the
    live grok one on exit. clientInfo is what tells the two apart.
    """
    env = {"GROK_SESSION_ID": "not-ours"}
    assert identify_mcp_client({"name": "claude-code"}, env) == (None, None)
    assert identify_mcp_client({"name": "something-else"}, env) == (None, None)
    assert identify_mcp_client(None, env) == (None, None)


# --- end to end, over the real transport ----------------------------------

@pytest.mark.parametrize("info,kind", [(CODEX, "codex"), (OMP, "omp")])
def test_an_mcp_peer_is_registered_as_its_own_kind(tmp_path, info, kind):
    home = tmp_path / "bus"
    home.mkdir()
    r = _talk(home, [_init(info), SELF_CALL])
    assert r.returncode == 0, r.stderr
    assert _self(r)["kind"] == kind


def test_without_a_recognised_client_it_stays_other(tmp_path):
    home = tmp_path / "bus"
    home.mkdir()
    r = _talk(home, [_init({"name": "some-editor", "version": "9"}), SELF_CALL])
    assert r.returncode == 0, r.stderr
    assert _self(r)["kind"] == "other"


def test_a_grok_peer_carries_its_session_address(tmp_path):
    """The link that makes a registered grok peer and its discovered entry
    reconcile into one row instead of two."""
    home = tmp_path / "bus"
    home.mkdir()
    sid = "01a03133-08b3-7950-8601-90e355728c2d"
    r = _talk(home, [_init(GROK), SELF_CALL], env_extra={"GROK_SESSION_ID": sid})
    assert r.returncode == 0, r.stderr
    me = _self(r)
    assert me["kind"] == "grok"
    assert f"grok:session:{sid}" in me["aliases"], me["aliases"]
    assert me["native"].get("sessionId") == sid


def test_the_session_id_alone_does_not_make_us_grok(tmp_path):
    """A Claude session inside a grok shell inherits GROK_SESSION_ID. It must
    not be enough."""
    home = tmp_path / "bus"
    home.mkdir()
    r = _talk(home, [_init({"name": "claude-code", "version": "2"}), SELF_CALL],
              env_extra={"GROK_SESSION_ID": "inherited-through-a-shell"})
    assert r.returncode == 0, r.stderr
    me = _self(r)
    assert me["kind"] == "other"
    assert me["aliases"] == []


def test_a_claimed_identity_is_never_overwritten(tmp_path):
    """register() outranks anything we infer. initialize precedes tools/call,
    so this is belt and braces -- but it is the guard that matters most."""
    home = tmp_path / "bus"
    home.mkdir()
    frames = [
        _init(CODEX),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "register", "arguments": {"name": "claimed", "kind": "omp"}}},
        _init(CODEX, rid=3),
        SELF_CALL,
    ]
    r = _talk(home, frames)
    assert r.returncode == 0, r.stderr
    me = _self(r)
    assert (me["name"], me["kind"]) == ("claimed", "omp")


def test_initialize_still_answers_when_adoption_fails(monkeypatch):
    """A failed initialize makes the whole server look dead to the harness, so
    no bookkeeping may take the handshake down with it."""
    from agent_bus import mcp_server

    def _boom(*a, **k):
        raise RuntimeError("roster is on fire")

    monkeypatch.setattr(mcp_server, "get_self", _boom)
    reply = mcp_server.handle_rpc(_init(CODEX))
    assert "error" not in reply, reply
    assert reply["result"]["serverInfo"]["name"] == "agent-bus"
