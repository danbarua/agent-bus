"""UDS listen/send-uds tests. Use overrides via direct env, never touch real ~/.claude or /tmp/cc-socks.
Uses fake pids for simulated agents to keep paths short and avoid cross-test pid collisions in same process.
"""
import json
import os
import socket
import threading
import time

import pytest

from agent_bus.uds import run_listen, send_uds_frame


def test_listen_receives_auth_user_and_acks():
    # fake pid for simulated listen pid (short path, no collision with real pytest pid across tests)
    pid = 98765
    base = f"/tmp/ab-uds-{pid}"
    sock_d = f"{base}-s"
    sess_d = f"{base}-c"
    bus_home = f"{base}-b"
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
    import stat
    mode = os.stat(key_path).st_mode & 0o777
    assert mode == 0o600, f"key mode {oct(mode)} not 0600"
    with open(key_path) as kf:
        kdata = json.load(kf)
    assert "peerToken" in kdata
    assert "procStart" in kdata
    # do not print token
    import hashlib
    import secrets
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
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(1.0)
    s.connect(sock_path)
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
    assert not dialback[0].startswith("__error__"), dialback[0]
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

    cap_path = os.path.join(bus_home, "captures", f"{pid}.jsonl")
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
