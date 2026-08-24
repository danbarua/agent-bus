"""Where each harness keeps its state. A leaf: imports nothing from this package.

Every one of these is overridable by an env var, because the tests need to
point a harness at a temp directory and because a peer may not use the default
home. `claude_sessions_dir` in particular existed in three places -- uds.py,
listener.py and the claude adapter -- as three identical copies, which is one
edit away from a listener publishing where nothing reads.
"""

from __future__ import annotations

import os


def claude_sessions_dir() -> str:
    return os.environ.get("AGENT_BUS_SESSIONS_DIR") or os.path.expanduser(
        "~/.claude/sessions"
    )


def grok_dir() -> str:
    return os.environ.get("AGENT_BUS_GROK_DIR") or os.path.expanduser("~/.grok")


def omp_dir() -> str:
    return os.environ.get("AGENT_BUS_OMP_DIR") or os.path.expanduser("~/.omp")

