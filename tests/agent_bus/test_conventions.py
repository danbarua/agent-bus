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
import re

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

# The configs that gate code: one on every pull request, one before a release,
# one before a deploy. cloudbuild.e2e.yaml and cloudbuild.image.yaml do other
# jobs.
GATE_CONFIGS = ["cloudbuild.test.yaml", "cloudbuild.yaml", "cloudbuild.deploy.yaml"]
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


def test_the_firehose_has_call_sites_and_debug_is_not_advertised():
    """An advertised level with nothing on it is worse than a missing one.

    DEBUG was documented as "more, when something is being taken apart" and
    emitted nothing -- the whole package made exactly one logging call. You
    turn it on, see the same records as INFO, and conclude the thing you were
    hunting did not happen. Nothing could catch it: the level existed, the
    parser accepted it, every test passed.

    Two rules, because the levels differ in kind. INFO is emitted from one
    choke point inside log.py -- the @logged decorator -- and that is the
    design. TRACE is the opposite: it is only worth anything emitted from the
    places being traced, so it must have callers OUTSIDE log.py.
    """
    src = os.path.join(REPO, "src", "agent_bus")
    elsewhere = ""
    for dirpath, _, filenames in os.walk(src):
        for fn in filenames:
            if fn.endswith(".py") and fn != "log.py":
                with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                    elsewhere += f.read()

    assert "log.trace(" in elsewhere, (
        "TRACE is advertised and nothing outside log.py emits at it. A "
        "firehose with no call sites answers a question wrongly instead of "
        "not answering it -- which is what DEBUG did."
    )

    # The ladder is the indented block of level names in the docstring.
    # Prose ABOUT debug is fine and there is some -- explaining an absence is
    # not advertising a level.

    with open(os.path.join(src, "log.py"), encoding="utf-8") as f:
        doc = f.read().split('"""')[1]
    ladder = re.findall(r"^ {4}(\w[\w /]*?)\s{2,}\S", doc, re.M)
    assert "DEBUG" not in {rung.strip() for rung in ladder}, (
        f"DEBUG is on the ladder again and nothing emits at it: {ladder}. "
        "Give it call sites first, or leave it off."
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


def test_every_gate_is_listed_as_one():
    """A config that calls the gate script is a gate, whether or not anyone
    said so. The two checks around this one iterate GATE_CONFIGS, so a build
    config added outside that list is exempt from both while looking governed.
    """
    found = sorted(
        fn for fn in os.listdir(REPO)
        if fn.startswith("cloudbuild") and fn.endswith(".yaml")
        and GATE_SCRIPT in open(os.path.join(REPO, fn), encoding="utf-8").read()
    )
    assert found == sorted(GATE_CONFIGS), (
        f"these call {GATE_SCRIPT}: {found}\nGATE_CONFIGS says: "
        f"{sorted(GATE_CONFIGS)}\nAdd it, so the checks below cover it too."
    )


def test_every_gate_requires_the_firestore_emulator():
    """The store tests skip themselves when there is no emulator, and say so
    quietly.

    That is right on a laptop and wrong in a build. `ci-build.sh` makes them
    mandatory only when FIRESTORE_EMULATOR_HOST is set, so a gate that starts
    no emulator runs ten fewer tests and stays green -- and the gate most
    likely to be written without one is the gate that deploys the server,
    which is the one place those tests are load-bearing.

    Nothing in a run can report this: the skips are the intended behaviour
    everywhere else.
    """
    offenders = []
    for name in GATE_CONFIGS:
        text = open(os.path.join(REPO, name), encoding="utf-8").read()
        body = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
        )
        if "FIRESTORE_EMULATOR_HOST" not in body:
            offenders.append(f"{name}: runs the gate without requiring an emulator")
        elif "emulators" not in body:
            offenders.append(f"{name}: points at an emulator it never starts")
    assert not offenders, (
        "\n  ".join(["a gate is testing less than it says:", *offenders])
        + "\n\nCopy the firestore-emulator step and the env line from "
        "cloudbuild.test.yaml."
    )


def _throwaway_repo_with_both_tag_namespaces(root):
    """A repo holding a package release, then a later server release."""
    import subprocess

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=root, check=True, capture_output=True,
        )

    git("init", "-q", "-b", "main")
    for tag in ("v0.1.0", "cloud-v9.9.9"):
        open(os.path.join(root, tag), "w").close()
        git("add", tag)
        git("commit", "-qm", tag)
        git("tag", "-a", tag, "-m", tag)
    return git


