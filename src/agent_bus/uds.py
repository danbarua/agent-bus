"""UDS peer support: listen makes this host a peer on Claude Code's protocol.

We are the SERVER side. We publish a sessions/<pid>.json and bind our own socket.
We write dial-back status acks and support outbound send-peer to other agents' sockets.


Env overrides (for tests, NEVER for live):
  AGENT_BUS_SOCK_DIR     -> instead of /tmp/cc-socks
  AGENT_BUS_SESSIONS_DIR -> instead of ~/.claude/sessions
"""
from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import sys
import threading
import time
import uuid

from . import address, log
from .paths import claude_sessions_dir
from .protocol import now_iso
from .store import (
    ancestor_pids,
    capture_path,
    ensure_dirs,
    get_home,
    get_live_roster,
    is_pid_alive,
    register,
    send_message,
)

# How long a listener waits for the host that spawned it to appear on the bus
# before giving up and claiming a name of its own. Only the race matters here:
# a host that is never going to register does not get slower to detect.
ADOPT_TIMEOUT = 5.0


def _sock_dir() -> str:
    return os.environ.get("AGENT_BUS_SOCK_DIR", "/tmp/cc-socks")


def _sessions_dir() -> str:
    """Kept as a name because callers import it; the logic lives in paths.py."""
    return claude_sessions_dir()


def _epoch_ms() -> int:
    return int(time.time() * 1000)


def _proc_start_str() -> str:
    # e.g. "Fri Aug 21 21:42:08 2026"
    return time.strftime("%a %b %d %H:%M:%S %Y")


def _key_path(pid: int, sock_path: str, sess_dir: str) -> str:
    """Compute the .key filename as {pid}.{sha256(sock_path).hexdigest()}.key"""
    h = hashlib.sha256(sock_path.encode("utf-8")).hexdigest()
    return os.path.join(sess_dir, f"{pid}.{h}.key")


def _write_our_session(
    pid: int,
    name: str,
    sock_path: str,
    sess_dir: str,
    session_id: str,
) -> str:
    """Write our session file where Claude Code looks for its own.

    That is `~/.claude/sessions/<pid>.json` (AGENT_BUS_SESSIONS_DIR overrides
    it for tests), plus a 0600 `.key` beside it. Nothing subscribes -- Claude
    reads the directory when its ListAgents runs -- so this is the whole of
    what "publishing" means here.

    `session_id` is the caller's, not minted here: the caller registers the
    same address as an alias, and the two have to agree.
    """
    os.makedirs(sess_dir, exist_ok=True)
    session_path = os.path.join(sess_dir, f"{pid}.json")
    session = {
        "pid": pid,
        "sessionId": session_id,
        "cwd": os.getcwd(),
        "startedAt": _epoch_ms(),
        "procStart": _proc_start_str(),
        "version": "2.1.239",
        "peerProtocol": 1,
        "peerFeatures": ["notify_idle"],
        "kind": "interactive",
        "entrypoint": "cli",
        "messagingSocketPath": sock_path,
        "agentBus": True,
        "name": name,
        "nameSince": _epoch_ms(),
        "updatedAt": _epoch_ms(),
        "status": "idle",
        "statusUpdatedAt": _epoch_ms(),
    }
    tmp = session_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)
    os.replace(tmp, session_path)

    # also publish our peer key for incoming status-back auth from others
    key_path = _key_path(pid, sock_path, sess_dir)
    peer_token = secrets.token_hex(16)
    key = {
        "peerToken": peer_token,
        "procStart": session["procStart"],
    }
    ktmp = key_path + ".tmp"
    with open(ktmp, "w", encoding="utf-8") as f:
        json.dump(key, f, indent=2, sort_keys=True)
    os.replace(ktmp, key_path)
    with contextlib.suppress(Exception):
        os.chmod(key_path, 0o600)

    return session_path

