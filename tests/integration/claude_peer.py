"""A headless Claude Code session that can be messaged, for the UDS tiers.

Tiers 3 and 4 need a live Claude peer. Using the developer's own session works
-- and is how those tiers were first proven -- but it needs a human sitting
there to answer, so it cannot run unattended.

A `claude -p` worker binds the same inbox socket as an interactive session, so
it can receive cross-session messages. Verified by watching
`~/.claude/sessions/` and `/tmp/cc-socks/` while one was alive: both counts go
up for the life of the process and back down when it exits.

The trap is keeping it alive. `-p` ends the turn when the model stops emitting,
and no prompt fixes that: a worker told to "count slowly to 300, do not stop
early" exited anyway, its transcript ending "Counted 1-25. Timer running; will
continue on each tick." It believed it was still running. The turn had ended.

What does work is holding stdin open. With `--input-format stream-json` the
session waits for more input instead of finishing, so the peer lives exactly as
long as we keep the pipe open -- which is the whole point of a fixture.
`--output-format stream-json` additionally requires `--verbose`.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import subprocess
import time

SESSIONS = os.path.expanduser("~/.claude/sessions")

# What the peer is for. It must reply, or tier 4 has nothing to wait for.
BRIEF = (
    "You are a peer in an integration test for agent-bus. Other agents will "
    "message you; each arrives in your conversation as a <cross-session-message> "
    "block. For every one, immediately reply with your native SendMessage tool, "
    "addressed to that message's from= address, with the text "
    "'ack from headless claude'. Do not do anything else. Say READY now."
)


def _session_files() -> set[str]:
    try:
        return set(glob.glob(os.path.join(SESSIONS, "*.json")))
    except OSError:
        return set()


def _name_of(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("name")
    except (OSError, json.JSONDecodeError):
        return None


@contextlib.contextmanager
def headless_claude_peer(timeout: float = 60.0):
    """Run a headless Claude peer; yield the name other agents address it by.

    Its name is auto-derived by Claude Code, so it is discovered rather than
    chosen: watch for the session file that appears, and read the name out of
    it.
    """
    before = _session_files()
    proc = subprocess.Popen(
        ["claude", "-p",
         "--input-format", "stream-json",
         "--output-format", "stream-json",
         "--verbose",
         "--dangerously-skip-permissions"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # The pipe stays open for the life of the block. Closing it is what
        # ends the session, so nothing here may close stdin early.
        proc.stdin.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": BRIEF}]},
        }) + "\n")
        proc.stdin.flush()

        deadline = time.time() + timeout
        name = None
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(
                    f"headless claude exited early (rc={proc.returncode})"
                )
            for path in _session_files() - before:
                name = _name_of(path)
                if name:
                    break
            if name:
                break
            time.sleep(1.0)
        if not name:
            raise AssertionError(
                "headless claude published no session file within "
                f"{timeout}s -- it cannot be messaged"
            )
        yield name
    finally:
        with contextlib.suppress(Exception):
            proc.stdin.close()
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=15)
        with contextlib.suppress(Exception):
            proc.kill()
