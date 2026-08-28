"""Shared across both test suites.

tests/support holds helpers neither suite owns -- the headless Claude peer and
the spendy opt-in gate are used by the agent_bus integration tests and by
agent_bridge's alike. Putting them in either suite would make one depend on the other's tests.
"""
import os
import secrets
import shutil
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "support"))


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
