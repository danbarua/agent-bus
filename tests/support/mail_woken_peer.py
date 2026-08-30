"""A headless peer that takes a turn because mail arrived.

The peer arms its own monitor on `agent-bus watch --name <me>` and then stops.
A watch line becomes a monitor event, and the event starts a new turn in a
session whose previous turn had already ended -- so nothing has to tick it.

Measured for both harnesses before this existed. Claude: arm, turn ends, sixty
seconds of nothing, then a turn, with no input written to stdin in between.
Grok: still alive at 45s with its monitor armed, then "Received the agent-bus
wake" in its own words. Two harnesses, one mechanism, and it is the mechanism
grok's `monitor` tool was designed around.

How they are started differs and is not incidental:

**claude** needs stdin held open (`--input-format stream-json`). `-p` ends the
turn when the model stops emitting, and closing stdin ends the session, so the
pipe stays open for the life of the block.

**grok** takes its prompt in argv and needs no open stdin at all -- its
persistent monitor is what keeps the session up.

**omp** has no push. It parks: `hub start` puts `agent-bus watch` under
supervision, then a bounded `hub logs --follow` loop -- 300s per call, not one
indefinite block -- reads whatever arrived and acts on it between calls. This
is a CI compromise for determinism, not a recommendation: it is shaped this
way so the test has one deterministic point per exchange to assert on, and a
real session is not obligated to loop this tightly just because this test
does. Measured against real interactive use, 2026-08-28: the shape is real and
it works -- the same bounded-loop pattern, independently, is what a live omp
session actually did to hold a bus conversation, not something invented for
this test. See `WAKE` below.

Two things every caller must get right:

**Register before the brief reaches it, which is what `on_spawn` is for.** The
peer's first act is to start `watch --name <me>`, and watch exits 1 with
"cannot resolve inbox" for a name nobody has registered. Registering after the
monitor is up is too late: the watch is already dead and the peer -- finding
itself broken -- improvises. One run had a peer register itself under the wrong
kind and send its opening message twice.

**Register as `other`, not `claude`.** This peer reads the file inbox; it
publishes no listener. Registered as `claude`, `send` takes the UDS path and
refuses with "no reachable socket" -- correctly.

See `claude_peer.py` for the older ticker-driven Claude peer, which the
one-shot messaging tests still use.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time

import pytest
from models import CLAUDE_MODEL, GROK_MODEL, OMP_MODEL

ARM_TIMEOUT = 150.0

MODELS = {"claude": CLAUDE_MODEL, "grok": GROK_MODEL, "omp": OMP_MODEL}

# How this harness comes to notice mail, which decides the shape of its brief:
# a pushed peer ends its turn and is re-invoked, a parked one blocks in a
# bounded tool call and loops. Measured per harness -- see
# docs/harness-compatibility.md.
WAKE = {"claude": "push", "grok": "push", "omp": "park"}


def _open_logs(log_dir: str):
    os.makedirs(log_dir, exist_ok=True)
    return (open(os.path.join(log_dir, "stdout.log"), "w", encoding="utf-8"),
            open(os.path.join(log_dir, "stderr.log"), "w", encoding="utf-8"))


def _spawn_claude(brief, *, model, cwd, env, out, err):
    proc = subprocess.Popen(
        ["claude", "-p", "--model", model,
         "--input-format", "stream-json", "--output-format", "stream-json",
         "--verbose", "--dangerously-skip-permissions"],
        stdin=subprocess.PIPE, stdout=out, stderr=err, text=True, cwd=cwd, env=env,
    )
    return proc, lambda: _write_stdin(proc, brief)


def _write_stdin(proc, brief):
    proc.stdin.write(json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": brief}]},
    }) + "\n")
    proc.stdin.flush()


def _spawn_grok(brief, *, model, cwd, env, out, err):
    """grok takes its prompt in argv, so there is nothing to deliver after.

    That means registration races grok's own start rather than being ordered
    before it, as it is for claude. The race is wide -- registering takes about
    a second, and grok has to boot and reach a model before it can call its
    monitor tool -- and losing it is loud: the watch cannot resolve an inbox,
    no watch is running, and the arm check fails naming the log. Worth knowing
    if this ever flakes; not worth a holder process until it does.
    """
    proc = subprocess.Popen(
        ["grok", "-p", brief, "--always-approve", "-m", model],
        stdin=subprocess.DEVNULL, stdout=out, stderr=err, text=True, cwd=cwd, env=env,
    )
    return proc, lambda: None


def _spawn_omp(brief, *, model, cwd, env, out, err):
    """Parks rather than ending its turn, so `--max-time` bounds the whole
    conversation rather than one reply. `--mode json` because text mode emits
    nothing until the run ends -- kill a text-mode omp and its transcript is
    gone exactly when the failure needs reading."""
    proc = subprocess.Popen(
        ["omp", "-p", "--no-session", "--no-title", "--auto-approve",
         "--model", model, "--cwd", cwd, "--max-time", "20m",
         "--mode", "json", "--", brief],
        stdin=subprocess.DEVNULL, stdout=out, stderr=err, text=True, cwd=cwd, env=env,
    )
    return proc, lambda: None


SPAWN = {"claude": _spawn_claude, "grok": _spawn_grok, "omp": _spawn_omp}


def watch_is_running(name: str, *, not_pid: int) -> bool:
    """Is there a live `agent-bus watch` for this name, other than `not_pid`?

    The real question, and harness-agnostic. Reading the transcript for the
    monitor tool's own acknowledgement is not the same thing: it says the tool
    accepted the command, and a watch that died on the next line leaves that
    string behind exactly as a healthy one does.

    `not_pid` is the harness itself, and skipping it is not defensive tidying.
    grok and omp take their prompt in **argv**, and the prompt contains the
    very command we are looking for -- so a bare `pgrep -f` matches the
    harness the moment it starts, long before it has run anything. That made
    the arm check pass instantly, let the first message be sent before the
    watch existed, and `watch` starts from the end of the inbox: the message
    was never emitted. The peer then followed an empty log until the test gave
    up. grok only escaped it by arming fast enough.
    """
    r = subprocess.run(["pgrep", "-f", f"watch --name {name}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False
    pids = {int(p) for p in r.stdout.split() if p.strip().isdigit()}
    return bool(pids - {not_pid})


@contextlib.contextmanager
def mail_woken_peer(name: str, brief: str, *, harness: str, env: dict[str, str],
                    cwd: str, log_dir: str, on_spawn=None):
    """Run a peer under `brief`; yield once its watch is actually running.

    Yielding earlier would lose the first message: `watch` starts from the end
    of the inbox, so anything sent before it is up is never seen.
    """
    if harness not in SPAWN:
        raise AssertionError(f"no launcher for {harness!r}; have {sorted(SPAWN)}")
    if not shutil.which(harness):
        pytest.skip(f"{harness} is not on PATH")

    out, err = _open_logs(log_dir)
    proc, deliver = SPAWN[harness](
        brief, model=MODELS[harness], cwd=cwd, env=env, out=out, err=err)
    try:
        if on_spawn is not None:
            on_spawn(proc.pid)
        deliver()

        deadline = time.time() + ARM_TIMEOUT
        while time.time() < deadline:
            if watch_is_running(name, not_pid=proc.pid):
                break
            if proc.poll() is not None:
                raise AssertionError(
                    f"{name} ({harness}) exited before its watch was running "
                    f"(rc={proc.returncode}); see {log_dir}"
                )
            time.sleep(1.0)
        else:
            raise AssertionError(
                f"{name} ({harness}) had no running watch after "
                f"{ARM_TIMEOUT:.0f}s; see {log_dir}"
            )
        yield proc
    finally:
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.close()
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=15)
        with contextlib.suppress(Exception):
            proc.kill()
        for handle in (out, err):
            with contextlib.suppress(Exception):
                handle.close()