def test_the_packages_version_comes_from_v_tags_only(tmp_path):
    """`cloud-v*` is the server's namespace and must not name the package.

    setuptools_scm's default tag regex allows a `<word>-` prefix and discards
    it, so `cloud-v9.9.9` parses as 9.9.9 -- measured, as an sdist called
    `agent_bus_team-9.9.9.tar.gz`. Every build then reports a version the
    package never released and a later real release collides with.

    Nothing in a run can see it. The version is only wrong on a checkout where
    a cloud tag is the nearest one, which is every checkout after a deploy and
    none before the first.
    """
    import subprocess
    import tomllib

    with open(os.path.join(REPO, "pyproject.toml"), "rb") as f:
        options = tomllib.load(f)["tool"]["hatch"]["version"].get("raw-options", {})
    describe = options.get("git_describe_command")
    assert describe, (
        "pyproject sets no git_describe_command, so hatch-vcs uses the default "
        "and a `cloud-v*` tag becomes the package's version."
    )

    _throwaway_repo_with_both_tag_namespaces(tmp_path)
    out = subprocess.run(describe, cwd=tmp_path, check=True,
                         capture_output=True, text=True).stdout
    assert out.startswith("v0.1.0"), (
        f"the configured describe resolved to {out.strip()!r}, so the package "
        "would be versioned from the server's tag namespace."
    )


def test_the_check_above_would_notice(tmp_path):
    """The default really does take the cloud tag -- otherwise the check above
    passes because there is nothing to catch, which is the failure mode of
    every test written against a defect that was fixed first."""
    import subprocess

    _throwaway_repo_with_both_tag_namespaces(tmp_path)
    out = subprocess.run(["git", "describe", "--tags", "--long"], cwd=tmp_path,
                         check=True, capture_output=True, text=True).stdout
    assert out.startswith("cloud-v9.9.9"), (
        f"unfiltered describe gave {out.strip()!r}; the fixture no longer "
        "reproduces the thing the check above defends against."
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


# --------------------------------------------------------- the version it reports


def _deploy_build_args() -> list[str]:
    """The `args` of cloudbuild.deploy.yaml's `build` step, without a YAML parser.

    `pyyaml` is not a dependency of anything here and adding one so a test can
    read four lines would be the wrong trade.
    """
    path = os.path.join(REPO, "cloudbuild.deploy.yaml")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "- id: build")
    out = []
    for ln in lines[start:]:
        if ln.strip().startswith("- id:") and out:
            break
        s = ln.strip()
        if s.startswith("- ") and not s.startswith("- id:") and "name:" not in s:
            out.append(s[2:])
    return out


def test_the_image_is_told_the_version_it_is_tagged_with():
    """`/health` and MCP `serverInfo` reported `0+unknown` on every deploy this
    service ever had, because `AGENT_BUS_CLOUD_VERSION` was read and never set.

    It is baked into the image rather than set on the service: `infra/staging`
    has `ignore_changes` on the image because CI owns it there, so terraform
    setting the version would be stating one it cannot know -- staging would
    report one build while running another. The answer has to travel with the
    thing being identified.
    """
    args = _deploy_build_args()
    assert "--build-arg" in args, args
    version = args[args.index("--build-arg") + 1]
    assert version == "VERSION=${TAG_NAME}", version

    tagged = [a for a in args if a.startswith("${_AR_IMAGE}:")]
    assert "${_AR_IMAGE}:${TAG_NAME}" in tagged, (
        f"the version passed in is {version} but the image is tagged {tagged} -- "
        "a build that reported a version it was not tagged with is worse than "
        "one that reports nothing"
    )


def test_the_dockerfile_turns_that_arg_into_the_variable_the_code_reads():
    """Something in `cloud/` reads `AGENT_BUS_CLOUD_VERSION`. A build arg that
    never became that variable would leave the endpoint saying `0+unknown`
    while the pipeline looked correct.

    Which module reads it is not the claim, and naming one was wrong: this
    said `cloud/app.py` until `version()` moved to `cloud/config.py`, and then
    failed over a rename while the thing it exists to protect was intact.
    """
    with open(os.path.join(REPO, "cloud", "Dockerfile"), encoding="utf-8") as f:
        dockerfile = f.read()
    assert "ARG VERSION=" in dockerfile
    assert "ENV AGENT_BUS_CLOUD_VERSION=${VERSION}" in dockerfile

    cloud = os.path.join(REPO, "cloud")
    readers = [
        name for name in sorted(os.listdir(cloud)) if name.endswith(".py")
        if 'AGENT_BUS_CLOUD_VERSION"' in open(
            os.path.join(cloud, name), encoding="utf-8").read()
    ]
    assert readers, "nothing in cloud/ reads the variable the image sets"


