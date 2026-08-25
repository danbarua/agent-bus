"""A headless Claude Code session that can be messaged, for the UDS tiers.

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

`crossSessionInbound: accept` is set for the same reason it is easy to miss:
unset means *mode parity*, where a sender asserting no permission class -- our
CLI is one -- is held for approval whenever the receiving session bypasses
prompts, which a `--dangerously-skip-permissions` peer does. Delivery then
depends on who is asking rather than on the test, and a headless peer has no
one to approve it.

**Status: the 30s tick is proven.** Three consecutive rounds of tiers 3 and 4
passed unattended, and the peer's own stream shows the whole path -- READY, the
inbound block, a native SendMessage to the driver's socket, success. This file
previously recorded the opposite, and the correction is worth keeping: the runs
that looked like wake failures were *grading* failures. The driver had completed
the round trip and said so in its own words ("The inbox contains a message.")
where the test grepped for a literal marker. Nothing about waking was wrong; the
evidence channel was. Tiers 3 and 4 now read markers off disk for that reason.

The peer's streams are never piped to an unread pipe -- see the redirect below.
A 40s run emits ~39KB of stream-json against a ~64KB pipe buffer, so an
undrained pipe is a deadlock roughly one longer run away, and a peer frozen
mid-write is indistinguishable from one that never woke.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import subprocess
import tempfile
import threading
import time

SESSIONS = os.path.expanduser("~/.claude/sessions")

# The exact words the peer must answer with. Tier 4 greps the driver's inbox
# for this string, so it lives here rather than in the test: the brief and the
# assertion have to agree, and two copies of a magic string do not stay equal.
ACK_TEXT = "ack from headless claude"

# What the peer is for. It must reply, or tier 4 has nothing to wait for.
BRIEF = (
    "You are a peer in an integration test for agent-bus. Other agents will "
    "message you; each arrives in your conversation as a <cross-session-message> "
    "block. For every one, immediately reply with your native SendMessage tool, "
    "addressed to that message's from= address, with the text "
    f"'{ACK_TEXT}'. Do not do anything else. Say READY now."
)

# Each tick is a turn. Without one, a message that has already been delivered
# just sits there: the peer is alive but has nothing running to surface it in.
TICK = (
    "Tick. If any <cross-session-message> has arrived since your last turn and "
    "you have not already replied to it, reply now with SendMessage to its "
    f"from= address, text '{ACK_TEXT}'. Otherwise say nothing."
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
def headless_claude_peer(
    timeout: float = 60.0,
    brief: str | None = None,
    tick: str | None = None,
    log_dir: str | None = None,
):
    """Run a headless Claude peer; yield the name other agents address it by.

    Its name is auto-derived by Claude Code, so it is discovered rather than
    chosen: watch for the session file that appears, and read the name out of
    it.

    `brief` and `tick` default to the reply-with-ACK pair tiers 3 and 4 need.
    A tier that wants the peer to *do* something else supplies its own, keeping
    the hard-won parts -- stdin held open so the session does not end, the tick
    cadence, `crossSessionInbound: accept`, and streams written to files rather
    than an unread pipe -- rather than reimplementing them and rediscovering why
    each is there.

    `log_dir` puts the streams somewhere the caller already knows. The default
    is a fresh temp dir, printed for a human; a test that needs to *read* the
    transcript cannot go hunting for it in stdout.
    """
    brief = brief or BRIEF
    tick = tick or TICK
    before = _session_files()
    # Never hand the peer an unread pipe. With --verbose --output-format
    # stream-json it emits an event per turn, and a pipe nobody drains fills at
    # ~64KB and blocks the writer mid-turn -- a peer frozen that way is
    # indistinguishable from one that never woke. Files also survive the run,
    # which is the only way to tell those two apart afterwards.
    logdir = log_dir or tempfile.mkdtemp(prefix="claude-peer-")
    os.makedirs(logdir, exist_ok=True)
    out = open(os.path.join(logdir, "stdout.jsonl"), "w", encoding="utf-8")
    err = open(os.path.join(logdir, "stderr.txt"), "w", encoding="utf-8")
    print(f"[peer] stream log: {logdir}", flush=True)
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
        stdin=subprocess.PIPE, stdout=out, stderr=err,
        text=True,
    )
    try:
        # The pipe stays open for the life of the block. Closing it is what
        # ends the session, so nothing here may close stdin early.
        proc.stdin.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": brief}]},
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
                                    "content": [{"type": "text", "text": tick}]},
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
        for handle in (out, err):
            with contextlib.suppress(Exception):
                handle.close()
