"""A harness that runs our MCP server can say what it is, and we listen.

session_start() registers before any client speaks, and it has nothing to go
on: probed 2026-08-24, codex hands its MCP child exactly HOME, LANG, LOGNAME,
PATH, SHELL, TERM, TMPDIR, USER and __CF_USER_TEXT_ENCODING -- no thread id,
no session id, no socket. So it registers as `pending-<pid>` and waits.

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


def test_a_client_we_cannot_place_settles_as_other(tmp_path):
    """Somebody connected and we cannot tell what they are: that is `other`.

    Not left pending. Pending means nobody has connected; once one has,
    the answer is settled even though it names no harness -- the peer is
    addressable and works, which is all `other` ever claimed.
    """
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
    # aliases is what reconciles the registered row with the discovered one,
    # and it is public because addressing is the caller's business. The same
    # session id is also kept in `native` for the adapters, which is not --
    # asserted against the roster rather than the response.
    assert f"grok:session:{sid}" in me["aliases"], me["aliases"]
    assert "native" not in me, "harness internals are not a caller's"


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


def test_initialize_still_answers_when_adoption_fails(monkeypatch):
    """A failed initialize makes the whole server look dead to the harness, so
    no bookkeeping may take the handshake down with it."""
    from agent_bus import mcp_server

    def _boom(*a, **k):
        raise RuntimeError("roster is on fire")

    monkeypatch.setattr(mcp_server, "get_self", _boom)
    reply = mcp_server.handle_rpc(_init(CODEX))
    assert reply is not None, "initialize answered nothing"
    assert "error" not in reply, reply
    assert reply["result"]["serverInfo"]["name"] == "agent-bus"


def test_the_derived_name_is_replaced_once_the_kind_is_known(tmp_path):
    """`pending-<pid>` is what session_start could manage before the handshake.
    A codex peer should not be listed under it."""
    home = tmp_path / "bus"
    home.mkdir()
    r = _talk(home, [_init(CODEX), SELF_CALL])
    assert r.returncode == 0, r.stderr
    me = _self(r)
    assert not me["name"].startswith("other-"), me["name"]
    assert me["name"].startswith("codex"), me["name"]


def test_a_grok_peer_is_named_from_its_session(tmp_path):
    home = tmp_path / "bus"
    home.mkdir()
    sid = "01a0313d-fd26-7600-8573-ebec45581278"
    r = _talk(home, [_init(GROK), SELF_CALL], env_extra={"GROK_SESSION_ID": sid})
    assert r.returncode == 0, r.stderr
    me = _self(r)
    assert not me["name"].startswith("other-"), me["name"]
    assert sid[:8] in me["name"] or me["name"].startswith("grok"), me["name"]


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


# ------------------------------------------- pending is not the same as other


def test_before_anyone_connects_the_peer_is_pending(tmp_path):
    """The server registers before a client speaks, and says so.

    `other` would be a lie here. It asserts an agent is present and cannot be
    classified; at this point in startup nobody has connected at all. The
    difference is what lets the handshake know it is allowed to write.
    """
    home = tmp_path / "bus"
    home.mkdir()
    r = _talk(home, [SELF_CALL])  # no initialize -- nobody has said hello
    assert r.returncode == 0, r.stderr
    me = _self(r)
    assert me["kind"] == "pending", me
    assert me["name"].startswith("pending-"), me["name"]
