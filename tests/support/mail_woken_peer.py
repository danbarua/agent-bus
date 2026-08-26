"""A headless Claude peer that takes a turn because mail arrived.

The peer arms its own `Monitor` on `agent-bus watch --name <me>` and then
stops. A watch line becomes a monitor event, and the event starts a new turn in
a session whose previous turn had already ended -- so nothing has to tick it.
Measured: arm, `RESULT_EVENT: success`, sixty seconds of nothing, then a turn
with no input written to stdin in between.

That is the mechanism grok's `monitor` tool was designed around, and Claude
turns out to have the same shape, so a peer built this way is the one that can
hold a conversation rather than answer once.

Two things it must do that are easy to leave out:

**Register it before the brief reaches it, which is what `on_spawn` is for.**
The peer's first act is to start `watch --name <me>`, and watch exits 1 with
"cannot resolve inbox" for a name nobody has registered. Registering after the
monitor is up is too late: the watch is already dead, `Monitor started` is
already in the transcript, and the peer -- finding itself broken -- improvises.
One run had a peer register itself under the wrong kind and send its opening
message twice.

**Register as `other`, not `claude`.** This peer reads the file inbox; it
publishes no listener. Registered as `claude`, `send` takes the UDS path and
refuses with "no reachable socket" -- correctly.

**Tell it to `ToolSearch` for `Monitor`.** The tool is deferred in some
configurations, and a peer that cannot find it looks exactly like a mechanism
that does not work.

See `claude_peer.py` for the older ticker-driven peer, which the
one-shot messaging tests still use.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time

ARM_TIMEOUT = 120.0


@contextlib.contextmanager
def mail_woken_peer(name: str, brief: str, *, env: dict[str, str],
                    cwd: str, log_dir: str, model: str,
                    on_spawn=None):
    """Run a peer under `brief`; yield once its monitor is watching.

    Yielding early would lose the first message: `watch` starts from the end of
    the inbox, so anything sent before the monitor is up is never seen.
    """
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "stdout.jsonl")
    out = open(path, "w", encoding="utf-8")
    err = open(os.path.join(log_dir, "stderr.txt"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        ["claude", "-p", "--model", model,
         "--input-format", "stream-json", "--output-format", "stream-json",
         "--verbose", "--dangerously-skip-permissions"],
        stdin=subprocess.PIPE, stdout=out, stderr=err, text=True,
        cwd=cwd, env=env,
    )
    try:
        # Before the brief, never after: see the module docstring.
        if on_spawn is not None:
            on_spawn(proc.pid)

        # The pipe stays open for the life of the block: closing stdin ends the
        # session, and this peer has to outlive its own first turn.
        proc.stdin.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": brief}]},
        }) + "\n")
        proc.stdin.flush()

        deadline = time.time() + ARM_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(
                    f"{name} exited before arming its monitor "
                    f"(rc={proc.returncode}); see {path}"
                )
            try:
                seen = open(path, encoding="utf-8").read()
            except OSError:
                seen = ""
            # `Monitor started` only means the tool accepted the command. A
            # watch that exited immediately leaves it there too, which is how
            # a dead peer once looked armed for seven minutes.
            if "cannot resolve inbox" in seen:
                raise AssertionError(
                    f"{name}'s watch could not resolve an inbox -- it was not "
                    f"registered before the brief. See {path}"
                )
            if "Monitor started" in seen:
                break
            time.sleep(1.0)
        else:
            raise AssertionError(
                f"{name} did not arm a monitor within {ARM_TIMEOUT:.0f}s; "
                f"see {path}"
            )
        yield proc
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
