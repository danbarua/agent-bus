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
import time
from typing import Any

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


INIT_WITH_ROOTS = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {"roots": {}},
        "clientInfo": {"name": "omp-coding-agent", "version": "1"},
    },
}


def _spawn_mcp(env):
    """A live `agent-bus mcp` subprocess plus a threaded stdout reader.

    Shared setup for the roots/list tests below, mirroring
    test_a_subscribed_client_is_notified_of_new_mail_while_idle's own
    inline pattern. Returns (proc, next_frame, no_frame_within).

    Two separate functions, not one with a "did it fail" flag: next_frame
    always returns a real frame (pytest.fail()'s NoReturn keeps its
    signature dict[str, Any], not Optional, so every caller that expects a
    frame is not stuck narrowing away a None basedpyright would otherwise
    infer at every one of them).
    """
    import queue
    import threading

    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_bus", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=REPO, text=True, bufsize=1,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    child_stdout, child_stderr = proc.stdout, proc.stderr
    lines: queue.Queue = queue.Queue()
    threading.Thread(
        target=lambda: [lines.put(line) for line in iter(child_stdout.readline, "")],
        daemon=True,
    ).start()

    def next_frame(timeout=10) -> dict[str, Any]:
        try:
            return json.loads(lines.get(timeout=timeout))
        except queue.Empty:
            pytest.fail(f"no frame within {timeout}s; stderr={child_stderr.read()[:2000]}")

    def no_frame_within(timeout=2) -> bool:
        """True if nothing arrived -- the assertion a muted notification needs."""
        try:
            frame = json.loads(lines.get(timeout=timeout))
        except queue.Empty:
            return True
        pytest.fail(f"expected no frame within {timeout}s, got: {frame}")

    return proc, next_frame, no_frame_within


def test_a_roots_capable_client_gets_asked_and_named_by_project(tmp_path):
    """The deliverable for #311. No MCP tool call and no `agent-bus
    register` anywhere in this test: the server asks the connected client
    directly for its own root, over the connection that already exists,
    and uses the answer to replace the bare pid-derived name with a
    project-scoped one.

    Registered under `os.getpid()`, not `proc.pid`: `session_start()`
    resolves its own host pid by walking ancestors from inside the
    spawned server process, which lands on *this* test process (the one
    that called subprocess.Popen), the same fact
    test_send_message_over_stdio_reaches_the_inbox's comment already
    documents for a different reason.
    """
    env = _env(tmp_path)
    proc, next_frame, _ = _spawn_mcp(env)
    child_stdin = proc.stdin
    assert child_stdin is not None

    project_dir = tmp_path / "distinctive-project-name"
    project_dir.mkdir()

    try:
        child_stdin.write(json.dumps(INIT_WITH_ROOTS) + "\n")
        child_stdin.flush()
        next_frame()  # the initialize reply

        child_stdin.write(json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }) + "\n")
        child_stdin.flush()

        roots_request = next_frame()
        assert roots_request.get("method") == "roots/list"
        assert "id" in roots_request

        child_stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [
                {"uri": project_dir.as_uri(), "name": project_dir.name},
            ]},
        }) + "\n")
        child_stdin.flush()

        # A response gets no reply frame of its own -- poll the roster
        # directly rather than waiting on stdout for something that never
        # arrives.
        deadline = time.time() + 10
        entry = None
        while time.time() < deadline:
            listing = subprocess.run(
                [sys.executable, "-m", "agent_bus", "list", "--json"],
                env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
            )
            assert listing.returncode == 0, listing.stderr
            found = json.loads(listing.stdout or "[]")
            entry = next((a for a in found if a.get("pid") == os.getpid()), None)
            if entry is not None and entry.get("cwd") == str(project_dir):
                break
            time.sleep(0.2)

        assert entry is not None, "no roster entry for this test process's pid"
        assert entry["name"] == f"omp-{project_dir.name}", entry
        assert entry["cwd"] == str(project_dir), entry
    finally:
        child_stdin.close()
        proc.wait(timeout=10)


def test_a_client_that_refuses_roots_list_keeps_its_pid_name(tmp_path):
    """A client that declares the capability but errors the call is not
    fatal -- the peer keeps its pid-derived name, and the server keeps
    answering ordinary requests afterward.
    """
    env = _env(tmp_path)
    proc, next_frame, _ = _spawn_mcp(env)
    child_stdin = proc.stdin
    assert child_stdin is not None

    try:
        child_stdin.write(json.dumps(INIT_WITH_ROOTS) + "\n")
        child_stdin.flush()
        next_frame()

        child_stdin.write(json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }) + "\n")
        child_stdin.flush()

        roots_request = next_frame()
        assert roots_request.get("method") == "roots/list"

        child_stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": roots_request["id"],
            "error": {"code": -32601, "message": "roots not actually supported"},
        }) + "\n")
        child_stdin.flush()

        # No reply is expected for a response frame. Poll the roster
        # directly, the same way the success-path test does, both to give
        # the refusal time to be processed and to prove the server is
        # still alive and answering afterward -- `list` only succeeds
        # against a roster a live process still owns.
        deadline = time.time() + 10
        entry = None
        while time.time() < deadline:
            assert proc.poll() is None, (
                f"server exited after a roots/list refusal, rc={proc.returncode}"
            )
            listing = subprocess.run(
                [sys.executable, "-m", "agent_bus", "list", "--json"],
                env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
            )
            assert listing.returncode == 0, listing.stderr
            found = json.loads(listing.stdout or "[]")
            entry = next((a for a in found if a.get("pid") == os.getpid()), None)
            if entry is not None:
                break
            time.sleep(0.2)

        assert entry is not None, "no roster entry for this test process's pid"
        assert entry["name"] == f"omp-{os.getpid()}", entry
    finally:
        child_stdin.close()
        proc.wait(timeout=10)


