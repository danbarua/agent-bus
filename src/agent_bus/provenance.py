"""What a running process is: which build, installed how, from where."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from importlib import metadata

DISTRIBUTION = "agent-bus-team"


@dataclass(frozen=True, slots=True)
class Provenance:
    version: str
    version_source: str
    install: str
    module_path: str
    python: str
    executable: str


def _is_editable(dist: metadata.Distribution) -> bool:
    raw = dist.read_text("direct_url.json")
    if not raw:
        return False
    try:
        return bool(json.loads(raw).get("dir_info", {}).get("editable"))
    except (ValueError, AttributeError):
        return False


def _under_site_packages(path: str) -> bool:
    parts = os.path.normpath(path).split(os.sep)
    return "site-packages" in parts or "dist-packages" in parts


def provenance() -> Provenance:
    import agent_bus

    module_path = os.path.dirname(os.path.abspath(agent_bus.__file__))
    try:
        dist = metadata.distribution(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        dist = None
    if dist is None:
        install = "source-tree"
    elif _is_editable(dist):
        install = "editable"
    elif _under_site_packages(module_path):
        install = "installed"
    else:
        install = "source-tree"
    return Provenance(
        version=agent_bus.__version__,
        version_source="distribution" if dist is not None else "source-tree",
        install=install,
        module_path=module_path,
        python=".".join(map(str, sys.version_info[:3])),
        executable=sys.executable,
    )
