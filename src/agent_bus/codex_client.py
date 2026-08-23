"""Outbound client for Codex's app-server.

Codex is the one harness we can message with nothing installed on its side:
`thread/queue/add` persists to SQLite before any attempt to wake the target, so
a busy, cold or restarting thread all accept a message, and an idle thread is
woken automatically. See docs/codex-messaging-reference.md.

Transport notes, all verified against codex-cli 0.149.0 on a live app-server:

- The protocol is JSON-RPC *shaped* but not JSON-RPC: there is no "jsonrpc"
  field, and sending one is not expected.
- `codex app-server` speaks newline-delimited JSON on stdio. Its `unix://` and
  `ws://` transports are WebSocket, so stdio is the only framing that needs no
  WebSocket implementation -- which is why this spawns a subprocess rather than
  dialling the control socket.
- `initialize` must complete before any other request; everything else is
  rejected with "Not initialized" until it does.
- `thread/queue/add` is experimental, so `capabilities.experimentalApi` must be
  true at initialize or the method is unavailable.
- The server keeps running while stdin is open. Writing a request and closing
  stdin immediately races shutdown against the response, and the response is
  lost -- so this holds the pipe open and reads until the id comes back.

This deliberately spawns its own app-server rather than proxying to a running
daemon. The queue is a SQLite table keyed on thread_id, and a daemon that has
the thread loaded picks up external queue writes on its own poller, so a
separate short-lived server is enough to deliver.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any

DEFAULT_COMMAND = ("codex", "app-server")
CLIENT_NAME = "agent-bus"
CLIENT_VERSION = "0.1.0"

# initialize is fast; a thread/list against a large history is not.
DEFAULT_TIMEOUT = 60.0


class CodexError(RuntimeError):
    """An error returned by the app-server, or a transport failure."""


def codex_available(command: tuple[str, ...] = DEFAULT_COMMAND) -> bool:
    return shutil.which(command[0]) is not None


class CodexAppServer:
    """A short-lived app-server subprocess, spoken to over NDJSON.

    Use as a context manager; the subprocess is terminated on exit.
    """

    def __init__(
        self,
        command: tuple[str, ...] | list[str] = DEFAULT_COMMAND,
        *,
        codex_home: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._command = list(command)
        self._timeout = timeout
        self._env = os.environ.copy()
        if codex_home:
            self._env["CODEX_HOME"] = codex_home
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str] = queue.Queue()
        self._next_id = 0
        # Notifications arrive interleaved with responses; keep them rather than
        # discarding, since thread/queue/changed is the only delivery signal.
        self.notifications: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- lifecycle

    def __enter__(self) -> CodexAppServer:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self) -> None:
        if self._proc is not None:
            return
        try:
            self._proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self._env,
            )
        except FileNotFoundError as e:
            raise CodexError(f"cannot run {self._command[0]!r}: {e}") from e
        threading.Thread(target=self._pump, daemon=True).start()
        self._initialize()

    def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self._lines.put(line)

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass

    # ------------------------------------------------------------------ protocol

    def _send(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise CodexError("app-server is not running")
        try:
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise CodexError(f"app-server closed the pipe: {e}") from e

    def _await(self, msg_id: int) -> dict[str, Any]:
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            try:
                line = self._lines.get(timeout=0.5)
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    raise CodexError(
                        f"app-server exited with code {self._proc.returncode}"
                    )
                continue
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == msg_id:
                if "error" in msg:
                    err = msg["error"]
                    raise CodexError(
                        f"{err.get('message', 'unknown error')} "
                        f"(code {err.get('code')})"
                    )
                return msg.get("result", {})
            if "method" in msg and "id" not in msg:
                self.notifications.append(msg)
        raise CodexError(f"timed out after {self._timeout}s awaiting response {msg_id}")

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        self._send({"id": msg_id, "method": method, "params": params or {}})
        return self._await(msg_id)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _initialize(self) -> dict[str, Any]:
        # experimentalApi gates thread/queue/add, which is marked experimental.
        result = self.request(
            "initialize",
            {
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.notify("initialized")
        return result

    # --------------------------------------------------------------- operations

    def list_threads(self) -> list[dict[str, Any]]:
        result = self.request("thread/list", {})
        data = result.get("data")
        return data if isinstance(data, list) else []

    def queue_message(self, thread_id: str, text: str) -> dict[str, Any]:
        """Queue one text message for a thread. Returns the QueuedSubmission.

        Persisted before any wake attempt, so this succeeds whether the thread
        is busy, idle, or not loaded anywhere. It fails if the thread is
        archived, or if the queue is at its 100-item cap.
        """
        if not text:
            raise CodexError("message text must not be empty")
        result = self.request(
            "thread/queue/add",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text}],
                "clientUserMessageId": str(uuid.uuid4()),
            },
        )
        submission = result.get("queuedSubmission")
        if not isinstance(submission, dict):
            raise CodexError(f"unexpected thread/queue/add result: {result!r}")
        return submission


# ------------------------------------------------------------------- resolution


def resolve_thread(threads: list[dict[str, Any]], target: str) -> str | None:
    """Resolve a target to a thread id, by id first and then by exact name.

    Codex itself resolves duplicates by taking the most recently updated match
    and reports no ambiguity error (see docs/codex-messaging-reference.md §6).
    We refuse instead: silently delivering to whichever session was touched last
    is the kind of misrouting that is very hard to notice afterwards.
    """
    for t in threads:
        if t.get("id") == target or t.get("sessionId") == target:
            return str(t["id"])

    matches = [t for t in threads if t.get("name") == target]
    if not matches:
        return None
    if len(matches) > 1:
        ids = ", ".join(str(t.get("id")) for t in matches[:5])
        raise CodexError(
            f"{len(matches)} threads are named {target!r}; address one by id ({ids})"
        )
    return str(matches[0]["id"])


def send_to_codex(
    target: str,
    text: str,
    *,
    command: tuple[str, ...] | list[str] = DEFAULT_COMMAND,
    codex_home: str | None = None,
) -> dict[str, Any]:
    """Resolve a target and queue a message. Returns the QueuedSubmission."""
    with CodexAppServer(command, codex_home=codex_home) as server:
        thread_id = target
        if not _looks_like_uuid(target):
            resolved = resolve_thread(server.list_threads(), target)
            if resolved is None:
                raise CodexError(f"no codex thread found matching {target!r}")
            thread_id = resolved
        return server.queue_message(thread_id, text)


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
