"""Per-test logs for the suite that starts a real bridge."""
import pytest


@pytest.fixture(autouse=True)
def per_test_log_file(tmp_path, monkeypatch):
    """The bridge, its listener and the Claude peer all log to one file per
    test, so a round trip reads in order. `--basetemp` decides if it survives.
    """
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", str(tmp_path / "agent-bus.jsonl"))
