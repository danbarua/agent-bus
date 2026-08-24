"""UDS listen/send-uds tests. Use overrides via direct env, never touch real ~/.claude or /tmp/cc-socks.
Uses fake pids for simulated agents to keep paths short and avoid cross-test pid collisions in same process.
"""
import json
import os
import socket
import threading
import time

from agent_bus.adapters.discovery import claude
from agent_bus.uds import run_listen, send_uds_frame


def test_listen_receives_auth_user_and_acks():
    import secrets
    # use short random path under /tmp to keep AF_UNIX paths short (< ~100 chars) and unique
    rand = secrets.token_hex(4)
    base = f"/tmp/ab{rand}"
    sock_d = f"{base}/s"
    sess_d = f"{base}/c"
    bus_home = f"{base}/b"
    for d in (sock_d, sess_d, bus_home):
        os.makedirs(d, exist_ok=True)

    old = {k: os.environ.get(k) for k in ("AGENT_BUS_SOCK_DIR", "AGENT_BUS_SESSIONS_DIR", "AGENT_BUS_HOME")}
    os.environ["AGENT_BUS_SOCK_DIR"] = sock_d
    os.environ["AGENT_BUS_SESSIONS_DIR"] = sess_d
    os.environ["AGENT_BUS_HOME"] = bus_home

    sock_path = None
    sess_path = None
    pid = None

    # pre-clean any prior in these test dirs (from previous failed runs)
    for pfx in (sock_d, sess_d):
        try:
            for fn in os.listdir(pfx):
                if fn.endswith((".sock", ".json", ".key")):
                    fp = os.path.join(pfx, fn)
                    try:
                        os.unlink(fp)
                    except Exception:
                        pass
        except Exception:
            pass
    errors = []

    # Pre-register so that when listen calls send_message, find_entry succeeds
    # and inbox_ok stays True, allowing status to be sent on dial-back.
    from agent_bus.store import register
    register("test-bus", "other", pid=os.getpid(), home=bus_home)

    def runner():
        try:
            run_listen(name="test-bus")
        except Exception as e:
            errors.append(str(e))

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    # discover the sock listen actually created (uses its real getpid(), dirs overridden via AGENT_BUS_*)
    for _ in range(100):
        try:
            for fn in os.listdir(sock_d):
                if fn.endswith(".sock"):
                    sock_path = os.path.join(sock_d, fn)
                    pid = int(os.path.splitext(os.path.basename(sock_path))[0])
                    sess_path = os.path.join(sess_d, f"{pid}.json")
                    break
            if sock_path:
                break
        except Exception:
            pass
        time.sleep(0.02)
    assert sock_path, f"listen did not create socket in {sock_d}"
    # The socket is bound before the session file is written: identity comes from
    # register(), which runs after bind so a bind failure never leaves a stale
    # registration. So poll rather than assume the file is already there.
    for _ in range(150):
        if os.path.exists(sess_path):
            break
        time.sleep(0.02)
    assert os.path.exists(sess_path), f"session not at {sess_path}"
    time.sleep(0.1)  # allow reach accept loop

    with open(sess_path) as f:
        sess = json.load(f)
    assert sess["pid"] == pid
    assert sess["name"] == "test-bus"
    assert sess["messagingSocketPath"] == sock_path
    assert sess["peerProtocol"] == 1
    assert "notify_idle" in sess["peerFeatures"]

    # verify our published key (for status-back auth)
    key_path = None
    for fn in os.listdir(sess_d):
        if fn.startswith(f"{pid}.") and fn.endswith(".key"):
            key_path = os.path.join(sess_d, fn)
            break
    assert key_path and os.path.exists(key_path), f"key not written for {pid}"
    mode = os.stat(key_path).st_mode & 0o777
    assert mode == 0o600, f"key mode {oct(mode)} not 0600"
    with open(key_path) as kf:
        kdata = json.load(kf)
    assert "peerToken" in kdata
    assert "procStart" in kdata
    # do not print token
    import hashlib
    import socket as _socket

    # Stand up a fake SENDER peer: its own socket plus a published .key, so the
    # listener can look up its peerToken and authenticate the dial-back.
    # The ack does NOT come back on the inbound connection -- see UDS-protocol.md s4.
    sender_pid = 91234
    sender_sock = os.path.join(sock_d, f"{sender_pid}.sock")
    try:
        if os.path.exists(sender_sock):
            os.unlink(sender_sock)
    except Exception:
        pass

    sender_token = secrets.token_hex(16)
    sender_key = os.path.join(
        sess_d,
        f"{sender_pid}.{hashlib.sha256(sender_sock.encode('utf-8')).hexdigest()}.key",
    )
    with open(sender_key, "w") as skf:
        json.dump({"peerToken": sender_token, "procStart": "test"}, skf)
    os.chmod(sender_key, 0o600)

    sender_srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sender_srv.bind(sender_sock)
    sender_srv.listen(1)
    sender_srv.settimeout(5.0)

    dialback = []

    def dialback_acceptor():
        try:
            conn, _ = sender_srv.accept()
            conn.settimeout(3.0)
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            dialback.append(buf.decode("utf-8", errors="replace"))
            conn.close()
        except Exception as e:
            dialback.append(f"__error__ {e}")
        finally:
            try:
                sender_srv.close()
            except Exception:
                pass

    dt = threading.Thread(target=dialback_acceptor, daemon=True)
    dt.start()

    test_msg_id = "test-ack-uuid-1234"
    frame = json.dumps({
        "msgV": 1,
        "msg_id": test_msg_id,
        "type": "user",
        "message": {"role": "user", "content": "hello with id for ack test"},
        "from": f"uds:{sender_sock}",
    }) + "\n"
    inbound_token = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    auth_frame = json.dumps({"type": "auth", "token": inbound_token}) + "\n"
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(1.0)
    s.connect(sock_path)
    s.sendall(auth_frame.encode("utf-8"))
    s.sendall(frame.encode("utf-8"))

    # The listener must NEVER write on the inbound conn. Doing so made the real
    # Claude peer RST on close and report the send as failed (root cause #4).
    try:
        stray = s.recv(4096)
    except TimeoutError:
        stray = b""
    assert stray == b"", f"listener wrote on the inbound conn: {stray!r}"
    try:
        s.close()
    except Exception:
        pass
    # The ack arrives on the dial-back: auth frame FIRST, then the status frame.
    dt.join(timeout=5)
    assert not dt.is_alive(), "dial-back never arrived"
    assert dialback, "no dial-back data captured"
    assert not dialback[0].startswith("__error__"), f"{dialback[0]}; listen_errors={errors}"
    dl = [l for l in dialback[0].split("\n") if l.strip()]
    assert len(dl) == 2, f"expected auth + status frames, got {dl}"

    assert json.loads(dl[0]) == {"type": "auth", "token": sender_token}, \
        "dial-back must send auth as its first line"

    ack = json.loads(dl[1])
    assert ack.get("type") == "control", f"expected type=control, got {ack}"
    assert ack.get("action") == "peer_message_status", f"expected action=peer_message_status, got {ack}"
    assert ack.get("orig_msg_id") == test_msg_id
    assert ack.get("status") == "delivered"
    assert ack.get("from") == f"uds:{sock_path}"

    # Inbound auth tokens must never be persisted. The redaction guard was once
    # deleted while its body was left as unreachable code, leaking peer tokens
    # in cleartext to stdout and captures; nothing caught it.
    cap_path = os.path.join(bus_home, "captures", f"{pid}.jsonl")
    for _ in range(60):
        if os.path.exists(cap_path) and inbound_token in open(cap_path).read():
            break
        time.sleep(0.02)
    if os.path.exists(cap_path):
        blob = open(cap_path).read()
        assert inbound_token not in blob, "auth token was written to the capture file"
        assert "<redacted>" in blob, "auth frame was not redacted in the capture file"

    captured = False
    for _ in range(60):
        if os.path.exists(cap_path):
            try:
                with open(cap_path) as cf:
                    caps = [json.loads(l) for l in cf if l.strip()]
                has = any("hello from test uds" in str(c) or "user" in str(c.get("parsed", {})) for c in caps)
                if has:
                    captured = True
                    break
            except Exception:
                pass
        time.sleep(0.02)
    assert captured

    with open(cap_path) as cf:
        caps = [json.loads(l) for l in cf if l.strip()]
    has = any("hello from test uds" in str(c) or "user" in str(c.get("parsed", {})) for c in caps)
    assert has

    for p in (sock_path, sess_path, key_path, sender_sock, sender_key):
        try:
            if p and os.path.exists(p):
                os.unlink(p)
        except Exception:
            pass
    t.join(timeout=1.5)

    # restore
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_send_uds_writes_exact_frame():
    # dummy server on short path
    pid = 424242
    sock_d = f"/tmp/ab-uds-dummy-{pid}"
    os.makedirs(sock_d, exist_ok=True)

    sock_path = os.path.join(sock_d, f"{pid}.sock")
    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except Exception:
        pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    server.settimeout(3.0)

    received = []

    def acceptor():
        try:
            conn, _ = server.accept()
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            received.append(data.decode("utf-8", errors="replace"))
            conn.close()
        finally:
            try:
                server.close()
            except Exception:
                pass

    th = threading.Thread(target=acceptor, daemon=True)
    th.start()

    send_uds_frame(sock_path, "test content for frame")

    th.join(timeout=2)
    assert not th.is_alive()
    assert len(received) == 1
    lines = [l for l in received[0].split("\n") if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"type": "auth", "token": ""}
    u = json.loads(lines[1])
    assert u["type"] == "user"
    assert u["message"]["role"] == "user"
    assert u["message"]["content"] == "test content for frame"

    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except Exception:
        pass


