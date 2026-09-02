"""agent-bridge: stands in locally for peers that are only reachable remotely.

Depends on agent_bus and is never depended upon by it. A bridge is an ordinary
bus peer -- it registers, reads its inbox, acks and sends -- so everything here
is built on agent_bus's public surface, which is also the point: if this can be
written from outside the package, that surface is real.
"""

from __future__ import annotations

# No `__all__`. It listed `bridge`, and that worked -- `from package import *`
# imports submodules named in `__all__`, so nothing was broken; the checker
# flagged it because the name is not statically present, which is true.
#
# Removed rather than satisfied. Satisfying it means importing the submodule
# here, which the lazy-import policy in pyproject.toml exists to avoid, and
# nothing in this repository does `import *` on this package. A promise no
# caller uses and no reader can verify is worth less than the line it costs.
