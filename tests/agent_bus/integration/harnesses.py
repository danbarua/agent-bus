"""How to drive each coding harness headlessly, and how it joins the bus.

One place for the per-vendor knowledge, so a test can be written once and
parametrised over all of them. The differences are not incidental -- they are the thing being
tested. A harness joins the bus in one of two ways:

**mcp** -- it runs `agent-bus mcp`, whose serve() calls session_start() on
startup. That registers the session as `pending-<pid>` and publishes its
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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from models import CODEX_MODEL, GROK_MODEL, OMP_MODEL, PI_MODEL

REPO = Path(__file__).resolve().parents[2]


# The command an MCP server config must launch. `uv run --project` keeps the
# test honest about which checkout it is exercising.
def _server_argv() -> list[str]:
    return ["uv", "run", "--project", str(REPO), "agent-bus", "mcp"]


# What a harness's MCP child is allowed to inherit. An allowlist by prefix, not
# a list of names: the previous version named two log variables, which worked
# and would have gone wrong again for the third.
#
# Not the whole environment, and not a denylist. These configs are written to
# `.mcp.json` on disk and onto codex's command line, so a blanket merge puts
# API keys in both -- which is what .dockerignore and the printf-only secret
# rules exist to prevent. The child is our MCP server; it needs no model keys.
INHERITED_PREFIXES = ("AGENT_BUS_", "UV_")
INHERITED_NAMES = ("PATH", "HOME", "TMPDIR", "LANG")


def _server_env(home: Path) -> dict[str, str]:
    """The environment for a harness's MCP child.

    Some harnesses hand their child a fixed environment rather than their own
    -- codex through `-c`, omp through `.mcp.json` -- so whatever is not passed
    here does not arrive. Two things went missing that way, and neither failed:

    `AGENT_BUS_LOG_*`, so codex's MCP calls were logged nowhere, in the run
    whose point is observing them. It stayed hidden because codex authenticates
    with `codex login --with-api-key` into ~/.codex/auth.json and reads nothing
    from the environment at call time.

    `UV_PROJECT_ENVIRONMENT`, which is worse. Without it `uv run --project`
    falls back to `<project>/.venv` -- and in the container that path is the
    bind mount, so the run replaced the developer's own venv with a Linux one
    and the next `uv run` on the host silently rebuilt it.

    `home` still wins over anything inherited: the test's bus is not
    negotiable.
    """
    env = {
        k: v for k, v in os.environ.items()
        if k.startswith(INHERITED_PREFIXES) or k in INHERITED_NAMES
    }
    env["AGENT_BUS_HOME"] = str(home)
    return env


@dataclass(frozen=True)
class Harness:
    name: str
    kind: str                       # what it should appear as on the bus
    binary: str
    joins_by: str                   # "mcp" | "shell"
    run: Callable[..., subprocess.CompletedProcess]
    wire: Callable[[Path, Path], Callable[[], None]] | None = None
    # grok will not *start* a project-scoped MCP server in an untrusted folder
    # -- it lists the server and then never launches it -- so a throwaway
    # tmpdir is useless, being untrusted by definition. Its test runs in the
    # repo instead, writing <repo>/.grok/config.toml and removing it after.
    # In the container the trust file is an image layer; on a host the repo
    # must already be trusted or this test cannot run.
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
                "env": _server_env(home),
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
        ["grok", "-p", prompt, "--always-approve", "-m", GROK_MODEL],
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
    env_toml = ",".join(f'{k}="{v}"' for k, v in _server_env(home).items())
    server = (
        f'mcp_servers.agent-bus={{command="{argv[0]}",args=[{args_toml}],'
        f'env={{{env_toml}}}}}'
    )
    return subprocess.run(
        ["codex", "exec", "--skip-git-repo-check", "-C", str(project),
         "-m", CODEX_MODEL, "-c", server, prompt],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "AGENT_BUS_HOME": str(home)},
    )


# ---------------------------------------------------------------------- pi


def _run_pi(project: Path, prompt: str, *, home: Path, timeout: int = 420):
    """pi has no MCP and no hooks. Its integration point is the shell, so the
    prompt tells it to run the CLI -- the floor case for the whole design."""
    return subprocess.run(
        ["pi", "-p", "--approve", "--model", PI_MODEL, prompt],
        cwd=str(project),
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
