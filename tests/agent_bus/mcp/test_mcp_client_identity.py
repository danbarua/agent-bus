"""A harness that runs our MCP server can say what it is, over `register`.

`initialize`'s own clientInfo identifies the connection's kind before any
tool call, and once it has, that answer is authoritative -- the register
tool's own schema omits `kind` entirely in that case, and a value in `args`
is either absent or a stale client still sending what an older schema
advertised. These tests pin exactly that: which handshakes identify_mcp_client
places, and what the `register` tool call does with a kind it already knows.
Driven through the real stdio subprocess rather than handle_rpc in-process,
because the guard spans initialize -> tools/call and the transport is where
this kind of thing has broken before.
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
                     ("AGENT_BUS_GROK_DIR", "-g"), ("AGENT_BUS_OMP_DIR", "-o")):
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


def _reply(result, mid):
    """The raw JSON-RPC reply for one request id, error or result alike."""
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        msg = json.loads(line)
        if msg.get("id") == mid:
            return msg
    raise AssertionError(
        f"no reply for id={mid}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _self(result):
    """Read the `self` tool's answer out of the stdio replies.

    Asserted in-band rather than off the roster on disk: every test in this
    file leaves AGENT_BUS_NAME unset, so session_start() never registers
    anything and session_end() has nothing of its own to remove when stdin
    closes -- an entry an explicit `register` call created here outlives
    the subprocess. The question these tests ask is what the entry looked
    like *while the session was live*, not whether it is still there after.
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

def test_the_handshakes_kind_outranks_a_hand_supplied_one(tmp_path):
    """Once the handshake has identified this connection's kind, that answer
    is authoritative -- the register tool's own schema omits kind entirely
    in that case (_register_tool()), so a value in args is either absent or
    a stale client still sending what an older schema advertised. Either
    way it must not silently override a kind the handshake already got
    right: a codex connection claiming kind=omp stays codex."""
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
    assert (me["name"], me["kind"]) == ("claimed", "codex")


def test_a_claimed_name_is_never_overwritten(tmp_path):
    """The name half of a claimed identity still outranks anything we infer
    -- only kind is now handshake-authoritative. initialize precedes
    tools/call, so this is belt and braces, but it is the guard that
    matters most."""
    home = tmp_path / "bus"
    home.mkdir()
    frames = [
        _init(CODEX),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "register", "arguments": {"name": "claimed"}}},
        _init(CODEX, rid=3),
        SELF_CALL,
    ]
    r = _talk(home, frames)
    assert r.returncode == 0, r.stderr
    me = _self(r)
    assert (me["name"], me["kind"]) == ("claimed", "codex")


def test_a_claimed_name_survives_a_second_initialize(tmp_path):
    home = tmp_path / "bus"
    home.mkdir()
    frames = [
        _init(CODEX),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "register", "arguments": {"name": "claimed-name", "kind": "codex"}}},
        _init(CODEX, rid=3),
        SELF_CALL,
    ]
    r = _talk(home, frames)
    assert r.returncode == 0, r.stderr
    assert _self(r)["name"] == "claimed-name"


# ---------------------------- kind=claude is a socket promise, not a label


def test_registering_as_claude_is_rejected_when_the_handshake_says_otherwise(tmp_path):
    """#320: an omp session running a Claude-branded model asked to register
    with kind=claude. `claude` is not a model label -- it is a promise that
    this process is the native Claude Code CLI, which publishes its own
    delivery socket (adapters/transport/claude.py). The claim is already
    inert (the handshake's answer wins regardless), but a client asserting
    it over a connection the handshake placed as something else has
    misunderstood what it is -- rejected explicitly rather than silently
    ignored like any other mismatched claim.
    """
    home = tmp_path / "bus"
    home.mkdir()
    frames = [
        _init(OMP),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "register", "arguments": {"name": "overlap-bench", "kind": "claude"}}},
    ]
    r = _talk(home, frames)
    assert r.returncode == 0, r.stderr
    reply = _reply(r, 2)
    assert "error" in reply, reply
    assert reply["error"]["code"] == -32000
    assert "omp" in reply["error"]["message"]
    assert "claude" in reply["error"]["message"]
    assert "Omit kind" in reply["error"]["message"]