def test_a_roster_subscriber_gets_no_notification_by_default(tmp_path):
    """Roster change notifications are muted by default
    (mcp_server.ROSTER_NOTIFICATIONS_ENABLED) -- a roster churns on every
    peer's join/rename/leave, not just mail addressed to this connection,
    and a client that auto-subscribes to everything a server advertises as
    subscribable turned that into a notification per peer event. The
    subscribe call itself still succeeds; it just never fires.

    Before the mute this same sequence (subscribe, then a peer joins and
    leaves) produced two `notifications/resources/updated` frames -- see
    the history of this test, formerly
    test_a_roster_subscriber_is_notified_when_a_peer_joins_and_leaves.
    """
    env = _env(tmp_path)
    proc, next_frame, no_frame_within = _spawn_mcp(env)
    child_stdin = proc.stdin
    assert child_stdin is not None

    holder = subprocess.Popen(["sleep", "60"])
    try:
        child_stdin.write(json.dumps(INIT) + "\n")
        child_stdin.flush()
        init_reply = next_frame()
        assert init_reply["result"]["capabilities"]["resources"]["subscribe"] is True

        child_stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "resources/subscribe",
            "params": {"uri": "agentbus://roster"},
        }) + "\n")
        child_stdin.flush()
        sub_reply = next_frame()
        assert "error" not in sub_reply, sub_reply

        # Idle now -- a second process registers a new peer.
        reg = subprocess.run(
            [sys.executable, "-m", "agent_bus", "register",
             "--name", "roster-joiner", "--kind", "omp", "--pid", str(holder.pid)],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        assert reg.returncode == 0, reg.stderr

        assert no_frame_within(), "roster notification fired while muted"

        # And leaving stays quiet too.
        leave = subprocess.run(
            [sys.executable, "-m", "agent_bus", "leave", "--name", "roster-joiner"],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        assert leave.returncode == 0, leave.stderr

        assert no_frame_within(), "roster notification fired while muted"
    finally:
        child_stdin.close()
        proc.wait(timeout=10)
        holder.kill()
        holder.wait()


def test_inbox_and_roster_subscriptions_stay_independent_over_stdio(tmp_path):
    """Subscribing to both resources on one connection must not conflate
    them -- a roster-only change fires nothing (roster notifications are
    muted by default, see test_a_roster_subscriber_gets_no_notification_by_default),
    and a subsequent inbox-only change still fires the inbox notification
    on its own, even though both share the same underlying multi-directory
    Waiter.
    """
    env = _env(tmp_path)
    proc, next_frame, no_frame_within = _spawn_mcp(env)
    child_stdin = proc.stdin
    assert child_stdin is not None

    holder = subprocess.Popen(["sleep", "60"])
    try:
        child_stdin.write(json.dumps(INIT) + "\n")
        child_stdin.flush()
        next_frame()

        # Register THIS connection under the server's own pid, exactly as
        # test_a_subscribed_client_is_notified_of_new_mail_while_idle does,
        # so the inbox resource resolves to a real identity.
        reg_self = subprocess.run(
            [sys.executable, "-m", "agent_bus", "register",
             "--name", "both-subscriber", "--kind", "omp", "--pid", str(proc.pid)],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        assert reg_self.returncode == 0, reg_self.stderr

        for i, uri in enumerate(("agentbus://inbox", "agentbus://roster")):
            child_stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": 100 + i, "method": "resources/subscribe",
                "params": {"uri": uri},
            }) + "\n")
            child_stdin.flush()
            reply = next_frame()
            assert "error" not in reply, reply

        # Roster-only change first.
        reg_peer = subprocess.run(
            [sys.executable, "-m", "agent_bus", "register",
             "--name", "roster-only-peer", "--kind", "omp", "--pid", str(holder.pid)],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        assert reg_peer.returncode == 0, reg_peer.stderr

        assert no_frame_within(), "roster notification fired while muted"

        # Inbox-only change next.
        send = subprocess.run(
            [sys.executable, "-m", "agent_bus", "send", "both-subscriber",
             "-m", "hi", "--summary", "hi"],
            env=env, cwd=REPO, capture_output=True, text=True, timeout=30,
        )
        assert send.returncode == 0, send.stderr

        notice2 = next_frame()
        assert notice2["params"]["uri"] == "agentbus://inbox", (
            "an inbox-only change must not also claim the roster changed"
        )
    finally:
        child_stdin.close()
        proc.wait(timeout=10)
        holder.kill()
        holder.wait()


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
