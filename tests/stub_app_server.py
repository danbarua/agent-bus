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


def emit(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def error(msg_id, code, message):
    emit({"id": msg_id, "error": {"code": code, "message": message}})


def main() -> int:
    initialized = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

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
            if thread_id == "archived-thread":
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

        error(msg_id, -32601, f"method not found: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
