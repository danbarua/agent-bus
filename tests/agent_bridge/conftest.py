"""Per-test logs for the suite that starts a real bridge."""
import re

import pytest


@pytest.fixture(autouse=True)
def per_test_log_file(request, tmp_path, monkeypatch):
    """Give each test a log named after itself, and let children inherit it.

    A harness spawns `agent-bus mcp` with its own environment, so the peer's
    records land in the same file as the driver's -- which is the only way to
    see the two halves of a round trip in order.

    Named after the test, not `agent-bus-log.jsonl`, because pytest's
    `...current` symlink repoints as each parametrised case runs, so a file
    open under it walks from one case to the next with nothing saying it
    moved. The filename says which case wrote it.

    `-log` on purpose too: a bare `agent-bus.jsonl` beside a bus's inboxes and
    roster reads like state something depends on, and the first instinct on
    finding one is to keep it. This is diagnostics, and disposable.

    Written under tmp_path, so `--basetemp` decides whether it survives the
    run. pytest empties an explicit basetemp on every run, so pointing it at a
    mounted directory keeps the last run's logs without accumulating every
    previous one.
    """
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.name).strip("-")
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", str(tmp_path / f"{name}-log.jsonl"))