def test_production_cannot_deploy_a_container_nobody_named():
    """The default was Google's hello container, for a first apply that has
    happened once and cannot recur. What outlived it was a config where
    forgetting one line in a tfvars replaces the live service with a demo page,
    silently -- the same trap that nearly emptied the OAuth allowlist on
    2026-09-01, on a different variable.

    Staging keeps its default deliberately: it is disposable, so its bootstrap
    case is the recurring one. That asymmetry is the point, so both halves are
    asserted."""
    def _has_default(root: str) -> bool:
        """A real `default =` assignment, not the word in a comment -- both of
        these blocks explain at length why they do or do not have one."""
        path = os.path.join(REPO, "infra", root, "variables.tf")
        with open(path, encoding="utf-8") as f:
            body = f.read()
        start = body.index('variable "image"')
        block = body[start:body.index("\n}", start)]
        return any(ln.strip().startswith("default") for ln in block.splitlines())

    assert not _has_default("cloud"), (
        "production's image has a default again: forgetting it in a tfvars now "
        "silently replaces the service"
    )
    assert _has_default("staging"), (
        "staging lost its default -- it is recreated on purpose, so bootstrap "
        "is its normal case"
    )


def test_every_infra_stack_is_named_in_the_infra_readme():
    """`infra/README.md` says "one directory per terraform stack" and then
    lists them. It listed `ci/` and stopped -- `cloud/` and `staging/` were
    added and the table was not, so the file that exists to orient a reader
    described a third of what was there.

    Mechanical drift, so a mechanical guard: adding a stack without a row now
    fails here rather than being noticed a month later.
    """
    infra = os.path.join(REPO, "infra")
    stacks = sorted(
        d for d in os.listdir(infra)
        if os.path.isfile(os.path.join(infra, d, "providers.tf"))
    )
    assert stacks, "no stacks found -- this guard would pass forever in silence"

    with open(os.path.join(infra, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    missing = [s for s in stacks if f"`{s}/`" not in readme]
    assert not missing, (
        f"stacks with no row in infra/README.md: {missing}. A reader who opens "
        "that file to find out what is deployed would not learn these exist."
    )


def _ignored_changes(root: str) -> set[str]:
    """The bare field names inside a root's `ignore_changes = [...]`.

    Bracket-matched rather than regexed: the list contains
    `template[0].containers[0].image`, so a `[^]]*` character class stops at
    the first `]` inside it and never sees the entries after it.
    """
    with open(os.path.join(REPO, "infra", root, "run.tf"), encoding="utf-8") as f:
        body = f.read()
    at = body.find("ignore_changes")
    if at == -1:
        return set()
    start = body.index("[", at)
    depth, i = 0, start
    while i < len(body):
        if body[i] == "[":
            depth += 1
        elif body[i] == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return {ln.strip().rstrip(",") for ln in body[start + 1:i].splitlines() if ln.strip()}


def test_staging_ignores_what_ci_stamps_and_production_does_not():
    """The two roots are deployed by different things, so they drift
    differently -- and the asymmetry is the point, not an oversight.

    CI updates staging with `gcloud run services update`, and gcloud stamps
    `client`/`client_version` on the service. Terraform does not set them, so
    every staging plan offers to null them: a change on every plan, after every
    tag, forever. `infra/cloud/README.md` says **read the plan**, and an apply
    that always shows a change trains a reader to skim -- the habit that caught
    an apply about to empty the production allowlist.

    Production must keep reporting them. It has neither today because terraform
    deploys it; if they appear, somebody bypassed the promotion with `gcloud`,
    and that drift is a signal rather than noise.
    """
    staging, cloud = _ignored_changes("staging"), _ignored_changes("cloud")
    assert "template[0].containers[0].image" in staging, (
        f"the parser found no image entry, so it is reading the wrong thing: {staging}"
    )

    for field in ("client", "client_version"):
        assert field in staging, (
            f"staging no longer ignores `{field}`: every plan will show CI's "
            f"own deploy as drift. ignore_changes = {sorted(staging)}"
        )
        assert field not in cloud, (
            f"production ignores `{field}` -- that hides a `gcloud` deploy "
            "that bypassed the terraform promotion"
        )
