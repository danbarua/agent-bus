"""MCP server over real stdio, spoken the way a client speaks it.

The existing mcp_server tests call handle_rpc() directly, which skips the
transport entirely. That let a framing bug ship: the reader accepted both
newline-delimited JSON and LSP-style Content-Length, but the writer *always*
replied with Content-Length. MCP's stdio transport is newline-delimited, so
every real client sent NDJSON, got back a Content-Length frame it could not
parse, and timed out ("server timed out (no response within 30s)").

These tests drive the actual subprocess over a pipe.
"""

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SRC = os.path.join(REPO, "src")


def _env(tmp_path):
    """Fully isolated: serve() registers on the bus and may start a listener.

    That listener is why the socket dir is a short /tmp path rather than
    tmp_path -- over the AF_UNIX limit the bind fails on a background thread and
    the isolation this docstring claims is quietly not happening.
    """
    import secrets

    env = os.environ.copy()
    env["PYTHONPATH"] = SRC
    env["AGENT_BUS_HOME"] = str(tmp_path / "bus")
    env["AGENT_BUS_SESSIONS_DIR"] = str(tmp_path / "sessions")
    env["AGENT_BUS_SOCK_DIR"] = f"/tmp/ab-{secrets.token_hex(4)}/s"
    for k in ("AGENT_BUS_HOME", "AGENT_BUS_SESSIONS_DIR", "AGENT_BUS_SOCK_DIR"):
        os.makedirs(env[k], exist_ok=True)
    return env


def _talk(tmp_path, requests, timeout=30):
    """Send NDJSON requests to `agent-bus mcp`, return the raw stdout."""
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    return subprocess.run(
        [sys.executable, "-m", "agent_bus", "mcp"],
        input=payload,
        env=_env(tmp_path),
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _frames(stdout):
    """Parse newline-delimited JSON responses."""
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    },
}


def test_initialize_replies_as_newline_delimited_json(tmp_path):
    """The regression guard: an NDJSON request must get an NDJSON reply.

    A Content-Length header here means no MCP client can read us.
    """
    p = _talk(tmp_path, [INIT])
    assert p.returncode == 0, p.stderr

    assert "Content-Length" not in p.stdout, (
        "server replied with LSP framing to an NDJSON client; "
        f"stdout begins: {p.stdout[:120]!r}"
    )

    frames = _frames(p.stdout)
    assert frames, f"no response at all; stderr={p.stderr[:500]}"
    assert frames[0]["id"] == 1
    assert frames[0]["result"]["serverInfo"]["name"] == "agent-bus"


def test_tools_list_over_stdio(tmp_path):
    p = _talk(tmp_path, [INIT, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    assert p.returncode == 0, p.stderr
    frames = _frames(p.stdout)
    tools = next(f for f in frames if f.get("id") == 2)["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"list_agents", "send_message", "get_inbox", "ack_message", "self"} <= names, names


def test_send_message_over_stdio_reaches_the_inbox(tmp_path):
    """A tools/call round trip that ends in a real file-bus delivery."""
    env = _env(tmp_path)

    # The recipient needs its OWN live pid. Registering it under the pytest pid
    # does not work: the server's session_start() resolves its host pid by
    # walking ancestors, lands on pytest, and register()'s pid-match branch then
    # renames our entry to the host's name -- "no such agent: stdio-target".
    holder = subprocess.Popen(["sleep", "60"])
    try:
        reg = subprocess.run(
            [sys.executable, "-m", "agent_bus", "register",
             # Kind decides the channel now: a claude-kind target routes to UDS
             # and is refused when it has no socket, which is correct and not
             # what this test is about. omp reads the file bus.
             "--name", "stdio-target", "--kind", "omp", "--pid", str(holder.pid)],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        assert reg.returncode == 0, reg.stderr

        call = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "send_message",
                "arguments": {"to": "stdio-target", "text": "over stdio", "summary": "t"},
            },
        }
        p = _talk(tmp_path, [INIT, call])
        assert p.returncode == 0, p.stderr
        assert "Content-Length" not in p.stdout

        frames = _frames(p.stdout)
        resp = next(f for f in frames if f.get("id") == 3)
        assert "error" not in resp, resp["error"]

        inbox = subprocess.run(
            [sys.executable, "-m", "agent_bus", "inbox", "--json", "--target", "stdio-target"],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        msgs = json.loads(inbox.stdout or "[]")
        assert any(m["text"] == "over stdio" for m in msgs), msgs
    finally:
        holder.kill()


def test_a_subscribed_client_is_notified_of_new_mail_while_idle(tmp_path):
    """The real deliverable: not a protocol-level claim, a live subprocess
    that sits idle after subscribing and receives an unprompted
    notifications/resources/updated frame the moment a second process
    delivers it mail -- proving fswatch's directory watch, _check_and_notify,
    and serve()'s restructured loop are actually wired together correctly,
    not just each individually correct in isolation.
    """
    import queue
    import threading

    env = _env(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_bus", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=REPO, text=True, bufsize=1,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    child_stdin, child_stdout, child_stderr = proc.stdin, proc.stdout, proc.stderr
    lines: queue.Queue = queue.Queue()
    threading.Thread(
        target=lambda: [lines.put(line) for line in iter(child_stdout.readline, "")],
        daemon=True,
    ).start()

    def _next_frame(timeout=10):
        try:
            return json.loads(lines.get(timeout=timeout))
        except queue.Empty:
            pytest.fail(f"no frame within {timeout}s; stderr={child_stderr.read()[:2000]}")

    try:
        child_stdin.write(json.dumps(INIT) + "\n")
        child_stdin.flush()
        init_reply = _next_frame()
        assert init_reply["result"]["capabilities"]["resources"]["subscribe"] is True

        # Register THIS test process under the running server's own pid, the
        # same reason test_send_message_over_stdio_reaches_the_inbox's
        # comment gives -- self is resolved by walking ancestor pids from the
        # server's own process, which is `proc.pid` here, not pytest's.
        reg = subprocess.run(
            [sys.executable, "-m", "agent_bus", "register",
             "--name", "idle-subscriber", "--kind", "omp", "--pid", str(proc.pid)],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        assert reg.returncode == 0, reg.stderr

        child_stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "resources/subscribe",
            "params": {"uri": "agentbus://inbox"},
        }) + "\n")
        child_stdin.flush()
        sub_reply = _next_frame()
        assert sub_reply["id"] == 2
        assert "error" not in sub_reply, sub_reply

        # Idle now -- no request in flight. A second process delivers mail.
        send = subprocess.run(
            [sys.executable, "-m", "agent_bus", "send", "idle-subscriber",
             "-m", "wake up", "--summary", "wake up"],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        assert send.returncode == 0, send.stderr

        notice = _next_frame()
        assert notice.get("method") == "notifications/resources/updated"
        assert notice["params"]["uri"] == "agentbus://inbox"
    finally:
        child_stdin.close()
        proc.wait(timeout=10)


def test_content_length_client_still_supported(tmp_path):
    """We accept LSP framing too -- and must answer in kind, not switch to NDJSON."""
    body = json.dumps(INIT).encode()
    payload = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    p = subprocess.run(
        [sys.executable, "-m", "agent_bus", "mcp"],
        input=payload,
        env=_env(tmp_path),
        cwd=REPO,
        capture_output=True,
        timeout=30,
    )
    assert p.returncode == 0, p.stderr[:500]
    assert b"Content-Length" in p.stdout, (
        f"LSP client must get an LSP-framed reply; got {p.stdout[:120]!r}"
    )