def _advertised_name(our_sock: str, default: str = "agent-bus") -> str:
    """The name we advertise on the bus, read back from our own session file.

    run_listen writes that file from the name register() assigned, so this is
    the same identity the roster and ListAgents see -- not a separate one.
    """
    try:
        pid = int(os.path.basename(our_sock).split(".")[0])
        with open(os.path.join(_sessions_dir(), f"{pid}.json"), encoding="utf-8") as f:
            return json.load(f).get("name") or default
    except Exception:
        return default


def _cleanup(
    sock_path: str,
    session_path: str,
    server_sock: socket.socket | None = None,
    key_path: str | None = None,
) -> None:
    if server_sock:
        with contextlib.suppress(Exception):
            server_sock.close()
    for p in (sock_path, session_path, key_path):
        try:
            if p and os.path.exists(p):
                os.unlink(p)
        except Exception:
            pass
def run_listen(
    name: str = "agent-bus",
    pid: int | None = None,
    inbox_name: str | None = None,
) -> None:
    """Run the UDS listener. Blocks until signal. Cleans up on exit.

    Publishes to real (or overridden) Claude sessions dir so ListAgents sees us.
    Binds our socket. Receives frames, logs + captures, acks with a control
    peer_message_status on mid.

    The listener always publishes under its own os.getpid() (the binder pid) so
    getpeereid() from Claude matches the sessions/<pid>.json we wrote and the
    bound socket path. `pid` (from --pid) is WATCH-PID ONLY: if provided and
    that pid exits, listener exits+cleans up. It is NOT the advertised pid.
    """
    log.configure()
    log.identify(surface="listen")
    watch_pid = int(pid) if pid else None
    publish_pid = os.getpid()

    # Register under the host pid, so a sibling process can find this listener.
    # start_uds_listen() writes this file when it spawns a listener; run_listen
    # did not, so `agent-bus listen --pid $$` published a working socket that
    # `send` could never locate -- it resolves "our socket" by walking its own
    # ancestors for listeners/<ancestor>.pid. A harness with no MCP server has
    # only this CLI, so without it a shell-only peer can receive but not send.
    host_pid_file = None
    if watch_pid:
        try:
            ldir = os.path.join(get_home(), "listeners")
            os.makedirs(ldir, exist_ok=True)
            host_pid_file = os.path.join(ldir, f"{watch_pid}.pid")
            with open(host_pid_file, "w", encoding="utf-8") as f:
                f.write(str(publish_pid) + "\n")
        except OSError:
            host_pid_file = None

    sock_d = _sock_dir()
    os.makedirs(sock_d, exist_ok=True)
    with contextlib.suppress(Exception):
        os.chmod(sock_d, 0o700)

    sock_path = os.path.join(sock_d, f"{publish_pid}.sock")
    if os.path.exists(sock_path):
        with contextlib.suppress(Exception):
            os.unlink(sock_path)

    ensure_dirs()  # for captures
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(sock_path)
    with contextlib.suppress(Exception):
        os.chmod(sock_path, 0o600)
    server.listen(8)
    server.settimeout(2.0)

    # One bus, one identity. register() is the source of truth: it may rename on
    # collision (name -> name-2), and everything we advertise must be that assigned
    # name -- the session file Claude's ListAgents reads, the from-name on outbound
    # frames, and the inbox we persist to. The UDS side is not a second identity.
    requested = inbox_name or name
    entry = None
    if watch_pid:
        # Started for a host that session_start() already registered. Adopt that
        # entry: registering again would create a SECOND identity for one peer and
        # collide on the name, landing as "<name>-2". One peer, one socket, one
        # name -- so a sender can just address it by name.
        #
        # Waited for, because we are a detached child racing the parent that
        # spawned us. Losing that race is not harmless: we register the same
        # name under our own pid, the parent's registration is then renamed to
        # "<name>-2", and the caller is left holding an id that no longer
        # matches the name it asked for. That is what a bridge saw as itself
        # appearing in the roster it publishes.
        deadline = time.monotonic() + ADOPT_TIMEOUT
        while True:
            entry = next((e for e in get_live_roster() if e.pid == watch_pid), None)
            if entry is not None:
                print(f"[listen] adopting host registration {entry.name} (pid {watch_pid})")
                break
            if time.monotonic() >= deadline:
                print(f"[listen] no registration for pid {watch_pid} after "
                      f"{ADOPT_TIMEOUT:.0f}s; registering our own")
                break
            time.sleep(0.05)
    if entry is None:
        entry = register(requested, "other", pid=publish_pid)
        if entry.name != requested:
            print(f"[listen] registered as {entry.name} (requested {requested})")
    bus_name = entry.name
    # Deliver inbound frames by entry ID, not by name. A peer can rename itself
    # after we start (the register tool does exactly that), and a cached name
    # then resolves to nothing -- the message authenticates, parses, and is
    # dropped with "no such agent".
    bus_id = entry.id

    # The address we are about to publish, recorded as an alias so a listing
    # resolves it to this entry rather than to a second agent. session_start
    # does the same for the harness's own session address.
    register(bus_name, entry.kind, pid=entry.pid,
             aliases=[str(address.mint("agentbus", address.SESSION, bus_id))])

    sess_d = _sessions_dir()
    session_path = _write_our_session(
        publish_pid, bus_name, sock_path, sess_d, bus_id
    )
    key_path = _key_path(publish_pid, sock_path, sess_d)

    # Read back the token we just published. Inbound frames must present it.
    # Until this existed, _process_frame matched type == "auth", redacted the
    # token for logging and carried on without ever comparing it -- so any
    # caller that could reach the socket was authenticated.
    our_token = None
    try:
        with open(key_path, encoding="utf-8") as _kf:
            our_token = json.load(_kf).get("peerToken")
    except (OSError, ValueError) as _e:
        print(f"[listen] WARNING: cannot read our own peerToken ({_e}); refusing all inbound")

    capf_path = capture_path(publish_pid)
    print(f"[listen] pid={publish_pid} name={bus_name}")
    print(f"[listen] socket={sock_path}")
    print(f"[listen] session={session_path}")
    print(f"[listen] capture={capf_path}")
    print("[listen] waiting for connections (newline json frames)...")

    def _process_frame(conn: socket.socket, ln: str, cap_path: str, state: dict) -> bool:
        """Process one inbound line. Returns False to drop the connection."""
        parsed = None
        try:
            parsed = json.loads(ln)
        except Exception as e:
            print(f"[recv] {ln}")
            print(f"[parse-error] {e}")
            try:
                entry = {"ts": now_iso(), "raw": ln}
                with open(cap_path, "a", encoding="utf-8") as cf:
                    cf.write(json.dumps(entry) + "\n")
            except Exception:
                pass
            return bool(state.get("authed"))

        is_auth = isinstance(parsed, dict) and parsed.get("type") == "auth"

        # Authenticate first, per connection. The first frame must be an auth
        # frame carrying our published token; nothing else is processed until
        # it is. Filesystem permissions (0600 socket in a 0700 dir) were the
        # only control before this.
        if not state.get("authed"):
            if not is_auth:
                print("[auth] rejected: frame arrived before a valid auth frame")
                return False
            if not our_token or parsed.get("token") != our_token:
                print("[auth] rejected: token does not match our published key")
                return False
            state["authed"] = True

        if is_auth:
            red = {"type": "auth", "token": "<redacted>"}
            print(f"[recv] {json.dumps(red)}")
            print(f"[parsed] {red}")
            cap_raw = json.dumps(red)
            cap_parsed = red
        else:
            print(f"[recv] {ln}")
            print(f"[parsed] {parsed}")
            cap_raw = ln
            cap_parsed = parsed

        # capture always (sanitized for auth)
        try:
            entry = {"ts": now_iso(), "raw": cap_raw}
            if cap_parsed is not None:
                entry["parsed"] = cap_parsed
            with open(cap_path, "a", encoding="utf-8") as cf:
                cf.write(json.dumps(entry) + "\n")
        except Exception:
            pass

        # inbound user frames to file inbox, addressed by the rename-proof entry id
        inbox_ok = True
        if isinstance(parsed, dict) and parsed.get("type") == "user":
            msg_part = parsed.get("message") or {}
            content = (
                msg_part.get("content")
                if isinstance(msg_part, dict)
                else parsed.get("content", "")
            )
            text = str(content) if content is not None else ""
            m = re.search(
                r"<cross-session-message[^>]*>(.*?)</cross-session-message>",
                text,
                re.DOTALL | re.IGNORECASE,
            )
            if m:
                text = m.group(1).strip()
            from_name = (
                parsed.get("from-name")
                or parsed.get("from_name")
                or parsed.get("from")
                or "peer"
            )
            mfn = re.search(r'from-name="([^"]*)"', str(content) or "")
            if mfn:
                from_name = mfn.group(1)
            try:
                send_message(to=bus_id, text=text or "", from_name=from_name, from_kind="other")
            except Exception as ex:
                print(f"[listen] failed to persist inbound user frame to {bus_id}: {ex}")
                inbox_ok = False

        mid = None
        from_val = None
        if isinstance(parsed, dict):
            mid = parsed.get("msg_id") or parsed.get("id")
            if mid is None and isinstance(parsed.get("message"), dict):
                mid = parsed["message"].get("id") or parsed["message"].get("msg_id")
            from_val = parsed.get("from")
        status = None
        if mid and (inbox_ok or not (isinstance(parsed, dict) and parsed.get("type") == "user")):
            status = {
                "msgV": 1,
                "type": "control",
                "action": "peer_message_status",
                "orig_msg_id": str(mid),
                "status": "delivered",
                "from": f"uds:{sock_path}",
            }
        try:
            if status:  # noqa: SIM102  # the comment below explains the gap
                # DO NOT send a same-conn status frame on the inbound conn.
                # Claude never reads one; only dial-back works. That omission is
                # why these two ifs are not collapsed -- the comment belongs
                # between them, and reads as nonsense anywhere else.
                if from_val:
                    path = None
                    if isinstance(from_val, str):
                        if from_val.startswith("uds:"):
                            path = from_val[4:]
                        elif from_val.startswith("/tmp/cc-socks/"):
                            path = from_val
                        else:
                            sd = _sock_dir()
                            if sd and (from_val.startswith((sd + "/", sd)) or from_val == sd):
                                path = from_val
                    if path:
                        our_sock = sock_path
                        if path == our_sock:
                            print(f"[status-back] path={path} skip (our own socket)")
                        else:
                            token = None
                            try:
                                spid = int(os.path.basename(path).split(".")[0])
                                ssdir = _sessions_dir()
                                skey = _key_path(spid, path, ssdir)
                                if os.path.exists(skey):
                                    with open(skey, encoding="utf-8") as kf:
                                        token = json.load(kf).get("peerToken")
                                if not token and os.path.isdir(ssdir):
                                    for fn in os.listdir(ssdir):
                                        if fn.startswith(f"{spid}.") and fn.endswith(".key"):
                                            try:
                                                kf_path = os.path.join(ssdir, fn)
                                                with open(kf_path, encoding="utf-8") as kf:
                                                    token = json.load(kf).get("peerToken")
                                                    if token:
                                                        break
                                            except Exception:
                                                pass
                            except Exception:
                                pass
                            if not token:
                                print(f"[status-back] path={path} err: no peerToken")
                            else:
                                s = None
                                try:
                                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                                    s.settimeout(2.0)
                                    s.connect(path)
                                    auth = json.dumps({"type": "auth", "token": token}) + "\n"
                                    print(f"[status-back] auth token_len={len(token)}")
                                    s.sendall(auth.encode("utf-8"))
                                    sdata = (json.dumps(status) + "\n").encode("utf-8")
                                    s.sendall(sdata)
                                    try:
                                        s.shutdown(socket.SHUT_WR)
                                        s.settimeout(1.0)
                                        while s.recv(4096):
                                            pass
                                    except Exception:
                                        pass
                                    finally:
                                        if s:
                                            with contextlib.suppress(Exception):
                                                s.close()
                                    print(f"[status-back] path={path} ok")
                                    print(f"[sent-bytes] {sdata!r}")
                                except Exception as e:
                                    print(f"[status-back] path={path} err: {e}")
                                    if s:
                                        with contextlib.suppress(Exception):
                                            s.close()
        except Exception as se:
            print(f"[send-error] {se}")
        return True

    def handle(conn: socket.socket, peer: tuple) -> None:
        state: dict = {"authed": False}
        try:
            conn.settimeout(30)
            buf: bytes = b""
            while True:
                try:
                    chunk = conn.recv(16384)
                except TimeoutError:
                    break
                if not chunk:
                    break
                buf += chunk
                # process every complete line immediately (ack without waiting for close/EOF)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    ln = line.decode("utf-8", errors="replace").strip()
                    if ln and not _process_frame(conn, ln, capf_path, state):
                        return

            # on close or timeout: flush trailing partial line (no final \n)
            if buf:
                ln = buf.decode("utf-8", errors="replace").strip()
                if ln and not _process_frame(conn, ln, capf_path, state):
                    return
        except Exception as e:
            print(f"[client-error] {e}")
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    def _atexit():
        # Ours to remove: a stale entry points at a listener that is gone, and
        # send() would resolve a socket nobody is bound to.
        if host_pid_file:
            with contextlib.suppress(OSError):
                os.unlink(host_pid_file)
        _cleanup(sock_path, session_path, server, key_path)

    atexit.register(_atexit)

    def _on_signal(signum, frame):
        print(f"\n[listen] signal {signum}, cleaning...")
        _atexit()
        # os._exit skips atexit handlers *and* discards buffered stdio, and a
        # listener is always ended by a signal -- so before this called the
        # same cleanup itself, every shutdown leaked listeners/<host>.pid and
        # threw the whole log away. An empty log is worst exactly when it is
        # wanted: after the peer has stopped.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    try:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    except Exception:
        # signal only in main thread; tests run listen in bg thread
        pass



    try:
        while True:
            if watch_pid is not None and not is_pid_alive(watch_pid):
                break
            try:
                conn, peer = server.accept()
            except TimeoutError:
                continue
            except OSError:
                if server.fileno() == -1:
                    break
                raise
            t = threading.Thread(target=handle, args=(conn, peer), daemon=True)
            t.start()
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup(sock_path, session_path, server, key_path)

