"""Native directory-change detection, verified against a real OS pipe.

`select.kqueue()`/inotify need a genuine fd -- this process's own stdin,
piped through pytest's own capture machinery, is not one (confirmed live:
it is a socket, and kqueue's EVFILT_READ refuses it with EINVAL). Every test
here drives a real subprocess instead, exactly the shape `serve()` actually
runs under.
"""

import os
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
SRC = os.path.join(REPO, "src")


def _run_waiter(watch_dir: str, timeout: float, on_ready) -> subprocess.Popen:
    """Start a child that waits on (its own stdin, watch_dir) and prints the
    result of one `wait(timeout)` call, then calls `on_ready` once the child
    has had a moment to register its watch."""
    script = (
        f"import sys, time; sys.path.insert(0, {SRC!r})\n"
        "from agent_bus import fswatch\n"
        f"w = fswatch.watcher(sys.stdin.buffer, {watch_dir!r})\n"
        "print('READY', flush=True)\n"
        f"r = w.wait({timeout!r})\n"
        "print('RESULT', r, flush=True)\n"
        "w.close()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    ready_line = proc.stdout.readline()
    assert ready_line.strip() == "READY", ready_line
    on_ready()
    return proc


def _result(proc: subprocess.Popen) -> tuple[bool, bool]:
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line.startswith("RESULT "), line
    return eval(line[len("RESULT "):])  # noqa: S307  # our own trusted fixture output


def test_a_new_file_in_the_watched_directory_wakes_the_waiter(tmp_path):
    d = str(tmp_path)
    proc = _run_waiter(d, 10.0, lambda: time.sleep(0.3))
    with open(os.path.join(d, "new.txt"), "w") as f:
        f.write("hello\n")
    input_ready, dir_changed = _result(proc)
    assert (input_ready, dir_changed) == (False, True)
    proc.wait(timeout=10)


def test_an_atomic_rename_replace_wakes_the_waiter(tmp_path):
    """The exact hazard fswatch.py's own module docstring names:
    compact_inbox/ack_message rewrite via os.replace(), which swaps in a new
    inode -- a watch on the file itself would go stale silently. Watching
    the directory must catch this."""
    d = str(tmp_path)
    target = os.path.join(d, "inbox.jsonl")
    with open(target, "w") as f:
        f.write('{"a":1}\n')
    proc = _run_waiter(d, 10.0, lambda: time.sleep(0.3))
    tmp = target + ".tmp"
    with open(tmp, "w") as f:
        f.write('{"a":1,"read":true}\n')
    os.replace(tmp, target)
    input_ready, dir_changed = _result(proc)
    assert (input_ready, dir_changed) == (False, True)
    proc.wait(timeout=10)


def test_writing_to_stdin_wakes_the_waiter_as_input_ready(tmp_path):
    d = str(tmp_path)

    def send_stdin():
        time.sleep(0.3)
        assert proc.stdin is not None
        proc.stdin.write("hello\n")
        proc.stdin.flush()

    proc = _run_waiter(d, 10.0, lambda: None)
    send_stdin()
    input_ready, dir_changed = _result(proc)
    assert (input_ready, dir_changed) == (True, False)
    proc.wait(timeout=10)


def test_nothing_happening_times_out_with_both_false(tmp_path):
    d = str(tmp_path)
    proc = _run_waiter(d, 0.5, lambda: None)
    input_ready, dir_changed = _result(proc)
    assert (input_ready, dir_changed) == (False, False)
    proc.wait(timeout=10)
