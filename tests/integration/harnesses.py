"""How to drive each coding harness headlessly, and how it joins the bus.

One place for the per-vendor knowledge, so the tiers can be written once and
parametrised. The differences are not incidental -- they are the thing being
tested. A harness joins the bus in one of two ways:

**mcp** -- it runs `agent-bus mcp`, whose serve() calls session_start() on
startup. That registers the session as `other-<pid>` and publishes its
listener, because the MCP child does not inherit the harness's session
variables (grok's are hook-scoped; verified). The agent then calls the
`register` tool to claim a name, which *renames* that entry rather than adding
one.

**shell** -- the harness has no MCP and no hooks, so the prompt tells it to run
`agent-bus register` with its own shell tool. pi is this case, and it is worth
testing precisely because it is the floor: a harness with no integration points
at all can still join.

Where each one's MCP config goes differs too, and none of it may touch global
config:

| harness | config | note |
|---|---|---|
| omp    | `<project>/.mcp.json`           | no gate |
| grok   | `<repo>/.grok/config.toml`      | **must** be a folder the user trusted |
| codex  | none on disk -- `-c` overrides  | dotted TOML, parsed inline |
| pi     | none                            | no MCP at all |
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]

OMP_MODEL = os.environ.get("AGENT_BUS_OMP_MODEL", "xai-oauth/grok-4.6")
PI_MODEL = os.environ.get("AGENT_BUS_PI_MODEL")

# The command an MCP server config must launch. `uv run --project` keeps the
# test honest about which checkout it is exercising.
def _server_argv() -> list[str]:
    return ["uv", "run", "--project", str(REPO), "agent-bus", "mcp"]


@dataclass(frozen=True)
class Harness:
    name: str
    kind: str                       # what it should appear as on the bus
    binary: str
    joins_by: str                   # "mcp" | "shell"
    run: Callable[..., subprocess.CompletedProcess]
    wire: Callable[[Path, Path], Callable[[], None]] | None = None
    # grok will not *start* a project-scoped MCP server in an untrusted folder
    # -- it lists it and then does not launch it -- so its tier has to run in a
    # directory the user trusted by hand. See tests/integration/README.md.
    needs_trusted_repo: bool = False
    notes: str = ""

    @property
    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def workdir(self, project: Path) -> Path:
        return REPO if self.needs_trusted_repo else project


def _noop_cleanup() -> None:
    return None


# --------------------------------------------------------------------- omp


def _wire_omp(project: Path, home: Path) -> Callable[[], None]:
    (project / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "agent-bus": {
                "command": _server_argv()[0],
                "args": _server_argv()[1:],
                "env": {"AGENT_BUS_HOME": str(home)},
            }
        }
    }, indent=2))
    return _noop_cleanup


def _run_omp(project: Path, prompt: str, *, home: Path, timeout: int = 420):
    """stdin MUST be closed: omp probes stdin during startup, and an inherited
    pipe that never sends EOF wedges it in readPipedInput before the model is
    ever called."""
    return subprocess.run(
        ["omp", "-p", "--no-session", "--no-title", "--auto-approve",
         "--model", OMP_MODEL, "--cwd", str(project),
         "--max-time", "5m", "--mode", "text", "--", prompt],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "AGENT_BUS_HOME": str(home)},
    )


# -------------------------------------------------------------------- grok


def _wire_grok(project: Path, home: Path) -> Callable[[], None]:
    """Writes into the *repo*, because that is the folder the user trusted.

    While this file exists, any grok session started in this repo also launches
    the bus MCP server. It is removed again by the returned cleanup.
    """
    cfg_dir = REPO / ".grok"
    cfg = cfg_dir / "config.toml"
    existed = cfg.exists()
    previous = cfg.read_text() if existed else None
    cfg_dir.mkdir(exist_ok=True)
    args = ", ".join(f'"{a}"' for a in _server_argv()[1:])
    cfg.write_text(
        "[mcp_servers.agent-bus]\n"
        f'command = "{_server_argv()[0]}"\n'
        f"args = [{args}]\n"
        "enabled = true\n"
    )

    def cleanup() -> None:
        if previous is not None:
            cfg.write_text(previous)
        else:
            cfg.unlink(missing_ok=True)
            if not any(cfg_dir.iterdir()):
                cfg_dir.rmdir()

    return cleanup


def _run_grok(project: Path, prompt: str, *, home: Path, timeout: int = 420):
    return subprocess.run(
        ["grok", "-p", prompt, "--always-approve"],
        cwd=str(REPO),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "AGENT_BUS_HOME": str(home)},
    )


# ------------------------------------------------------------------- codex


def _run_codex(project: Path, prompt: str, *, home: Path, timeout: int = 420):
    """No config file at all -- the server is injected with a dotted `-c`
    override, whose value is parsed as TOML. Nothing global is touched."""
    argv = _server_argv()
    args_toml = ",".join(f'"{a}"' for a in argv[1:])
    # The key must be a TOML *bare* key. `mcp_servers."agent-bus"=...` parses,
    # and `codex mcp list` then shows a server literally named `"agent-bus"`,
    # quotes included -- so its tools are unreachable, the model cannot find
    # `register`, and it improvises by shelling out and reporting success it
    # did not have. Hyphens are legal in bare keys; quotes are not wanted.
    server = (
        f'mcp_servers.agent-bus={{command="{argv[0]}",args=[{args_toml}],'
        f'env={{AGENT_BUS_HOME="{home}"}}}}'
    )
    return subprocess.run(
        ["codex", "exec", "--skip-git-repo-check", "-C", str(project),
         "-c", server, prompt],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "AGENT_BUS_HOME": str(home)},
    )


# ---------------------------------------------------------------------- pi


def _run_pi(project: Path, prompt: str, *, home: Path, timeout: int = 420):
    """pi has no MCP and no hooks. Its integration point is the shell, so the
    prompt tells it to run the CLI -- the floor case for the whole design."""
    argv = ["pi", "-p", "--approve"]
    if PI_MODEL:
        argv += ["--model", PI_MODEL]
    argv.append(prompt)
    return subprocess.run(
        argv, cwd=str(project),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "AGENT_BUS_HOME": str(home)},
    )


HARNESSES: tuple[Harness, ...] = (
    Harness("omp", "omp", "omp", "mcp", _run_omp, _wire_omp),
    Harness("grok", "grok", "grok", "mcp", _run_grok, _wire_grok,
            needs_trusted_repo=True,
            notes="needs `cd <repo> && grok` once to grant folder trust"),
    Harness("codex", "codex", "codex", "mcp", _run_codex),
    Harness("pi", "other", "pi", "shell", _run_pi,
            notes="no MCP, no hooks -- joins over its shell tool"),
)

BY_NAME = {h.name: h for h in HARNESSES}