def send_peer_message(target_sock: str, text: str) -> bool:
    """Send one peer user message over UDS using auth + status-back pattern.
    target_sock: full path to target .sock
    Returns success bool.
    """
    our_sock = None
    mypid = os.getpid()
    sd = _sock_dir()
    env_sock = os.environ.get("AGENT_BUS_LISTEN_SOCK")
    if env_sock and os.path.exists(env_sock):
        our_sock = env_sock
    if not our_sock:
        cand = os.path.join(sd, f"{mypid}.sock")
        if os.path.exists(cand):
            our_sock = cand
    if not our_sock:
        # Our listener runs as a separate process: listeners/<host_pid>.pid is named
        # for the HOST and contains the LISTENER's pid, and the socket is named for
        # the listener. So walk our ancestors to find the host, then read the pid
        # file to get the socket. Building "<our own pid>.sock" never resolves,
        # because the caller is neither the host nor the listener.
        try:
            ldir = os.path.join(get_home(), "listeners")
            if os.path.isdir(ldir):
                for anc in ancestor_pids():
                    pid_file = os.path.join(ldir, f"{anc}.pid")
                    if not os.path.isfile(pid_file):
                        continue
                    try:
                        with open(pid_file, encoding="utf-8") as f:
                            listener_pid = int(f.read().strip())
                    except (OSError, ValueError):
                        continue
                    if not is_pid_alive(listener_pid):
                        continue
                    cand = os.path.join(sd, f"{listener_pid}.sock")
                    if os.path.exists(cand):
                        our_sock = cand
                        break
        except OSError:
            pass
    if not our_sock:
        # The ancestor walk fails whenever the calling process is not a descendant
        # of the host -- a harness bash tool need not preserve that chain. But an
        # AGENT_BUS_HOME belongs to one peer, so a single live listener registered
        # here is unambiguously ours.
        try:
            ldir = os.path.join(get_home(), "listeners")
            live = []
            if os.path.isdir(ldir):
                for fn in os.listdir(ldir):
                    if not fn.endswith(".pid"):
                        continue
                    try:
                        with open(os.path.join(ldir, fn), encoding="utf-8") as f:
                            lp = int(f.read().strip())
                    except (OSError, ValueError):
                        continue
                    cand = os.path.join(sd, f"{lp}.sock")
                    if is_pid_alive(lp) and os.path.exists(cand):
                        live.append(cand)
            if len(live) == 1:
                our_sock = live[0]
        except OSError:
            pass
    if not our_sock:
        print("[send-peer] err: cannot determine our listen socket")
        return False
    token = None
    base = os.path.basename(target_sock)
    tpid_str = base.split(".")[0]
    try:
        tpid = int(tpid_str)
    except Exception:
        tpid = None
    if tpid:
        ssdir = _sessions_dir()
        h = hashlib.sha256(target_sock.encode("utf-8")).hexdigest()
        kpath = os.path.join(ssdir, f"{tpid}.{h}.key")
        if os.path.exists(kpath):
            try:
                with open(kpath, encoding="utf-8") as kf:
                    token = json.load(kf).get("peerToken")
            except Exception:
                pass
        if not token:
            try:
                if os.path.isdir(ssdir):
                    for fn in os.listdir(ssdir):
                        if fn.startswith(f"{tpid}.") and fn.endswith(".key"):
                            try:
                                with open(os.path.join(ssdir, fn), encoding="utf-8") as kf:
                                    token = json.load(kf).get("peerToken")
                                    if token:
                                        break
                            except Exception:
                                pass
            except Exception:
                pass
    if not token:
        print(f"[send-peer] path={target_sock} err: no peerToken")
        return False
    # wrap text as cross-session-message
    from_name = _advertised_name(our_sock)
    inner = (
        f'<cross-session-message from="uds:{our_sock}" '
        f'from-name="{from_name}" from-mode="prompting">\n'
        f'{text}\n</cross-session-message>'
    )
    msg = {
        "msgV": 1,
        "msg_id": str(uuid.uuid4()),
        "type": "user",
        "message": {"role": "user", "content": inner},
        "priority": "next",
        "from": f"uds:{our_sock}",
    }
    auth = json.dumps({"type": "auth", "token": token}) + "\n"
    frame = json.dumps(msg) + "\n"
    s = None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(target_sock)
        s.sendall(auth.encode("utf-8"))
        s.sendall(frame.encode("utf-8"))
        try:
            s.shutdown(socket.SHUT_WR)
            s.settimeout(1.0)
            while s.recv(4096):
                pass
        except Exception:
            pass
        finally:
            if s:
                with contextlib.suppress(Exception):
                    s.close()
        print(f"[send-peer] path={target_sock} ok")
        return True
    except Exception as e:
        print(f"[send-peer] path={target_sock} err: {e}")
        if s:
            with contextlib.suppress(Exception):
                s.close()
        return False
