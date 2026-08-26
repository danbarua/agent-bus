"""Make the sibling harness registry importable without installing the tests."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def per_test_log_file(tmp_path, monkeypatch):
    """Give each test its own agent-bus log, and let children inherit it.

    A harness spawns `agent-bus mcp` with its own environment, so the peer's
    records land in the same file as the driver's -- which is the only way to
    see the two halves of a round trip in order.

    Written under tmp_path, so `--basetemp` decides whether it survives the
    run. pytest empties an explicit basetemp on every run, so pointing it at a
    mounted directory keeps the last run's logs without accumulating every
    previous one.
    """
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", str(tmp_path / "agent-bus.jsonl"))
