"""The frozen tool surface. Four tools, and nothing else, forever.

Frozen is the point rather than a caution. The predecessor's connectors were
provoked into failure by shipping changes -- OpenAI's verification and WAF-like
filtering are opaque and undebuggable -- so the contract stops moving and the
bus iterates behind it. A change here is a change every registered client may
have cached.

`test_contract.py` snapshots these structures, so an edit shows up as a loud
diff in review rather than as a connector that stopped working a week later.

Two shapes are lessons rather than taste, both from the predecessor:

**Sender identity is required and never defaulted.** Its `write` inferred the
sender, and the ambiguity attributed a message to the wrong party once. A
caller that cannot say who it is has nothing useful to say.

**No optional safety parameter that falls back to unsafe.** Its `archive`
defaulted to consuming *everything*, addressed or not, so "I forgot to pass it"
and "the feature is not deployed here" were indistinguishable after the fact --
and a message addressed to another session was destroyed. `ack` therefore takes
an explicit list of ids and has no "all" mode.
"""

from __future__ import annotations

import re
from typing import Any

# MCP requires this of tool names, and ChatGPT enforces it. A name outside it is
# not a validation error at call time -- the tool is simply never offered.
TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Matches the local bus. "the bus adopts the narrowest constraint of any
# supported harness" cuts both ways: a desktop mailbox is the one genuinely
# unread-accumulating kind, so the limits are enforced here too rather than
# trusted to the sender.
MAX_TEXT = 32_768
MAX_UNREAD = 50

TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "list-agents",
        "description": (
            "Who is on the team's bus right now. Call this before write: it "
            "answers whether the bridge is connected at all, and whether the "
            "name you are about to send to exists."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read",
        "description": "Messages waiting for you. Does not consume them; use ack.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "unread_only": {
                    "type": "boolean",
                    "description": "Default true. False also returns acked "
                                   "messages still within their lifetime.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "ack",
        "description": (
            "Mark specific messages read, by id. There is deliberately no "
            "'everything' mode."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Message ids from read. Required, and never defaulted to all.",
                }
            },
            "required": ["ids"],
        },
    },
    {
        "name": "write",
        "description": (
            "Send a message to one agent on the team's bus. Check the name with "
            "list-agents first: an unroutable name fails here rather than being "
            "guessed at."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string",
                       "description": "Agent name, exactly as list-agents reports it."},
                "text": {"type": "string",
                         "description": f"Body. At most {MAX_TEXT} characters."},
                "summary": {"type": "string",
                            "description": "One line, shown in the recipient's notification."},
                "from": {"type": "string",
                         "description": "Who is writing. Required; never inferred."},
            },
            "required": ["to", "text", "from"],
        },
    },
)

TOOL_NAMES = tuple(t["name"] for t in TOOLS)
