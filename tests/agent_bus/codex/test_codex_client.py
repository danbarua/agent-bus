"""Codex outbound client, driven against a stub app-server.

The stub speaks the real framing (NDJSON, JSON-RPC-shaped without a "jsonrpc"
field) so the client's protocol handling is exercised without needing codex
installed. The live protocol was verified separately against codex-cli 0.149.0;
these tests guard the parts we wrote.
"""

import json
import os
import subprocess
import sys

import pytest

from agent_bus.adapters.transport.codex import (
    CodexAppServer,
    CodexError,
    CodexTarget,
    resolve_thread,
    send_to_codex,
)

sys.path.insert(0, os.path.dirname(__file__))
from stub_app_server import ARCHIVED_THREAD_UUID

STUB = os.path.join(os.path.dirname(__file__), "stub_app_server.py")
STUB_CMD = (sys.executable, STUB)


def test_initialize_happens_before_anything_else():
    """The server rejects everything until initialize completes, so the client
    must do it on start() rather than lazily."""
    with CodexAppServer(STUB_CMD) as server:
        threads = server.list_threads()
    assert [t["id"] for t in threads] == [
        "01a01cb8-1f72-7e71-97ca-69349d003abc",
        "01a01cb8-1f72-7e71-97ca-69349d003abd",
    ]


def test_queue_message_returns_the_submission():
    with CodexAppServer(STUB_CMD) as server:
        sub = server.queue_message("01a01cb8-1f72-7e71-97ca-69349d003abc", "hello codex")
    assert sub["id"] == "queued-submission-id"
    assert sub["input"][0]["text"] == "hello codex"
    assert sub["clientUserMessageId"]


def test_env_replaces_os_environ_and_alive_tracks_the_subprocess():
    """`env=` is for a caller that needs the app-server's own process --
    and anything it shells out to inside a driven turn -- to see a specific
    environment (codex_peer.py's AGENT_BUS_HOME), not whatever this test
    process happens to have. Passing a minimal env that excludes almost
    everything and still reaching a working handshake is the proof it
    replaced os.environ rather than only adding to it."""
    server = CodexAppServer(STUB_CMD, env={"PATH": os.environ.get("PATH", "")})
    assert not server.alive()
    with server:
        assert server.alive()
        server.list_threads()
    assert not server.alive()


def test_server_errors_surface_as_codex_error():
    """thread/queue/add on an archived thread is a real server-side error; the
    client must raise rather than return a partial result."""
    with CodexAppServer(STUB_CMD) as server, pytest.raises(CodexError) as e:
        server.queue_message("archived-thread", "hello")
    assert "archived" in str(e.value)


def test_empty_message_rejected_before_the_wire():
    with CodexAppServer(STUB_CMD) as server, pytest.raises(CodexError):
        server.queue_message("01a01cb8-1f72-7e71-97ca-69349d003abc", "")


def test_notifications_are_kept_not_discarded():
    """thread/queue/changed is the only delivery signal, so interleaved
    notifications must not be dropped while awaiting a response."""
    with CodexAppServer(STUB_CMD) as server:
        server.queue_message("01a01cb8-1f72-7e71-97ca-69349d003abc", "hello")
        methods = {n["method"] for n in server.notifications}
    assert "remoteControl/status/changed" in methods


def test_send_to_codex_resolves_a_name():
    sub = send_to_codex(CodexTarget("alpha"), "by name", command=STUB_CMD)
    assert sub["input"][0]["text"] == "by name"


def test_send_to_codex_passes_a_uuid_straight_through():
    sub = send_to_codex(CodexTarget(
        "01a01cb8-1f72-7e71-97ca-69349d003abc",
    ), "by id", command=STUB_CMD)
    assert sub["id"] == "queued-submission-id"


def test_unknown_name_is_an_error():
    with pytest.raises(CodexError) as e:
        send_to_codex(CodexTarget("nope"), "text", command=STUB_CMD)
    assert "no codex thread" in str(e.value)


# --------------------------------------------------------- resume_thread / start_turn
#
# Both stayed after #292's `wake` reversal (see the module docstring):
# `codex_peer` (tests/support/codex_peer.py) uses them to deliver its own
# opening turn on a thread it just started and holds open for the whole
# exchange -- the one shape they are sound for.


def test_resume_thread_returns_the_thread():
    with CodexAppServer(STUB_CMD) as server:
        thread = server.resume_thread("some-thread")
    assert thread["status"] == {"type": "idle"}


def test_start_turn_begins_a_turn():
    with CodexAppServer(STUB_CMD) as server:
        turn = server.start_turn("some-thread", "hello")
    assert turn == {"id": "new-turn-id", "status": "inProgress",
                    "input": [{"type": "text", "text": "hello"}]}


def test_start_turn_rejects_an_archived_thread():
    with CodexAppServer(STUB_CMD) as server, pytest.raises(CodexError) as e:
        server.start_turn(ARCHIVED_THREAD_UUID, "hello")
    assert "is archived" in str(e.value)


