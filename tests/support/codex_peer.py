"""A Codex peer for the two-way conversation, driven over the app-server
protocol directly rather than spawned as a subprocess with a shell tool.

Codex's wake mechanism (#292) is neither push nor park -- nothing on its side
watches for mail at all. `agent-bus send <this peer's thread id>` delivers by
writing straight into `thread/queue/add`, and an app-server holding that
thread open picks it up on its own, idle or busy, confirmed live against a
real app-server (see `docs/transport-seam.md`'s third and fourth #292
probes). So this peer holds one `CodexAppServer` open for the whole exchange,
delivers its own opening turn directly with `start_turn` -- the one shape
that method is sound for, see `src/agent_bus/adapters/transport/codex.py`'s
module docstring -- and then does nothing else: every message after that
arrives as an ordinary queued turn, and codex replies by shelling out to
`agent-bus send` from inside its own turn, the same as any other harness with
shell access.

Two things this shares with `mail_woken_peer`, for the same reasons:

**The thread must be ready before the counterpart can address it.** A thread
with no completed turn has no rollout on disk yet -- a `thread/queue/add`
against one fails hard ("no rollout found for thread id"), which is a
counterpart harness's FAILED, not a retry. So this does not yield until the
opening turn has actually finished and the thread reports idle again.

**Registration doesn't apply here, but liveness does.** A codex thread is
never a roster entry (see `docs/transport-seam.md`), so there is no
`on_spawn`/register step. But the app-server subprocess can still die
underneath the conversation, so the yielded handle exposes `poll()` and
`returncode` with the same contract as `subprocess.Popen`, for the same
liveness check `test_they_alternate_until_one_says_done` already runs on
every other peer.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from dataclasses import dataclass

import pytest

from agent_bus.adapters.transport.codex import CodexAppServer, CodexError

# How long the opening turn gets before yielding gives up -- generous, since
# it is a real model turn, not a handshake. Mirrors mail_woken_peer's
# ARM_TIMEOUT in spirit: this is "armed" for a codex peer.
ARM_TIMEOUT = 150.0
POLL = 2.0


@dataclass
class CodexPeerHandle:
    thread_id: str
    server: CodexAppServer

    def poll(self) -> int | None:
        """Same contract as `subprocess.Popen.poll()`, for the liveness
        check the conversation test already runs on every peer."""
        if self.server.alive():
            return None
        return self.server.returncode

    @property
    def returncode(self) -> int | None:
        """Alongside `poll()`, matching `subprocess.Popen`'s own pair --
        callers that report a failure want the code without re-triggering
        the liveness check `poll()` implies."""
        return self.server.returncode


def _dump_thread(server: CodexAppServer, thread_id: str, log_dir: str, name: str) -> None:
    """Best-effort transcript for a post-mortem -- never itself a reason to
    fail the test that asked for one."""
    with contextlib.suppress(Exception):
        os.makedirs(log_dir, exist_ok=True)
        thread = server.resume_thread(thread_id)
        with open(os.path.join(log_dir, name), "w") as f:
            json.dump(thread, f, indent=2)


@contextlib.contextmanager
def codex_peer(brief: str, *, env: dict[str, str], log_dir: str):
    """Start a codex thread, deliver `brief` as its opening turn, and yield
    once that turn has finished and the thread is idle again.

    `env` is passed straight to `CodexAppServer` -- the same dict
    `mail_woken_peer`'s other harnesses get (`busctl.bus_env`), so the shell
    commands codex runs inside its own turns see the same `AGENT_BUS_HOME`
    every other peer does. Confirmed live: a driven turn's shell inherits the
    app-server process's own environment.

    Deliberately no `codex_home` override -- codex's own SQLite queue is
    keyed by thread id in one shared home, and every `agent-bus send` the
    counterpart runs spawns its own app-server against the *default* home.
    Pointing this one somewhere else would silently break delivery: nothing
    would ever see the counterpart's queue writes.
    """
    if not shutil.which("codex"):
        # Every other peer in this suite skips a missing binary rather than
        # failing (`mail_woken_peer`), because it is an environment fact and
        # not a defect. This one used to raise `CodexError` out of the
        # app-server's Popen instead, so a laptop without codex installed
        # reported a red test in a suite whose other three rows had passed.
        pytest.skip("codex is not on PATH")
    server = CodexAppServer(env=env)
    server.start()
    cleanup_thread_id: str | None = None
    try:
        started = server.request("thread/start", {})
        thread_id: str = str((started.get("thread") or started)["id"])
        cleanup_thread_id = thread_id
        server.start_turn(thread_id, brief)

        # thread/resume can fail with a genuine, transient server-side error
        # immediately after turn/start returns -- "rollout ... is empty",
        # confirmed live: the rollout file exists but has no content flushed
        # to it yet. Not the same race as an unstarted thread having no
        # rollout at all (that one is permanent); this one clears on its
        # own within the deadline, so it is retried exactly like "not idle
        # yet" rather than treated as a real failure.
        deadline = time.time() + ARM_TIMEOUT
        thread: dict[str, object] = {}
        while thread.get("status") != {"type": "idle"}:
            if time.time() > deadline:
                _dump_thread(server, thread_id, log_dir, "thread-not-idle.json")
                raise AssertionError(
                    f"codex thread {thread_id} had not gone idle after its "
                    f"opening turn within {ARM_TIMEOUT:.0f}s; see "
                    f"{log_dir}/thread-not-idle.json"
                )
            if not server.alive():
                raise AssertionError(
                    f"codex app-server for thread {thread_id} exited while "
                    f"delivering its opening turn (rc={server.returncode})"
                )
            try:
                thread = server.resume_thread(thread_id)
            except CodexError:
                time.sleep(POLL)
                continue
            if thread.get("status") != {"type": "idle"}:
                time.sleep(POLL)

        yield CodexPeerHandle(thread_id=thread_id, server=server)
    finally:
        if cleanup_thread_id is not None:
            _dump_thread(server, cleanup_thread_id, log_dir, "thread-final.json")
        server.close()
