"""A socket path that is too long fails on a thread, and the run stays green.

`AF_UNIX` caps a path at roughly 104 bytes. pytest's `tmp_path` is already most
of that before a `<pid>.sock` is appended, so a socket dir derived from it
overruns, `bind()` raises "AF_UNIX path too long" on the listener's background
thread, and pytest reports it as a *warning* -- in a passing run.

Nothing fails. The listener simply never comes up, and every assertion about it
is vacuous: a test that means to prove a peer is reachable proves nothing, and
says so in green. That is the worst available failure mode, and it is invisible
unless someone reads the warnings.

test_uds.py:16 already worked this out and uses a short random path under /tmp.
The rule was written down in a comment, in the file that needed it, and one
later test in that same file still reached for `tmp_path` anyway. Hence a check
rather than a comment.

The fix, wherever this fires:

    base = f"/tmp/ab-{secrets.token_hex(4)}"
    socks = f"{base}/s"
    os.makedirs(socks, exist_ok=True)
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", socks)
    ...
    shutil.rmtree(base, ignore_errors=True)

`AGENT_BUS_SESSIONS_DIR` has no such limit -- it holds ordinary files -- so
`tmp_path` is fine there and this only guards the socket dir.
"""

from __future__ import annotations

import os

TESTS = os.path.dirname(os.path.abspath(__file__))

# The bind target, and the thing whose length is capped.
SOCK_VAR = "AGENT_BUS_SOCK_DIR"

# pytest's per-test directory. Long by construction, and the whole problem.
TOO_LONG = "tmp_path"


def _test_files() -> list[str]:
    out = []
    for root, _, files in os.walk(TESTS):
        for fn in sorted(files):
            if fn.endswith(".py"):
                out.append(os.path.join(root, fn))
    return out


def test_no_test_points_the_socket_dir_at_a_pytest_tmp_path():
    offenders = []
    for path in _test_files():
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if SOCK_VAR in line and TOO_LONG in line:
                    rel = os.path.relpath(path, TESTS)
                    offenders.append(f"{rel}:{n}: {line.strip()}")

    assert not offenders, (
        f"{SOCK_VAR} must not be derived from pytest's tmp_path:\n  "
        + "\n  ".join(offenders)
        + "\n\nAF_UNIX caps the path near 104 bytes. Over it, bind() fails on a\n"
        "background thread, pytest downgrades that to a warning, and the test\n"
        "passes having proved nothing. Use a short path under /tmp instead --\n"
        "see this file's docstring for the shape, and test_uds.py:16 for the\n"
        "original working-out."
    )


def test_the_length_budget_is_real_and_not_folklore():
    """Verify the guard by watching the thing it guards against actually fail.

    If the platform limit were generous, the rule above would be cargo cult and
    should be deleted rather than obeyed. This binds a deliberately over-long
    path and asserts the kernel refuses it, so the constraint is measured here
    rather than remembered from a comment.
    """
    import socket
    import tempfile

    base = tempfile.mkdtemp(prefix="agent-bus-len-")
    long_path = os.path.join(base, "d" * 120, "x.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            s.bind(long_path)
        except OSError:
            return  # refused, as expected
        raise AssertionError(
            f"a {len(long_path)}-byte AF_UNIX path bound successfully; the "
            "length rule may no longer apply on this platform"
        )
    finally:
        s.close()