def test_a_rejected_register_reaches_the_default_log_level(tmp_path):
    """The mcp layer's own `_rpc_log` used to log every `tools/call` at INFO
    regardless of outcome -- the same silent-failure shape `log._emit` was
    fixed for once already (test_log.py::test_a_failed_verb_reaches_you_at_
    the_default_level), just not applied here. At the default (unset)
    level, a rejected register was indistinguishable from a successful one
    unless log level was raised."""
    home = tmp_path / "bus"
    home.mkdir()
    log_file = tmp_path / "agent-bus.jsonl"
    frames = [
        _init(OMP),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "register", "arguments": {"name": "x", "kind": "claude"}}},
    ]
    r = _talk(home, frames, env_extra={"AGENT_BUS_LOG_FILE": str(log_file)})
    assert r.returncode == 0, r.stderr
    records = [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]
    rec = next(rec for rec in records if rec.get("tool") == "register")
    assert rec["ok"] is False
    assert rec["severity"] == "WARNING"


def test_registering_as_claude_is_allowed_with_no_contradicting_handshake(tmp_path):
    """Conservative on purpose: the guard only fires on a positive
    contradiction. A client identify_mcp_client cannot place (settled as
    `other`) gets no veto -- there is no evidence it is lying, only that we
    do not know what it is.
    """
    home = tmp_path / "bus"
    home.mkdir()
    frames = [
        _init({"name": "some-editor", "version": "9"}),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "register",
                    "arguments": {"name": "genuinely-claude", "kind": "claude"}}},
        SELF_CALL,
    ]
    r = _talk(home, frames)
    assert r.returncode == 0, r.stderr
    reply = _reply(r, 2)
    assert "error" not in reply, reply
    assert _self(r)["kind"] == "claude"


def test_registering_as_the_handshakes_own_kind_is_never_rejected(tmp_path):
    """The guard is specific to kind=claude, not a general
    claimed-kind-must-match-handshake rule -- registering as your own
    identified kind, or a kind that is not claude at all, is unaffected."""
    home = tmp_path / "bus"
    home.mkdir()
    frames = [
        _init(OMP),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "register", "arguments": {"name": "labkit-omp-claude"}}},
        SELF_CALL,
    ]
    r = _talk(home, frames)
    assert r.returncode == 0, r.stderr
    reply = _reply(r, 2)
    assert "error" not in reply, reply
    # The handshake already identified this connection as omp, and
    # registering a name (with no kind supplied) must not replace that with
    # normalize_kind(None)'s fallback, "other".
    assert _self(r)["kind"] == "omp"


def _register_schema(reply):
    tool = next(t for t in reply["result"]["tools"] if t["name"] == "register")
    return tool["inputSchema"]


def test_register_hides_kind_once_the_handshake_knows_it(tmp_path):
    """Once identify_mcp_client has placed this connection, the agent is
    never asked to supply or override kind -- the schema omits the field
    entirely rather than advertise a knob _call_register would just ignore."""
    home = tmp_path / "bus"
    home.mkdir()
    frames = [_init(OMP),
              {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    r = _talk(home, frames)
    assert r.returncode == 0, r.stderr
    schema = _register_schema(_reply(r, 2))
    assert "kind" not in schema["properties"]


def test_register_still_offers_kind_when_unidentified(tmp_path):
    """A client identify_mcp_client cannot place is exactly where a
    hand-supplied kind is the only source of truth, so the field stays."""
    home = tmp_path / "bus"
    home.mkdir()
    frames = [_init({"name": "some-editor", "version": "9"}),
              {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    r = _talk(home, frames)
    assert r.returncode == 0, r.stderr
    schema = _register_schema(_reply(r, 2))
    assert "kind" in schema["properties"]
