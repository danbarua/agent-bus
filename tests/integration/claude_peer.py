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

Staying alive is still not the same as *acting*, and the two pull against each
other. Measured, one variable at a time:

    idle peer, no tick     SEND_EXIT=0   delivered, never answered
    peer ticking every 12s SEND_EXIT=1   "refused the message"

So a peer must be **idle to receive** and needs **a turn to act**. Ticking hard
enough to guarantee the second destroys the first: a peer mid-turn refuses the
frame outright. The tick is therefore slow -- the peer spends most of its time
idle and available, and gets a turn within TICK_SECONDS of anything arriving.
omp waits up to 300s for the reply, so a 30s tick gives it roughly ten chances.

`crossSessionInbound: accept` is set for the same reason it is easy to miss:
unset means *mode parity*, where a sender asserting no permission class -- our
CLI is one -- is held for approval whenever the receiving session bypasses
prompts, which a `--dangerously-skip-permissions` peer does. Delivery then
depends on who is asking rather than on the test, and a headless peer has no
one to approve it.

**Status: the tick is not yet proven.** Delivery and refusal above are measured;
that a slow tick reliably converts a delivered message into a reply is not.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import subprocess
import threading
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

# Each tick is a turn. Without one, a message that has already been delivered
# just sits there: the peer is alive but has nothing running to surface it in.
TICK = (
    "Tick. If any <cross-session-message> has arrived since your last turn and "
    "you have not already replied to it, reply now with SendMessage to its "
    "from= address, text 'ack from headless claude'. Otherwise say nothing."
)
TICK_SECONDS = 30.0


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
         # Unset crossSessionInbound means mode parity, under which a sender
         # that asserts no class is held while we bypass prompts -- and there
         # is no one here to approve it.
         "--settings", json.dumps({"crossSessionInbound": "accept"}),
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
        stop = threading.Event()

        def _tick() -> None:
            while not stop.wait(TICK_SECONDS):
                try:
                    proc.stdin.write(json.dumps({
                        "type": "user",
                        "message": {"role": "user",
                                    "content": [{"type": "text", "text": TICK}]},
                    }) + "\n")
                    proc.stdin.flush()
                except (OSError, ValueError):
                    return

        ticker = threading.Thread(target=_tick, daemon=True)
        ticker.start()
        try:
            yield name
        finally:
            stop.set()
            ticker.join(timeout=5)
    finally:
        with contextlib.suppress(Exception):
            proc.stdin.close()
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=15)
        with contextlib.suppress(Exception):
            proc.kill()
