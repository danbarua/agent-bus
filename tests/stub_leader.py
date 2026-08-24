"""A stand-in for Grok's leader socket, speaking its real wire protocol.

Real framing (4-byte big-endian length + JSON), real register/initialize
handshake, real underscore-prefixed ext methods, real double-nested list
result. Everything the client can get wrong is reproduced here, so the tests
bite without a running grok.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading


def _send(conn, obj):
    body = json.dumps(obj).encode("utf-8")
    conn.sendall(struct.pack(">I", len(body)) + body)


def _recv(conn):
    head = b""
    while len(head) < 4:
        c = conn.recv(4 - len(head))
        if not c:
            return None
        head += c
    (n,) = struct.unpack(">I", head)
    buf = b""
    while len(buf) < n:
        c = conn.recv(min(65536, n - len(buf)))
        if not c:
            return None
        buf += c
    return json.loads(buf)


def _acp(conn, payload):
    _send(conn, {"type": "acp", "payload": json.dumps(payload)})


class StubLeader:
    """Serves one connection at a time. `sessions` and `deltas` are the script."""

    def __init__(self, path, sessions=None, deltas=None, ready_first=True,
                 list_envelope="double"):
        self.path = str(path)
        self.sessions = sessions or []
        self.deltas = deltas or []
        self.ready_first = ready_first
        self.list_envelope = list_envelope
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(4)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _serve(self):
        self._srv.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn):
        try:
            while not self._stop.is_set():
                msg = _recv(conn)
                if msg is None:
                    return
                kind = msg.get("type")
                if kind == "register":
                    _send(conn, {"type": "registered", "client_id": 1,
                                 "ready": self.ready_first,
                                 "leader_binary_version": "stub"})
                    if not self.ready_first:
                        # A real leader holds the connection until startup
                        # finishes, and ACP sent before leader_ready is
                        # documented as forbidden. Enforce that, or a client
                        # which ignores `ready` passes by luck.
                        self._ready = False
                        threading.Timer(
                            0.25, self._become_ready, args=(conn,)
                        ).start()
                elif kind == "disconnect":
                    return
                elif kind == "acp":
                    if not getattr(self, "_ready", True):
                        _send(conn, {"type": "error",
                                     "message": "acp before leader_ready"})
                        return
                    self._handle_acp(conn, json.loads(msg["payload"]))
        except (OSError, ValueError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _become_ready(self, conn):
        self._ready = True
        try:
            _send(conn, {"type": "leader_ready"})
        except OSError:
            pass

    def _handle_acp(self, conn, req):
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            # A real leader interleaves unsolicited notifications with
            # responses; the client must skip them rather than mistake one for
            # its answer.
            _acp(conn, {"jsonrpc": "2.0", "method": "_x.ai/announcements/update",
                        "params": {"announcements": []}})
            _acp(conn, {"jsonrpc": "2.0", "id": rid,
                        "result": {"protocolVersion": 1, "agentCapabilities": {}}})
        elif method == "_x.ai/sessions/list":
            body = {"sessions": self.sessions}
            result = {"result": body} if self.list_envelope == "double" else body
            _acp(conn, {"jsonrpc": "2.0", "id": rid, "result": result})
            for delta in self.deltas:
                _acp(conn, {"jsonrpc": "2.0", "method": "_x.ai/sessions/changed",
                            "params": delta})
        else:
            # Exactly what a real leader answers for the un-prefixed name.
            _acp(conn, {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32601, "message": "Method not found"}})


def entry(session_id, activity="idle", **kw):
    e = {"sessionId": session_id, "cwd": "/repo", "activity": activity,
         "isWorktree": False, "resident": True, "lastChangeUnixMs": 0,
         "origin": {"kind": "local"}}
    e.update(kw)
    return e
