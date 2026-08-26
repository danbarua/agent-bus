"""Fixtures for the integration tests."""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def per_test_log_file(request, tmp_path, monkeypatch):
    """Give each test a log named after itself, and let children inherit it.

    A harness spawns `agent-bus mcp` with its own environment, so the peer's
    records land in the same file as the driver's -- the only way to read the
    two halves of a round trip in order.

    Named after the test because pytest's `...current` symlink repoints as each
    parametrised case runs: open the one under `test_a_harness_joins_the_bcurrent`
    and it walks omp, grok, codex, pi underneath you with nothing in the file
    saying it moved.

    Written under tmp_path, so `--basetemp` decides whether it survives.
    """
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.name).strip("-")
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", str(tmp_path / f"{name}-log.jsonl"))


@pytest.fixture
def bus_home(tmp_path):
    """An empty bus of this test's own."""
    home = tmp_path / "bus"
    home.mkdir()
    return home


@pytest.fixture
def project(tmp_path):
    """A working directory to point a harness at."""
    d = tmp_path / "proj"
    d.mkdir()
    return d


@pytest.fixture
def evidence(tmp_path):
    """Where a driver's shell writes what it did.

    Outside the bus home, so nothing in here can be mistaken for bus state.
    """
    d = tmp_path / "evidence"
    d.mkdir()
    return d


@pytest.fixture
def claude_session():
    """A live headless Claude session, and the name others address it by.

    Briefed to answer known words, so an assertion can be about the reply's
    content rather than about something having arrived. Nothing is installed on
    the Claude side; it replies with its own native tools.
    """
    import shutil

    if not shutil.which("claude"):
        pytest.skip("`claude` is not on PATH")
    from claude_peer import headless_claude_peer

    with headless_claude_peer() as name:
        yield name
