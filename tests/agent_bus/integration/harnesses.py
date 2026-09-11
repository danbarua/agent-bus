"""How to drive each coding harness headlessly, and how it joins the bus.

One place for the per-vendor knowledge, so a test can be written once and
parametrised over all of them. The differences are not incidental -- they are the thing being
tested. Every harness here joins the bus the same way:

**mcp** -- it runs `agent-bus mcp`, whose serve() calls session_start() on
startup. That registers the session as `pending-<pid>` and publishes its
listener, because the MCP child does not inherit the harness's session
variables (grok's are hook-scoped; verified). The agent then calls the
`register` tool to claim a name, which *renames* that entry rather than adding
one.

Where each one's MCP config goes differs too, and none of it may touch global
config:

| harness | config | note |
|---|---|---|
| omp    | `<project>/.mcp.json`           | no gate |
| grok   | `<repo>/.grok/config.toml`      | **must** be a folder the user trusted |
| codex  | none on disk -- `-c` overrides  | dotted TOML, parsed inline |
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from models import CODEX_MODEL, GROK_MODEL, OMP_MODEL
from omp_config import driven_omp_flags, wire_omp_mcp

# `parents[3]`, because this file is three directories deep
# (tests/agent_bus/integration/). It read `parents[2]` until 2026-09-11, which
# is `tests/` -- a directory with no `pyproject.toml`, so every
# `uv run --project` in the wireups below named a project uv cannot resolve
# and the server never started. Nothing failed: the harnesses found the
# *developer's* own user-scope `agent-bus` entry instead and used that, which
# is exactly the "a test can pass without its own wireup being read" failure
# that moving this config to project scope was meant to make impossible.
# Asserted rather than commented, so moving this file fails here and not in a
# model's transcript twenty minutes later.
REPO = Path(__file__).resolve().parents[3]
assert (REPO / "pyproject.toml").is_file(), (
    f"{REPO} has no pyproject.toml, so `uv run --project` cannot start the "
    f"MCP server from it -- has this file moved?"
)


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
    # Asked after `wire`, answered by the harness's own diagnostics: why it
    # will not start our MCP server here, or None. grok declines to start a
    # repo-local server in an untrusted folder, and it declines *quietly* --
    # it keeps its shell, improvises `agent-bus` commands, and every
    # assertion about delivery still passes. A row that cannot exercise the
    # surface it is about skips, the same as a missing binary does.
    mcp_preflight: Callable[[], str | None] | None = None

    @property
    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def workdir(self, project: Path) -> Path:
        return REPO if self.needs_trusted_repo else project


def _noop_cleanup() -> None:
    return None


# --------------------------------------------------------------------- omp


def _wire_omp(project: Path, home: Path) -> Callable[[], None]:
    """Project-scoped MCP config, not user-scope.

    A developer has an `agent-bus` server in their own `~/.omp/agent/mcp.json`,
    so a run that reads user scope connects to whatever release that entry
    names and this wireup is never exercised. A project entry is encountered
    before the same-named user entry, so `agent-bus` here is the one that wins.
    """
    wire_omp_mcp(project, {
        "agent-bus": {
            "command": _server_argv()[0],
            "args": _server_argv()[1:],
            "env": _server_env(home),
        }
    })
    return _noop_cleanup


def _readable_omp(stream: str) -> str:
    """omp's NDJSON, as something a failing assertion can print.

    Rendered from `tool_execution_*` and assistant `message_end` only.
    `message_update` is the streaming form of the same text -- 147 of them
    against 22 message_ends in one measured run -- so including it prints every
    line three or four times. The first `message_end` is a `custom` role
    carrying omp's own system reminder, which is not the agent talking.
    """
    out: list[str] = []
    for line in stream.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = e.get("type")
        if kind == "tool_execution_start":
            out.append(f"[tool] {e.get('toolName')} "
                       f"{json.dumps(e.get('args') or {})[:400]}")
        elif kind == "tool_execution_end":
            said = " ".join(
                c.get("text", "") for c in (e.get("result") or {}).get("content") or []
                if isinstance(c, dict)
            )
            out.append(f"  -> {'ERROR' if e.get('isError') else 'ok'}: {said[:400]}")
        elif kind == "message_end":
            msg = e.get("message") or {}
            if msg.get("role") != "assistant":
                continue
            body = msg.get("content")
            if isinstance(body, list):
                body = " ".join(c.get("text", "") for c in body
                                if isinstance(c, dict) and c.get("type") == "text")
            if isinstance(body, str) and body.strip():
                out.append(f"[said] {body.strip()[:400]}")
    return "\n".join(out)


def _run_omp(project: Path, prompt: str, *, home: Path, timeout: int = 420):
    """stdin MUST be closed: omp probes stdin during startup, and an inherited
    pipe that never sends EOF wedges it in readPipedInput before the model is
    ever called.

    `--mode json` rather than text, and the stream is rendered before it is
    returned. Text mode emits nothing until the run ends, so a run killed at
    its timeout hands back an empty transcript -- exactly when the failure needs
    reading. Nothing asserts on this output; it only ever appears in a failure
    message, which is why rendering in place beats making every call site parse.
    """
    r = subprocess.run(
        ["omp", "-p", "--no-session", "--no-title", "--auto-approve",
         *driven_omp_flags(project),
         "--model", OMP_MODEL, "--cwd", str(project),
         "--max-time", "5m", "--mode", "json", "--", prompt],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "AGENT_BUS_HOME": str(home)},
    )
    # Falls back to the raw stream: a render that comes back empty means the
    # shape changed, and an unreadable transcript beats no transcript.
    return subprocess.CompletedProcess(
        r.args, r.returncode, _readable_omp(r.stdout) or r.stdout, r.stderr)


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


def _grok_mcp_blocked() -> str | None:
    """grok's own answer to "will you start this server?", asked before a run.

    `grok mcp doctor --json` is the machine-readable form of what the TUI
    prints, and on an untrusted folder it says exactly this:

        {"name": "agent-bus", "healthy": false, "checks": [{
          "label": "folder untrusted", "passed": false,
          "detail": "repo-local (project-scoped) server not started ...",
          "hint": "re-run with --trust to allow repo-local servers"}]}

    Asked rather than assumed, and never fixed from here: granting trust is a
    developer's decision about their own machine, and a test that granted it
    silently would be changing the thing it is measuring.

    **Its own environment, deliberately.** The doctor does not just read
    config -- it starts each stdio server and handshakes with it. Run with
    this test's `AGENT_BUS_LOG_FILE` and `AGENT_BUS_HOME` inherited, that
    handshake writes `mcp server started` and `initialize` into the log the
    assertions then read, and registers a `pending-<pid>` entry in the test's
    own roster. So: log variables dropped, home pointed at a throwaway.

    A doctor whose output this cannot parse returns None -- the run then
    speaks for itself rather than being skipped on a shape change.
    """
    scratch = tempfile.mkdtemp(prefix="grok-doctor-")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_BUS_LOG")}
    env["AGENT_BUS_HOME"] = scratch
    try:
        r = subprocess.run(["grok", "mcp", "doctor", "--json"], cwd=str(REPO),
                           capture_output=True, text=True, timeout=120, env=env)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    try:
        servers = json.loads(r.stdout)["servers"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    for server in servers:
        if server.get("name") != "agent-bus":
            continue
        if server.get("healthy"):
            return None
        failed = [c for c in server.get("checks") or [] if not c.get("passed")]
        return "; ".join(
            " ".join(part for part in (c.get("label"), c.get("detail"), c.get("hint"))
                     if part)
            for c in failed
        ) or "grok reports the agent-bus server unhealthy, with no failing check"
    return "grok's doctor does not list an agent-bus server at all"


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


HARNESSES: tuple[Harness, ...] = (
    Harness("omp", "omp", "omp", "mcp", _run_omp, _wire_omp),
    Harness("grok", "grok", "grok", "mcp", _run_grok, _wire_grok,
            needs_trusted_repo=True,
            notes="needs `cd <repo> && grok` once to grant folder trust",
            mcp_preflight=_grok_mcp_blocked),
    Harness("codex", "codex", "codex", "mcp", _run_codex),
)

BY_NAME = {h.name: h for h in HARNESSES}
