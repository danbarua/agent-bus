"""What agent-bus can do, independent of who is asking.

The CLI and the MCP server had grown parallel implementations of the same
seven operations. They did not stay parallel: `list_agents(kind="ALL")`
returned everything on one surface and nothing on the other, the message
serializer existed three times (twice by hand, next to the canonical
`protocol.message_to_json` that only store used), and `self --json` described
an entry with seven keys while the MCP tool described it with eleven.

So the operations live here, and each edge does only its own job: argparse and
human-readable text on one side, JSON-RPC envelopes and tool schemas on the
other. Neither decides what a command means.

Commands return plain JSON-serializable dicts, never dataclasses -- both edges
serialize, and a dict is the shape they both need. Failures raise; the edges
already translate exceptions into their own idiom (stderr plus an exit code,
or a JSON-RPC error object), so a bespoke exception type would only be a third
thing to keep in sync.

Not everything moved. `listen`, `watch`, `send-codex` and `codex-list` are
already thin wrappers over uds.py, watch.py and codex_client.py, and are not
shared with the MCP server; `hook` shapes a hook-protocol response that only
the CLI speaks. Moving those would add a layer without removing a duplicate.
"""

from __future__ import annotations

from .agents import list_agents, register, self_info, set_status
from .messages import ack, inbox, send

__all__ = [
    "ack",
    "inbox",
    "list_agents",
    "register",
    "self_info",
    "send",
    "set_status",
]
