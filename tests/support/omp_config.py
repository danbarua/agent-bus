"""How a test-driven omp is kept off the developer's own omp configuration.

Three things leak from a developer's `~/.omp` into a driven run, and each is
closed differently.

**The MCP wireup** goes in the project, at `<project>/.omp/mcp.json`. A
developer working on this repository has an `agent-bus` server in their own
user-scope `~/.omp/agent/mcp.json`, and a run that reads it connects to
whatever release that entry names -- so the test's own wireup need never be
read for the test to pass. Project scope is encountered before the same-named
user entry, so this one wins under the name `agent-bus`.

**Memory and autolearn** go off through a `--config` overlay. Both keys are
needed, measured on the same prompt with the overlay and without:
`memory.backend: off` alone takes away `recall` and `learn` but leaves
`manage_skill`, which writes into `managed-skills` regardless of backend.

**Thinking** is pinned by flag. A developer configured at `high` pays for
reasoning on a prompt that is three imperative sentences; one measured run
took 311s and hit its own `--max-time`.

`PI_CONFIG_DIR` would replace all three by moving the config root, and cannot
be used here, for two measured reasons. It is joined onto `$HOME` rather than
taken as given -- `PI_CONFIG_DIR=/var/folders/…/omproot` resolves to
`/Users/<me>/var/folders/…/omproot`, so nothing written to the real path is
ever read, and the wireup silently goes missing rather than failing. And the
credential store lives in that root: with it moved, a run authenticates only
from the environment, and `ANTHROPIC_API_KEY` is not in a developer's shell --
only inside an omp session, which is not where these tests are launched from.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Thinking inherits from config, and a developer's is not the test's to spend.
DRIVEN_OMP_FLAGS = ("--thinking", "low")


def wire_omp_mcp(project: Path | str, servers: dict[str, object],
                 settings: dict[str, object] | None = None) -> Path:
    """Write project-scoped omp config: the MCP `servers`, and any `settings`.

    Returns the `.omp` directory, which is inside the project a test made and
    throws away -- nothing here touches the developer's own configuration.
    """
    omp_dir = Path(project) / ".omp"
    omp_dir.mkdir(parents=True, exist_ok=True)
    (omp_dir / "mcp.json").write_text(json.dumps({"mcpServers": servers}, indent=2))
    if settings is not None:
        (omp_dir / "settings.json").write_text(json.dumps(settings, indent=2))
    return omp_dir


def driven_omp_flags(project: Path | str) -> list[str]:
    """omp flags for a driven run, with the overlay written under `project`.

    Beside the project's other omp config rather than in a directory of its
    own: `.e2e/` is pruned of empty directories after a run so that what is
    left behind is evidence, and one more sibling holding one generated file
    is noise in exactly that reading.

    JSON, which omp's overlay loader accepts as the YAML subset it is -- and a
    test that hand-writes YAML owns an escaping bug for nothing. The loader is
    strict: a missing or unparseable overlay is a hard error, not a warning, so
    this having been written is checked by every run that uses it.
    """
    omp_dir = Path(project) / ".omp"
    omp_dir.mkdir(parents=True, exist_ok=True)
    overlay = omp_dir / "config.json"
    overlay.write_text(json.dumps({"memory": {"backend": "off"},
                                   "autolearn": {"enabled": False}}))
    return ["--config", str(overlay), *DRIVEN_OMP_FLAGS]
