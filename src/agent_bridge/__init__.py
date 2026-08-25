"""agent-bridge: stands in locally for peers that are only reachable remotely.

Depends on agent_bus and is never depended upon by it. A bridge is an ordinary
bus peer -- it registers, reads its inbox, acks and sends -- so everything here
is built on agent_bus's public surface, which is also the point: if this can be
written from outside the package, that surface is real.
"""

from __future__ import annotations

__all__ = ["bridge"]
