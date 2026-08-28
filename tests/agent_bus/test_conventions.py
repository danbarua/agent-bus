"""Things a passing test run cannot tell you.

Three so far. They share a shape: the suite is green and something is still
wrong -- a listener that never bound, an import that never resolved, two build
configs that have quietly stopped agreeing. Running the tests cannot catch any
of them, so each gets a check that reads the source instead.


## A function-scoped import nothing ever resolves

`cmd_bridge` did `from .paths import get_home`. `get_home` lives in `store`. The
import sits inside the function, so nothing at import time touched it, and no
unit test invokes that CLI command -- 365 of them passed while
`agent-bridge` could not start at all. It took a container and a real Claude
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

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Both suites: a socket dir pointed at tmp_path is as wrong in one as the other.
TESTS = os.path.join(REPO, "tests")
SRC = os.path.join(REPO, "src")

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


def test_no_test_can_reach_the_developers_own_bus():
    """The net in tests/conftest.py, guarded.

    It is one autouse fixture, and deleting it would break nothing visibly --
    the suite would stay green while every test quietly started reading
    whatever bus the developer is running. That is the failure it exists to
    prevent, so it needs a check that notices its absence.

    Three tests hit this class in one week: the bridge tests found the live
    grok and omp registries, and test_log.py asked get_self() who it was and
    got a real Claude session, which made one assertion unsatisfiable on the
    developer's laptop and vacuous in CI.
    """
    src = open(os.path.join(REPO, "tests", "conftest.py"), encoding="utf-8").read()
    assert "autouse=True" in src and "AGENT_BUS_HOME" in src, (
        "tests/conftest.py no longer isolates AGENT_BUS_HOME for every test. "
        "Without it a test reads whatever bus the developer is running, and "
        "the suite stays green while proving something about their laptop."
    )

    # And it must actually take effect, not merely be written down.
    assert os.environ.get("AGENT_BUS_HOME"), (
        "AGENT_BUS_HOME is unset while a test is running, so the fixture is "
        "present but not applying."
    )
    from agent_bus import store

    assert store.get_self() is None or "ab-home" in os.environ["AGENT_BUS_HOME"], (
        "this test can see an agent, so it is reading a real bus"
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
            # SRC is src/, so the relative path already begins with the
            # package name -- agent_bus/... or agent_bridge/...
            parent = os.path.dirname(rel)
            pkg = parent.replace(os.sep, ".") if parent else ""
            if not pkg:
                continue
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


# ------------------------------------------------------------- the build gate

# The two configs that gate code: one on every pull request, one before a
# release. cloudbuild.e2e.yaml and cloudbuild.image.yaml do other jobs.
GATE_CONFIGS = ["cloudbuild.test.yaml", "cloudbuild.yaml"]
GATE_SCRIPT = "ci-build.sh"


def test_both_gate_configs_call_the_one_script():
    """A gate defined twice is a gate that will be changed once.

    These two files held identical inline copies of the same commands, so
    adding a lint step meant making the same edit in both and noticing that it
    had to be made in both. The commands now live in ci-build.sh, which the
    local `ci-build` compose service also runs -- so what passes on a laptop is
    what passes in the build.
    """
    missing = [
        name for name in GATE_CONFIGS
        if GATE_SCRIPT not in open(os.path.join(REPO, name), encoding="utf-8").read()
    ]
    assert not missing, (
        f"{missing} no longer call {GATE_SCRIPT}. If the gate has moved, move it "
        "for all of them -- including the ci-build compose service."
    )


def test_the_gate_runs_every_suite_the_repo_has():
    """A suite nobody runs is worse than a suite nobody wrote.

    `cloud/` is a separate deployable with its own pyproject, so the root's
    `testpaths = ["tests"]` never reached it -- deliberately, because the bus
    must not grow a dependency on Firestore to run its own tests. The cost was
    that five cloud pull requests came back green having executed none of the
    tests they added. Ruff covers `cloud/` from the root, so the lint half
    looked right, which is the more misleading half.

    Discovered rather than listed: a third deployable added later gets the same
    check without anyone remembering to add it here.
    """
    import tomllib

    suites = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in {"node_modules", "dist", "infra"}
        ]
        if "pyproject.toml" not in filenames:
            continue
        with open(os.path.join(dirpath, "pyproject.toml"), "rb") as f:
            cfg = tomllib.load(f)
        paths = cfg.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths")
        if paths:
            suites.append(os.path.relpath(dirpath, REPO))

    gate = open(os.path.join(REPO, GATE_SCRIPT), encoding="utf-8").read()
    body = "\n".join(ln for ln in gate.splitlines() if not ln.lstrip().startswith("#"))

    unrun = [
        s for s in suites
        if not (("pytest tests/" in body or "pytest tests " in body) if s == "."
                else f"cd {s}" in body)
    ]
    assert not unrun, (
        f"{unrun} declare a pytest suite that {GATE_SCRIPT} never runs. A green "
        "build that executed none of the tests in a change is the failure this "
        "whole file exists for."
    )
    assert len(suites) >= 2, (
        f"only found {suites}; this check has stopped discovering anything and "
        "would now pass whatever the gate does."
    )


def test_no_gate_config_inlines_the_commands_again():
    """The failure this guards against is additive, not a deletion: someone
    adds a step here rather than to the script, and the two drift apart while
    everything still passes."""
    offenders = []
    for name in GATE_CONFIGS:
        text = open(os.path.join(REPO, name), encoding="utf-8").read()
        body = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
        )
        for cmd in ("pytest", "ruff check"):
            if cmd in body:
                offenders.append(f"{name}: runs {cmd!r} directly")
    assert not offenders, (
        "the gate is drifting back into the build configs:\n  "
        + "\n  ".join(offenders)
        + f"\n\nPut it in {GATE_SCRIPT}, which every caller shares."
    )


# ------------------------------------ what a harness hands its MCP child

# harnesses.py lives with the integration tests, but this guard must run in
# every sweep: both defects below were invisible in a passing run.
INTEGRATION = os.path.join(TESTS, "agent_bus", "integration")


def _server_env(home="/tmp/some-bus-home"):
    import sys
    from pathlib import Path

    if INTEGRATION not in sys.path:
        sys.path.insert(0, INTEGRATION)
    from harnesses import _server_env as build

    return build(Path(home))


def test_the_mcp_child_keeps_uvs_environment(monkeypatch):
    """Without UV_PROJECT_ENVIRONMENT, a container run eats the host's venv.

    codex and omp hand their MCP child a fixed environment. `uv run --project`
    with no UV_PROJECT_ENVIRONMENT falls back to `<project>/.venv`, and in the
    container that path is the bind mount -- so the run replaced a developer's
    macOS venv with a Linux one, and the next `uv run` on the host rebuilt it
    without saying why. Nothing failed. A test in the middle of the suite did,
    once, and looked like a flake.
    """
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/opt/venv")
    assert _server_env().get("UV_PROJECT_ENVIRONMENT") == "/opt/venv"


def test_the_mcp_child_keeps_agent_bus_settings(monkeypatch):
    """Passed by prefix, so the next one does not have to be remembered."""
    monkeypatch.setenv("AGENT_BUS_LOG_LEVEL", "INFO")
    monkeypatch.setenv("AGENT_BUS_SOMETHING_LATER", "yes")
    env = _server_env()
    assert env["AGENT_BUS_LOG_LEVEL"] == "INFO"
    assert env["AGENT_BUS_SOMETHING_LATER"] == "yes", (
        "a name-by-name allowlist is how the log variables went missing"
    )


def test_the_test_bus_wins_over_an_ambient_one(monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", "/not/this/one")
    assert _server_env("/tmp/the-test-bus")["AGENT_BUS_HOME"] == "/tmp/the-test-bus"


def test_no_credential_reaches_the_mcp_child(monkeypatch):
    """These configs are written to `.mcp.json` and onto codex's command line.

    The child is our MCP server and needs no model keys, so merging the whole
    environment would put an API key on disk and in `ps` for nothing.
    """
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY",
                "GITHUB_TOKEN", "SOME_SECRET"):
        monkeypatch.setenv(var, "sk-not-a-real-key")
    leaked = [k for k in _server_env()
              if any(w in k for w in ("KEY", "TOKEN", "SECRET", "PASSWORD"))]
    assert leaked == [], leaked
