"""A stub of Codex's app-server, speaking the real framing.

Deliberately mimics the behaviours that shaped the client, all observed against
codex-cli 0.149.0:

- newline-delimited JSON, JSON-RPC *shaped* but with no "jsonrpc" field
- every request rejected until `initialize` completes
- notifications interleaved with responses
- the process stays alive while stdin is open, and exits at EOF

Run directly it behaves like the server; the client tests drive it as a
subprocess.
"""

import json
import os
import sys

THREADS = [
    {
        "id": "01a01cb8-1f72-7e71-97ca-69349d003abc",
        "sessionId": "01a01cb8-1f72-7e71-97ca-69349d003abc",
        "name": "alpha",
        "preview": "a thread",
    },
    {
        "id": "01a01cb8-1f72-7e71-97ca-69349d003abd",
        "sessionId": "01a01cb8-1f72-7e71-97ca-69349d003abd",
        "name": "beta",
        "preview": "another thread",
    },
]

# UUID-shaped, deliberately: a test driving send_to_codex (not the server
# methods directly) needs a target _as_thread_id recognizes on its own,
# bypassing name resolution -- a bare word like "archived-thread" would fail
# to resolve as a name instead of reaching the behaviour under test.
ARCHIVED_THREAD_UUID = "01a01cb8-1f72-7e71-97ca-69349d00afca"

# A test can point this at a file to get a durable record of which RPC
# methods this run received, in order -- used to prove send_to_codex issues
# only thread/queue/add and never turn/start (#292's own regression: an
# earlier version tried turn/start first, and it silently dropped a message).
METHOD_LOG = os.environ.get("CODEX_STUB_METHOD_LOG")


def _log_method(method):
    if METHOD_LOG and method:
        with open(METHOD_LOG, "a") as f:
            f.write(method + "\n")


def emit(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def error(msg_id, code, message):
    emit({"id": msg_id, "error": {"code": code, "message": message}})


def main() -> int:
    initialized = False
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        _log_method(method)

        if method == "initialize":
            initialized = True
            emit({
                "id": msg_id,
                "result": {
                    "userAgent": "agent-bus/stub",
                    "codexHome": "/tmp/stub-codex-home",
                    "platformFamily": "unix",
                    "platformOs": "macos",
                },
            })
            # the real server emits this unprompted shortly after initialize
            emit({
                "method": "remoteControl/status/changed",
                "params": {"status": "disabled"},
                "emittedAtMs": 0,
            })
            continue

        if method == "initialized":
            continue

        if not initialized:
            error(msg_id, -32600, "Not initialized")
            continue

        if method == "thread/list":
            emit({"id": msg_id, "result": {"data": THREADS}})
            continue

        if method == "thread/queue/add":
            params = msg.get("params") or {}
            thread_id = params.get("threadId")
            if thread_id in ("archived-thread", ARCHIVED_THREAD_UUID):
                error(
                    msg_id,
                    -32600,
                    f"session {thread_id} is archived. "
                    f"Run `codex unarchive {thread_id}` to unarchive it first.",
                )
                continue
            emit({
                "id": msg_id,
                "result": {
                    "queuedSubmission": {
                        "id": "queued-submission-id",
                        "input": params.get("input"),
                        "clientUserMessageId": params.get("clientUserMessageId"),
                    }
                },
            })
            continue

        if method == "thread/resume":
            params = msg.get("params") or {}
            thread_id = params.get("threadId")
            emit({
                "id": msg_id,
                "result": {"thread": {"id": thread_id, "status": {"type": "idle"}, "turns": []}},
            })
            continue

        if method == "turn/start":
            params = msg.get("params") or {}
            thread_id = params.get("threadId")
            if thread_id in ("archived-thread", ARCHIVED_THREAD_UUID):
                error(
                    msg_id,
                    -32600,
                    f"session {thread_id} is archived. "
                    f"Run `codex unarchive {thread_id}` to unarchive it first.",
                )
                continue
            emit({
                "id": msg_id,
                "result": {"turn": {"id": "new-turn-id", "status": "inProgress",
                                    "input": params.get("input")}},
            })
            continue

        error(msg_id, -32601, f"method not found: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
