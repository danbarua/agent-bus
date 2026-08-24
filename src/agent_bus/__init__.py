"""agent-bus: inter-agent messaging for Claude, Grok, OMP, Codex etc.

Stdlib only.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

# Read from the installed distribution rather than restated here. This was a
# hardcoded "0.1.0" that every surface repeated -- the MCP handshake told a
# harness it was talking to 0.1.0 while the package on disk was 0.1.4, which is
# the one number a client has to trust. hatch-vcs derives it from the git tag,
# so there is no second place to bump.
try:
    __version__ = _version("agent-bus-team")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