def test_listen_publishes_claude_compatible_teammate(tmp_path, monkeypatch):
    """Grok listen publishes a Claude-shaped session + socket under the host pid."""
    import subprocess
    import secrets

    host = subprocess.Popen(["sleep", "30"])
    # short paths under /tmp; do not use tmp_path for .sock/.json to avoid AF_UNIX path length limit on macOS
    rand = secrets.token_hex(4)
    base = f"/tmp/ab{rand}"
    sock_d = f"{base}/s"
    sess_d = f"{base}/c"
    bus_home = f"{base}/b"
    for d in (sock_d, sess_d, bus_home):
        os.makedirs(d, exist_ok=True)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", sock_d)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", sess_d)
    monkeypatch.setenv("AGENT_BUS_HOME", bus_home)
    errors = []

    def runner():
        try:
            run_listen(name="exo-grok", pid=host.pid)
        except Exception as e:
            errors.append(str(e))

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    # Wait for ANY .sock in AGENT_BUS_SOCK_DIR (listen publishes under its own getpid(), not host.pid)
    sock_path = None
    pid = None
    for _ in range(100):
        try:
            for fn in os.listdir(sock_d):
                if fn.endswith(".sock"):
                    sock_path = os.path.join(sock_d, fn)
                    pid = int(os.path.splitext(os.path.basename(sock_path))[0])
                    break
            if sock_path:
                break
        except Exception:
            pass
        time.sleep(0.02)
    sess_path = os.path.join(sess_d, f"{pid}.json") if pid else None
    # The socket is bound before identity is resolved and the session written,
    # so seeing the socket does not mean the session file exists yet. That gap
    # widened when register() started recording procStart via ps, which is a
    # subprocess call in the startup path.
    for _ in range(250):
        if sess_path and os.path.exists(sess_path):
            break
        time.sleep(0.02)
    try:
        assert sock_path, f"listen did not create socket in {sock_d}: {errors}"
        assert os.path.exists(sess_path), f"session missing: {errors}"
        with open(sess_path) as f:
            sess = json.load(f)
        # sess pid == os.getpid() (same process as the thread running listen)
        assert sess["pid"] == os.getpid()
        # sock name is <getpid()>.sock NOT host.pid
        assert os.path.basename(sock_path) == f"{os.getpid()}.sock"
        assert sess["name"] == "exo-grok"
        assert sess["messagingSocketPath"] == sock_path
        assert sess["kind"] == "interactive"
        assert sess["entrypoint"] == "cli"
        assert sess["peerProtocol"] == 1
        assert "notify_idle" in sess["peerFeatures"]
        # agentBus true if present
        if "agentBus" in sess:
            assert sess["agentBus"] is True
        found = claude.discover()
        # claude.discover() finds name exo-grok with pid == getpid()
        assert any(a["name"] == "exo-grok" and a["pid"] == os.getpid() for a in found)
    finally:
        host.kill()
        host.wait()


