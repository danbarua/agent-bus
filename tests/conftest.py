"""Shared across both test suites.

tests/support holds helpers neither suite owns -- the headless Claude peer and
the spendy opt-in gate are used by the agent_bus integration tests and by
agent_bridge's alike. Putting them in either suite would make one depend on the other's tests.
"""
import contextlib
import os
import secrets
import shutil
import signal
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "support"))

# After the path insert above, not with the imports at the top: `reaping` lives
# in `tests/support`, which is only importable once that line has run.
from reaping import reapable, still_running  # noqa: E402


@pytest.fixture(autouse=True)
def _never_the_developers_own_bus(tmp_path, monkeypatch):
    """Every test gets its own AGENT_BUS_HOME. No exceptions, none needed.

    Three tests in one week read the machine they were running on. The bridge
    tests found the developer's live grok and omp registries. `test_log.py`
    asked `get_self()` who it was and got a real Claude session, which made one
    assertion unsatisfiable on this laptop and vacuous in CI -- passing there
    for a reason having nothing to do with the code.

    Isolating in each test is the fix that keeps not happening: 21 of 30 files
    did it, which is exactly the ratio that makes the other 9 invisible. So the
    default is inverted. A test cannot forget.

    **Deliberately only AGENT_BUS_HOME.** The four registry directories --
    SESSIONS_DIR, SOCK_DIR, GROK_DIR, OMP_DIR -- are NOT touched here, because
    the integration tests that message a real Claude session must see the real
    ones, and they un-isolate exactly those while still passing an explicit
    home (see `busctl.bus_env`). Adding them to this net would break the tests
    that most need to run.

    Measured before landing: forcing this on the whole suite changed nothing.
    481 passed either way. It is a net, not a behaviour change.
    """
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path / "ab-home"))


#: The decision about *which* pids may be signalled lives in
#: `tests/support/reaping.py` -- #214 made it a thing worth testing on its own,
#: and a conftest module cannot be imported by name.

#: Listeners we have signalled, so the one-off sweep below can check they went.
_SIGNALLED: list[int] = []


@pytest.fixture(autouse=True)
def _no_listener_outlives_its_test(tmp_path):
    """SIGTERM the listeners a test started. They are detached, so nothing else will.

    `lifecycle.session_start` spawns a real listener for every non-Claude peer,
    and `agents.join` does the same -- deliberately detached, because the point
    is that it outlives the hook invocation that started it. Only
    `session_end`/`leave` stop one, so a test exercising half a pair leaves a
    process running for as long as the machine stays up. Left alone they
    accumulate, all still publishing, all competing for the CPU the assertions
    around them are timing out on.

    Scoped to this test's own `tmp_path`, so it can never reach a listener a
    developer is actually using: a pid recorded there was written by a listener
    this test started, minutes ago at most. `/proc` is belt and braces on top
    of that, against a pid the OS has already recycled.

    **Signal and move on.** Waiting here for each listener to actually die cost
    64 seconds on a 35-second suite and prevented nothing -- they go on their
    own, and the sweep at session end is what proves it. **And no subprocess:**
    `ps` would be the obvious way to read a command line, but
    `test_presence_reconciliation` monkeypatches `subprocess.Popen` and that
    patch is still live during this teardown, so `subprocess.run` picks up the
    fake and raises. `/proc` is a file read.
    """
    yield
    # Anywhere under this test's own tmp_path, not just the env var's home:
    # plenty of tests pass an explicit `home=` of their own (`tmp_path` itself,
    # `tmp_path / "bus"`), and their listeners write pid files there. Scoping
    # to `ab-home` alone reaps none of those.
    for pid_file in sorted(tmp_path.rglob("listeners/*.pid")):
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            continue
        if not reapable(pid):
            continue
        _SIGNALLED.append(pid)
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)



@pytest.fixture(scope="session", autouse=True)
def _nothing_survives_the_session():
    """One sweep at the end for anything that ignored its SIGTERM.

    Session-scoped because it is a safety net, not a per-test cost: a listener
    wedged in a syscall is rare, and paying for that possibility 620 times over
    is what made the per-test wait cost more than the rest of the suite.
    """
    yield
    for pid in _SIGNALLED:
        if still_running(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)



@pytest.fixture(autouse=True)
def _never_the_developers_own_log(tmp_path, monkeypatch):
    """Every test writes its JSONL somewhere disposable.

    The default destination is `$XDG_STATE_HOME/agent-bus/agent-bus.jsonl`,
    which is a real file on the machine running the suite. Without this net a
    test run appends to the developer's own log -- and a test that asserts on
    what it finds there would read someone else's records.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _never_the_developers_own_credential(monkeypatch):
    """No test reads the login Keychain, whatever is in it.

    The same net as the bus home above, for the same reason and one step
    worse. `read_cloud_token` prefers the Keychain over the file, so the day a
    real `agent-bus-cloud-token` item exists, every test handing it a token
    fixture would silently be handed the developer's live cloud credential
    instead -- passing, testing nothing, and putting a production bearer into
    whatever the test does next.

    A test that means to exercise the Keychain patches this back explicitly.
    """
    monkeypatch.setattr("agent_bridge.bridge._keychain_token", lambda: None)


@pytest.fixture
def short_sock_dir():
    """A socket directory short enough for AF_UNIX to bind in.

    pytest's tmp_path is already most of the ~104-byte limit before a
    <pid>.sock is appended. Over it, bind() fails on the listener's background
    thread, pytest downgrades that to a warning, and the test passes having
    proved nothing about a listener that never came up.

    tests/agent_bus/test_conventions.py fails the suite if AGENT_BUS_SOCK_DIR is
    pointed at tmp_path again.
    """
    base = f"/tmp/ab-{secrets.token_hex(4)}"
    sock_dir = f"{base}/s"
    os.makedirs(sock_dir, exist_ok=True)
    yield sock_dir
    shutil.rmtree(base, ignore_errors=True)
