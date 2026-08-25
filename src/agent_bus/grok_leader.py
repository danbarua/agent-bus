"""A client for Grok's leader socket, for live session status.

Grok's leader process hosts every session in one process and exposes a roster
two ways: request/response `_x.ai/sessions/list`, and a broadcast
`_x.ai/sessions/changed` pushed to every connected client on each upsert or
removal. That roster carries an `activity` field, which is the only real
liveness signal grok offers -- our discovery adapter reads
`active_sessions.json` and can say a session exists, but never what it is doing,
so every grok peer listed as `unknown`.

A leaf: imports nothing from this package except paths, so both discovery and a
CLI command can use it without a cycle.

Everything here was verified against a live leader (grok 1.0.5) rather than
inferred, because four details are not guessable:

1. **Framing is 4-byte big-endian length + JSON**, not newline-delimited
   (`leader/protocol.rs:22-57`).
2. **ACP `initialize` comes first.** Without it every method answers
   "Method not found" -- which looks exactly like an unsupported build.
3. **Ext methods carry a leading underscore on the wire.** The source calls it
   `x.ai/sessions/list`; the wire wants `_x.ai/sessions/list`. Sending the
   documented name returns -32601. This was found by watching unsolicited
   notifications arrive as `_x.ai/mcp/servers_updated`.
4. **The result is nested twice** -- `result.result.sessions`. Grok's own pager
   carries a comment about this: the inner struct's `sessions` field has a serde
   default, so a single unwrap parses *successfully* into an empty roster and
   silently reports no sessions.

The whole handshake measured 2.7ms for ten sessions, which is why discovery can
afford it inline.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import struct
from collections.abc import Iterator
from typing import Any

from .paths import grok_dir

# Grok's own cap (protocol.rs:8). A frame larger than this is a broken peer.
MAX_FRAME = 64 * 1024 * 1024

LIST_METHOD = "_x.ai/sessions/list"
CHANGED_METHOD = "_x.ai/sessions/changed"

# RosterActivity (agent/roster.rs:26-41) mapped onto ours. `dormant`,
# `completed` and `dead` are not "some status" -- they mean the session is no
# longer running, which is a liveness answer, not a busy/idle one.
ACTIVITY_STATUS: dict[str, str] = {
    "working": "busy",
    "idle": "idle",
    "needs_input": "waiting",
}
GONE_ACTIVITIES: frozenset[str] = frozenset({"dormant", "completed", "dead"})


class LeaderError(RuntimeError):
    """The leader is absent, unreachable, or spoke unexpectedly."""


def leader_socket() -> str:
    """Where the leader listens. `--leader-socket` has an env twin."""
    return os.environ.get("GROK_LEADER_SOCKET") or os.path.join(
        grok_dir(), "leader.sock"
    )


def leader_available() -> bool:
    """Cheap enough to call on every listing: a stat, not a connect."""
    path = leader_socket()
    try:
        return os.path.exists(path)
    except OSError:
        return False


def activity_to_status(activity: str | None) -> str | None:
    """Our status for a grok activity, or None if the session is not running."""
    if not activity:
        return None
    return ACTIVITY_STATUS.get(str(activity).lower())


class LeaderClient:
    """One connection: register, initialize, then ask.

    Use as a context manager. Never blocks longer than `timeout` on any read,
    because this runs inside a listing and a hung leader must not hang the bus.
    """

    def __init__(self, path: str | None = None, timeout: float = 5.0) -> None:
        self.path = path or leader_socket()
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._next_id = 0

    # ------------------------------------------------------------- framing

    def _send(self, obj: dict[str, Any]) -> None:
        assert self._sock is not None  # noqa: S101  # type narrowing, not validation
        body = json.dumps(obj).encode("utf-8")
        self._sock.sendall(struct.pack(">I", len(body)) + body)

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None  # noqa: S101  # type narrowing, not validation
        chunks: list[bytes] = []
        got = 0
        while got < n:
            chunk = self._sock.recv(min(65536, n - got))
            if not chunk:
                raise LeaderError("leader closed the connection")
            chunks.append(chunk)
            got += len(chunk)
        return b"".join(chunks)

    def _recv(self) -> dict[str, Any]:
        (length,) = struct.unpack(">I", self._recv_exact(4))
        if length > MAX_FRAME:
            raise LeaderError(f"leader sent an oversized frame: {length}")
        return json.loads(self._recv_exact(length))

    # ----------------------------------------------------------- lifecycle

    def __enter__(self) -> LeaderClient:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect(self.path)
        except OSError as e:
            raise LeaderError(f"no grok leader at {self.path}: {e}") from e
        self._sock = s
        try:
            self._register()
            self._initialize()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._send({"type": "disconnect"})
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None

    def _register(self) -> None:
        self._send({
            "type": "register",
            "client_type": "agent-bus",
            "mode": "stdio",
            # We read a roster and nothing else. Claiming terminal or fs
            # capabilities would invite work we cannot do.
            "capabilities": {"terminal": False, "fs_read": False, "fs_write": False},
        })
        while True:
            msg = self._recv()
            kind = msg.get("type")
            if kind == "registered":
                # `ready: false` means the leader is still starting; it holds
                # the connection and sends leader_ready when it can take ACP
                # traffic. Sending before that is documented as forbidden.
                if msg.get("ready", True):
                    return
            elif kind == "leader_ready":
                return
            elif kind in ("error", "shutting_down", "shutdown"):
                raise LeaderError(f"leader refused the connection: {msg}")

    def _acp(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        rid = self._next_id
        self._send({"type": "acp", "payload": json.dumps({
            "jsonrpc": "2.0", "id": rid, "method": method, "params": params or {},
        })})
        while True:
            msg = self._recv()
            if msg.get("type") != "acp":
                continue
            payload = json.loads(msg["payload"])
            # Notifications have no id and interleave freely with responses.
            if payload.get("id") != rid:
                continue
            if "error" in payload:
                raise LeaderError(f"{method}: {payload['error']}")
            return payload.get("result")

    def _initialize(self) -> None:
        self._acp("initialize", {"protocolVersion": 1, "clientCapabilities": {}})

    # --------------------------------------------------------------- roster

    def list_sessions(self) -> list[dict[str, Any]]:
        """Every session the leader hosts, with its activity.

        Unwraps twice on purpose -- see the module docstring. A single unwrap
        parses cleanly into an empty roster and reports nothing at all.
        """
        result = self._acp(LIST_METHOD)
        if not isinstance(result, dict):
            return []
        inner = result.get("result")
        payload = inner if isinstance(inner, dict) else result
        sessions = payload.get("sessions")
        return sessions if isinstance(sessions, list) else []

    def watch(self) -> Iterator[dict[str, Any]]:
        """Yield each `_x.ai/sessions/changed` broadcast as it arrives.

        Blocks. Yields `{"upserted": [...], "removed": [sessionId, ...]}`. The
        leader pushes these to *every* connected client, not just the owning
        session's, so one watcher sees the whole machine.
        """
        assert self._sock is not None  # noqa: S101  # type narrowing, not validation
        self._sock.settimeout(None)
        while True:
            msg = self._recv()
            if msg.get("type") != "acp":
                continue
            try:
                payload = json.loads(msg["payload"])
            except json.JSONDecodeError:
                continue
            if payload.get("method") != CHANGED_METHOD:
                continue
            params = payload.get("params") or {}
            yield {
                "upserted": params.get("upserted") or [],
                "removed": params.get("removed") or [],
            }


def session_status() -> dict[str, str]:
    """sessionId -> our status, for sessions the leader says are running.

    Best effort and never raises: no leader, an unreachable one, or a protocol
    surprise all mean "we learned nothing", which is what discovery assumed
    before this existed.
    """
    if not leader_available():
        return {}
    try:
        with LeaderClient() as client:
            sessions = client.list_sessions()
    except (LeaderError, OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for s in sessions:
        sid = s.get("sessionId") or s.get("session_id")
        status = activity_to_status(s.get("activity"))
        if sid and status:
            out[str(sid)] = status
    return out
