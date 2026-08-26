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