def test_send_to_codex_issues_only_the_queue_write(tmp_path):
    """The regression guard for #292's own reversal: an earlier version
    tried `turn/start` before the queue, and closing the server it ran on
    silently killed the turn -- confirmed live against a real app-server,
    see the module docstring. `initialize` is allowed; nothing else that
    could start or steer a turn is."""
    log = tmp_path / "methods.log"
    os.environ["CODEX_STUB_METHOD_LOG"] = str(log)
    try:
        sub = send_to_codex(CodexTarget(
            "01a01cb8-1f72-7e71-97ca-69349d003abc",
        ), "still reaches you", command=STUB_CMD)
    finally:
        del os.environ["CODEX_STUB_METHOD_LOG"]
    assert sub["id"] == "queued-submission-id"
    methods = log.read_text().splitlines()
    assert "thread/queue/add" in methods
    assert "turn/start" not in methods
    assert "turn/steer" not in methods


def test_an_archived_thread_is_refused_by_the_queue_via_send_to_codex():
    """UUID-shaped target, deliberately: the bare word "archived-thread"
    would fail name resolution before ever reaching the queue, and that
    failure also happens to contain the substring "archived" (it quotes the
    target back), which let an earlier version of this assertion pass for a
    reason that had nothing to do with what the test claims."""
    with pytest.raises(CodexError) as e:
        send_to_codex(CodexTarget(ARCHIVED_THREAD_UUID), "hello", command=STUB_CMD)
    assert "is archived" in str(e.value)


# ------------------------------------------------------------------- resolution


THREADS = [
    {"id": "aaa", "sessionId": "aaa", "name": "alpha"},
    {"id": "bbb", "sessionId": "bbb", "name": "beta"},
    {"id": "ccc", "sessionId": "ccc", "name": "beta"},
]


def test_resolve_prefers_id_over_name():
    assert resolve_thread(THREADS, CodexTarget("aaa")) == "aaa"


def test_resolve_by_unique_name():
    assert resolve_thread(THREADS, CodexTarget("alpha")) == "aaa"


def test_resolve_refuses_a_duplicate_name():
    """Codex itself takes the most recently updated match and reports no
    ambiguity. We refuse: silently delivering to whichever session was touched
    last is misrouting that is very hard to notice afterwards."""
    with pytest.raises(CodexError) as e:
        resolve_thread(THREADS, CodexTarget("beta"))
    assert "2 threads are named" in str(e.value)


def test_resolve_returns_none_for_unknown():
    assert resolve_thread(THREADS, CodexTarget("missing")) is None


def test_dead_server_is_reported_not_hung():
    """If the app-server exits, the client must notice rather than block until
    its timeout."""
    dead = (sys.executable, "-c", "import sys; sys.exit(3)")
    with pytest.raises(CodexError) as e:
        CodexAppServer(dead, timeout=30).start()
    assert "exited" in str(e.value) or "pipe" in str(e.value)


def test_missing_binary_is_reported_clearly():
    with pytest.raises(CodexError) as e:
        CodexAppServer(("definitely-not-a-real-binary-xyz",)).start()
    assert "cannot run" in str(e.value)


def test_stub_matches_real_framing():
    """Guard the stub itself: it must emit NDJSON with no jsonrpc field, which
    is what the real app-server does."""
    p = subprocess.run(
        [sys.executable, STUB],
        input=json.dumps({"id": 1, "method": "initialize", "params": {}}) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    first = json.loads(p.stdout.splitlines()[0])
    assert first["id"] == 1
    assert "jsonrpc" not in first
    assert first["result"]["codexHome"]


# ------------------------------------------------- regressions from PR review


NOISY_STUB = (
    "import sys, json\n"
    "sys.stderr.write('x' * 300_000)\n"      # more than a pipe buffer
    "sys.stderr.flush()\n"
    "for line in sys.stdin:\n"
    "    m = json.loads(line)\n"
    "    if m.get('id'):\n"
    "        sys.stdout.write(json.dumps({'id': m['id'], 'result': {'ok': True}}) + '\\n')\n"
    "        sys.stdout.flush()\n"
)

SILENT_STUB = "import sys, time\nfor line in sys.stdin:\n    time.sleep(30)\n"


def test_stderr_is_drained_so_a_noisy_server_still_answers():
    """stderr is a pipe. If nothing drains it, a server that fills the buffer
    blocks before it can respond, and a working server looks like a timeout."""
    with CodexAppServer((sys.executable, "-c", NOISY_STUB), timeout=20) as server:
        assert server.request("thread/list", {}) == {"ok": True}
    assert server.stderr_tail(), "stderr should be retained for diagnostics"


def test_failed_initialize_does_not_leak_the_subprocess():
    """start() spawns the process, so start() owns it until the handshake
    completes. Otherwise a failing __enter__ leaves an app-server running:
    the with-body never runs, so __exit__ never fires."""
    server = CodexAppServer((sys.executable, "-c", SILENT_STUB), timeout=2)
    with pytest.raises(CodexError):
        server.start()
    assert server._proc is None, "process handle should be released"


def test_failed_enter_does_not_leak_the_subprocess():
    live = []

    class Tracking(CodexAppServer):
        def start(self):
            super().start()

    server = Tracking((sys.executable, "-c", SILENT_STUB), timeout=2)
    with pytest.raises(CodexError), server:
        pass
    live.append(server._proc)
    assert live == [None]


def test_late_response_is_not_eaten():
    """A reply that arrives while we await a different id must be kept, not
    discarded -- otherwise a late answer to a timed-out request is lost."""
    with CodexAppServer(STUB_CMD) as server:
        server._pending[99] = {"id": 99, "result": {"late": True}}
        assert server._await(99) == {"late": True}
