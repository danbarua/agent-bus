"""Both places that drive omp configure it themselves, per project.

Not gated on `spendy`: no harness runs here, only the launch each site builds.
That is what a regression silently drops. An omp left to read the developer's
own configuration finds *their* `agent-bus` server -- so this repository's
wireup need never be read for a test to pass -- consolidates the briefing it
was given into their memory bank, and reasons at whatever thinking level they
configured.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import harnesses
import mail_woken_peer


def _project_wireup(project: Path) -> dict:
    """The `agent-bus` server a launcher wrote into the project it was given.

    Project scope, not `~/.omp/agent/mcp.json`: a project entry is encountered
    before the same-named user entry, which is what makes this one win.
    """
    wireup = json.loads((project / ".omp" / "mcp.json").read_text())
    assert "agent-bus" in wireup["mcpServers"], wireup
    return wireup["mcpServers"]["agent-bus"]


def _overlay(argv: list[str], project: Path) -> dict:
    """The config overlay and thinking pin the launcher passed.

    Beside the project's other omp config, so a pruned `.e2e/` shows one
    directory per test rather than a sibling holding one generated file.

    `manage_skill` outlives `memory.backend: off` -- measured -- so a run with
    only that key still writes a skill into the developer's `managed-skills`.
    Thinking is pinned because one measured run at an inherited `high` took
    311s and hit its own `--max-time`.
    """
    assert "--config" in argv, f"no overlay in {argv}"
    path = Path(argv[argv.index("--config") + 1])
    assert path == project / ".omp" / "config.json", path
    overlay = json.loads(path.read_text())
    assert overlay["memory"]["backend"] == "off", overlay
    assert overlay["autolearn"]["enabled"] is False, overlay
    assert "--thinking" in argv, f"no thinking level pinned in {argv}"
    assert argv[argv.index("--thinking") + 1] == "low", argv
    return overlay


def test_the_harness_runner_configures_the_omp_it_drives(tmp_path, monkeypatch):
    home, project = tmp_path / "bus", tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    seen: dict = {}

    def _fake_run(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    harnesses._wire_omp(project, home)
    harnesses._run_omp(project, "brief", home=home)

    _overlay(seen["argv"], project)
    server = _project_wireup(project)
    assert server["env"]["AGENT_BUS_HOME"] == str(home), (
        "the server the wireup names must talk to this test's bus"
    )
    assert seen["env"]["AGENT_BUS_HOME"] == str(home), "the test's bus is not negotiable"


def test_the_mail_woken_peer_configures_the_omp_it_spawns(tmp_path, monkeypatch):
    cwd = tmp_path / "peer"
    cwd.mkdir()
    seen: dict = {}

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    mail_woken_peer._spawn_omp(
        "brief", model="anthropic/claude-haiku-4-5", cwd=str(cwd),
        env={**os.environ, "AGENT_BUS_HOME": str(tmp_path / "bus")},
        out=None, err=None)

    _overlay(seen["argv"], cwd)
    _project_wireup(cwd)
    settings = json.loads((cwd / ".omp" / "settings.json").read_text())
    assert settings["mcp.notifications"] is True, (
        "without this the peer never sees an inbox update, which is the whole "
        "mechanism the conversation tests measure"
    )
