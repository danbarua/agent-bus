"""Things that are green locally and broken at runtime.

Two of them so far, and they share a shape: the suite passes, and the code does
not work. Neither is caught by running the tests, so each gets a check that
inspects the source instead.


## A function-scoped import nothing ever resolves

`cmd_bridge` did `from .paths import get_home`. `get_home` lives in `store`. The
import sits inside the function, so nothing at import time touched it, and no
unit test invokes that CLI command -- 365 of them passed while
`agent-bus bridge` could not start at all. It took a container and a real Claude
session to find a typo.

Lazy imports in a CLI are deliberate here (they keep startup cheap), so the fix
is not to hoist them; it is to resolve them in a test.


## A socket path that is too long fails on a thread, and the run stays green.

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

import ast
import importlib
import os

TESTS = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(TESTS), "src", "agent_bus")

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


# --------------------------------------------------- function-scoped imports


def _lazy_relative_imports() -> list[tuple[str, int, str, tuple[str, ...]]]:
    """Every `from .x import y` written inside a function body.

    Module-scope imports are resolved the moment anything imports the module,
    so they cannot rot unnoticed. These can, and do.
    """
    found = []
    for root, _, files in os.walk(SRC):
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, SRC)
            pkg = "agent_bus"
            parent = os.path.dirname(rel)
            if parent:
                pkg += "." + parent.replace(os.sep, ".")
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=rel)
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if isinstance(node, ast.ImportFrom) and node.level:
                        base = pkg.split(".")
                        up = node.level - 1
                        if up:
                            base = base[:-up]
                        target = ".".join(base + ([node.module] if node.module else []))
                        found.append(
                            (rel, node.lineno, target, tuple(a.name for a in node.names))
                        )
    return found


def test_every_function_scoped_import_actually_resolves():
    """The check that would have caught `from .paths import get_home`."""
    broken = []
    for rel, line, target, names in _lazy_relative_imports():
        try:
            mod = importlib.import_module(target)
        except ImportError as e:
            broken.append(f"{rel}:{line}: cannot import module {target!r} ({e})")
            continue
        for name in names:
            if hasattr(mod, name):
                continue
            try:
                importlib.import_module(f"{target}.{name}")
            except ImportError:
                broken.append(f"{rel}:{line}: {target!r} has no {name!r}")

    assert not broken, (
        "these imports live inside function bodies, so nothing resolves them "
        "until that function runs:\n  " + "\n  ".join(broken)
    )


def test_the_check_above_is_looking_at_something():
    """A guard that found nothing to inspect would pass forever in silence."""
    assert len(_lazy_relative_imports()) > 5
