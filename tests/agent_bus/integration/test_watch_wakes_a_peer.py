"""`agent-bus watch` emits a line when mail arrives, while it is running.

This is the wake source for every harness that is not Claude. grok, omp and pi
have no way to notice an inbox on their own; grok's `monitor` runs a command
and turns each stdout line into a conversation event, so a peer starts

    monitor(command="agent-bus watch --name me", persistent=true)

once and inbound traffic arrives as events. If watch stops emitting, or stops
flushing, those harnesses go deaf and nothing anywhere reports it.

tests/agent_bus/test_watch.py covers the formatting and offset logic in
process. What it cannot cover is the part that actually has to work: a
long-running `agent-bus watch` process, mail arriving from a *different*
process, and the line appearing promptly enough for a monitor to see it.
Buffered output would pass every in-process test and deliver nothing until
several kilobytes had accumulated.

No model and no harness, so this is not spendy -- it drives two processes and
a file.
"""

import subprocess
import time

from agent_names import mint_agent_name
from busctl import REPO, bus, bus_env, register

# Generous: a first `uv run` may resolve the environment before agent-bus
# starts. The assertion is that the line arrives, not that it races.
WATCH_START = 30.0
DELIVERY = 30.0


def _wait_for(predicate, timeout, what):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(0.5)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")


def test_a_message_reaches_a_running_watch(bus_home, tmp_path):
    watcher, sender = mint_agent_name(), mint_agent_name()
    out = tmp_path / "watch.out"
    holder = subprocess.Popen(["sleep", "120"])
    proc = None
    try:
        # Only the watcher is registered. A second name against the same pid
        # *renames* that entry rather than adding one, so registering the
        # sender here would take the watcher off the bus -- and `--from-name`
        # is a label on the message, not a claim that the sender exists.
        register(bus_home, watcher, "other", pid=holder.pid)

        with open(out, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(
                ["uv", "run", "--project", REPO, "agent-bus", "watch",
                 "--name", watcher],
                env=bus_env(bus_home), cwd=REPO,
                stdout=f, stderr=subprocess.STDOUT, text=True,
            )

        # It starts from the end of the inbox on purpose -- replaying a backlog
        # is what gets a monitor rate-limited to death -- so it has to be
        # watching before the message is sent, or there is nothing to notice.
        _wait_for(lambda: proc.poll() is not None or out.exists(),
                  WATCH_START, "the watch process to start")
        assert proc.poll() is None, (
            f"watch exited with {proc.returncode} instead of following:\n"
            f"{out.read_text()[-1500:]}"
        )
        time.sleep(3)

        r = bus(bus_home, "send", watcher, "-m", "the body", "--summary",
                "wake up", "--from-name", sender)
        assert r.returncode == 0, r.stderr

        line = _wait_for(
            lambda: next((ln for ln in out.read_text().splitlines()
                          if "[agent-bus]" in ln and "summary=" in ln), None),
            DELIVERY, f"a watch line for the message sent to {watcher}",
        )
        assert f"from={sender}" in line, line
        assert "wake up" in line, line
        # The body is what get_inbox is for; a line carrying it would blow the
        # monitor's per-line limit and tell the peer nothing new.
        assert "the body" not in line, line
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        holder.kill()
        holder.wait()