def test_listen_advertises_the_registered_name_not_the_requested_one(tmp_path, monkeypatch):
    """One bus, one identity.

    When the requested name is already held by a live agent, register() assigns
    name-2. The session file Claude's ListAgents reads must carry that assigned
    name -- previously it advertised the requested name, so the roster and the
    socket disagreed and two listeners could advertise the same identity.
    """
    import secrets
    import subprocess

    from agent_bus.store import list_agents, register

    holder = subprocess.Popen(["sleep", "30"])
    rand = secrets.token_hex(4)
    base = f"/tmp/ab{rand}"
    sock_d, sess_d, bus_home = f"{base}/s", f"{base}/c", f"{base}/b"
    for d in (sock_d, sess_d, bus_home):
        os.makedirs(d, exist_ok=True)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", sock_d)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", sess_d)
    monkeypatch.setenv("AGENT_BUS_HOME", bus_home)

    # an unrelated live agent already owns the plain name
    taken = register("contested", "other", pid=holder.pid)
    assert taken.name == "contested"

    errors = []

    def runner():
        try:
            run_listen(name="contested")
        except Exception as e:
            errors.append(str(e))

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    sess_file = None
    for _ in range(150):
        for fn in os.listdir(sess_d):
            if fn.endswith(".json"):
                sess_file = os.path.join(sess_d, fn)
                break
        if sess_file:
            break
        time.sleep(0.02)
    assert sess_file, f"listener published no session file; errors={errors}"

    with open(sess_file) as f:
        advertised = json.load(f)["name"]

    assert advertised == "contested-2", (
        f"session file advertises {advertised!r}; must match the name register() "
        f"assigned, not the requested one"
    )
    names = {a.name for a in list_agents()}
    assert {"contested", "contested-2"} <= names, names

    holder.kill()


def test_listen_registers_under_its_host_pid(tmp_path, monkeypatch):
    """`agent-bus listen --pid <host>` must be findable by a sibling sender.

    send() resolves "our socket" by walking its own ancestors for
    listeners/<ancestor>.pid. start_uds_listen() writes that file when it spawns
    a listener; run_listen did not, so a hand-started listener published a
    perfectly good socket that send could never locate. A harness with no MCP
    server has only this CLI, so a shell-only peer could receive but not send.
    """
    import threading
    import time

    from agent_bus.uds import run_listen

    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", str(tmp_path / "socks"))
    os.makedirs(str(tmp_path / "sessions"), exist_ok=True)
    os.makedirs(str(tmp_path / "socks"), exist_ok=True)

    host = os.getpid()
    t = threading.Thread(target=run_listen, args=("host-pid-test", host), daemon=True)
    t.start()
    pid_file = os.path.join(home, "listeners", f"{host}.pid")
    for _ in range(50):
        if os.path.exists(pid_file):
            break
        time.sleep(0.1)
    assert os.path.exists(pid_file), "listen did not register under its host pid"
    assert int(open(pid_file).read().strip()) > 0
